"""The routing engine: decide, apply, record — and fail open at every step.

Called from the ``llm_request`` middleware with the request kwargs Hermes is about to send.
The contract is narrow on purpose:

* the payload is returned **unchanged** unless a confident decision was obtained;
* nothing here raises — the host already isolates middleware exceptions, but the router also
  enforces its own budget and swallows its own errors so a slow Jev cannot stall a turn;
* one decision per user turn, replayed for the rest of that turn's tool loop.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

from .audit import AuditLog
from .catalog import ModelCatalog
from .client import (
    REASON_DISABLED,
    REASON_EXCEPTION,
    REASON_LOW_CONFIDENCE,
    REASON_NO_API_KEY,
    REASON_SKIPPED_API_MODE,
    REASON_SKIPPED_MODEL,
    REASON_SKIPPED_NO_MESSAGE,
    REASON_SKIPPED_PROVIDER,
    REASON_SKIPPED_UNKNOWN_MODEL,
    REASON_SURFACE_DISABLED,
    REASON_USER_DATA,
    Decision,
    JevClient,
    _cap_effort,
)
from .config import (
    ROUTED_API_MODES,
    ROUTED_PROVIDER,
    ROUTED_PROVIDER_ALIASES,
    EffectiveSettings,
    Settings,
    api_key,
    route_catalog,
)
from .effort import resolve_effort
from .memo import Memo, TurnMemo
from .state import last_user_message

logger = logging.getLogger(__name__)

#: Where the routing decision puts the effort, per payload shape (see ``config.ROUTED_API_MODES``).
#:
#: ``chat_completions`` takes the level as the top-level ``reasoning_effort``. The Codex Responses
#: route (``codex_responses``) takes it nested under ``reasoning``: the transport decodes the
#: params-level ``reasoning_config`` into ``reasoning: {"effort": ...}`` while it *builds* the
#: body (``agent/transports/codex.py::_reasoning_fields``), and the middleware runs after that
#: build, so the payload here is already the Responses body. A top-level ``reasoning_effort`` in
#: it is not a Responses field at all — the SDK call raises ``TypeError: unexpected keyword
#: argument 'reasoning_effort'`` before a request is sent.
EFFORT_WIRE_KEY = "reasoning_effort"
RESPONSES_API_MODE = "codex_responses"
RESPONSES_REASONING_KEY = "reasoning"


def conversation_messages(payload: Any) -> List[Dict[str, Any]]:
    """The conversation a payload carries, in the chat shape the state builder reads.

    ``chat_completions`` hands the middleware ``messages``. A Responses payload has no
    ``messages`` at all: the conversation is ``input``, a list of items where a turn is
    ``{"type": "message", "role": ..., "content": [...]}`` and everything else
    (``function_call``, ``function_call_output``, ``reasoning``) is a tool or reasoning item with
    no role. Only role-bearing items are conversation — the same set ``recent_context`` keeps on
    the chat shape, so tool payloads stay out of the routing state on both wires.
    """
    if not isinstance(payload, dict):
        return []
    messages = payload.get("messages")
    if isinstance(messages, list):
        return messages
    items = payload.get("input")
    if not isinstance(items, list):
        return []
    conversation: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if not role:
            continue
        conversation.append({"role": role, "content": item.get("content")})
    return conversation


def wire_model_for(provider: str, effective: EffectiveSettings, model_id: str) -> str:
    """The id to put in the provider's ``model`` field for the routed choice.

    Read from the route's **built-in catalog** first. A picker alias (the Codex ``-900k``
    variants) is stripped by the transport while it *builds* the body — before the middleware
    this router runs in — so writing the alias back would send an id the backend does not serve.
    The operator's ``grids.<provider>`` is the *choice* set, not the record of what each id means
    on the wire: a grid parsed from config carries descriptions only, so it cannot answer this
    question and must not be trusted to.
    """
    for entry in route_catalog(provider) or ():
        if entry.model_id == model_id:
            return entry.wire_model
    resolved = effective.entry_for(model_id)
    return resolved.wire_model if resolved is not None else model_id


class Router:
    """Stateful routing engine: one instance per loaded plugin."""

    def __init__(self, get_settings, get_state=None, *, client_factory=None, catalog=None) -> None:
        self._get_settings = get_settings
        self._get_state = get_state
        self._client_factory = client_factory or (lambda settings: JevClient(settings))
        self._memo = TurnMemo()
        self._audit: Optional[AuditLog] = None
        self._audit_key: Optional[tuple] = None
        self._client = None
        self._client_key: Optional[tuple] = None
        self._catalog = catalog if catalog is not None else ModelCatalog()

    # -- wiring ------------------------------------------------------------------

    def _data_dir(self) -> Optional[str]:
        """The plugin data directory, when the host exposed one."""
        try:
            state = self._get_state() if callable(self._get_state) else None
            data_dir = getattr(state, "data_dir", None)
            return str(data_dir) if data_dir else None
        except Exception:  # noqa: BLE001 - auditing is optional, never load-bearing
            return None

    def audit(self, settings: Settings) -> AuditLog:
        key = (self._data_dir(), bool(settings.audit_enabled))
        if self._audit is None or self._audit_key != key:
            self._audit = AuditLog(key[0], enabled=key[1])
            self._audit_key = key
        return self._audit

    def client(self, settings: Settings):
        key = (
            settings.decider,
            settings.endpoint,
            settings.base_url,
            settings.jev_model,
            settings.decider_model,
            settings.timeout_s,
        )
        if self._client is None or self._client_key != key:
            self._client = self._client_factory(settings)
            self._client_key = key
        return self._client

    # -- the middleware body -----------------------------------------------------

    def on_llm_request(
        self,
        request: Any = None,
        original_request: Any = None,
        *,
        turn_id: str = "",
        session_id: str = "",
        task_id: str = "",
        platform: str = "",
        model: str = "",
        provider: str = "",
        base_url: str = "",
        api_mode: str = "",
        api_call_count: Any = 0,
        api_request_id: str = "",
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        """``llm_request`` middleware entry point.

        Returns ``None`` to leave the request untouched, or ``{"request": {...}}`` with the
        rewritten provider kwargs.
        """
        try:
            settings = self._get_settings()
        except Exception as exc:  # noqa: BLE001
            logger.debug("jev-effort-router: settings unavailable (%s); leaving request untouched", exc)
            return None

        where = _Where(
            turn_id=turn_id,
            session_id=session_id,
            api_request_id=api_request_id,
            platform=platform,
            provider=provider,
            model=model,
        )

        if not settings.enabled:
            self._record_skip(settings, REASON_DISABLED, where)
            return None

        # -- skip gates: anything the router does not own goes out untouched ---------
        provider_key = (provider or "").strip().lower()
        if not settings.is_provider_routable(provider_key):
            self._record_skip(settings, REASON_SKIPPED_PROVIDER, where)
            return None
        if api_mode and api_mode not in ROUTED_API_MODES:
            self._record_skip(settings, REASON_SKIPPED_API_MODE, where, extra={"api_mode": api_mode})
            return None

        # -- per-surface policy: resolved once per request ---------------------------
        # Absent, null or an unlisted platform leaves ``policy`` None and ``effective`` equal
        # to the global settings, so the gates below behave exactly as before the key existed.
        policy = settings.policy_for(platform)
        if policy is not None and policy.enabled is False:
            self._record_skip(settings, REASON_SURFACE_DISABLED, where)
            return None
        effective = settings.effective(policy, provider=provider)

        # A surface's grid is a structural cap: a configured model outside it degrades to a
        # skip that keeps that configured model on the wire, rather than being re-pinned.
        if effective.entry_for(model) is None:
            self._record_skip(settings, REASON_SKIPPED_MODEL, where)
            return None
        if not isinstance(request, dict) or not isinstance(original_request, dict):
            self._record_skip(settings, REASON_USER_DATA, where)
            return None

        try:
            memo, replayed = self._lookup(settings, turn_id, session_id)

            decision: Optional[Decision] = None
            if memo is not None:
                # Provenance guard: a decision made for another provider must not be replayed to this one
                memo_prov = getattr(memo, "provider", "")
                if memo_prov and provider and memo_prov != provider:
                    logger.info(
                        "jev-effort-router: memo decided for provider %r is incompatible with active provider %r; failing open",
                        memo_prov,
                        provider,
                    )
                    self._record_skip(settings, REASON_SKIPPED_PROVIDER, where)
                    return None

                # Session guard: a decision made for another session must not be replayed to this one
                memo_session = getattr(memo, "session_id", "")
                if memo_session and session_id and memo_session != session_id:
                    logger.info(
                        "jev-effort-router: memo decided for session %r is incompatible with active session %r; failing open",
                        memo_session,
                        session_id,
                    )
                    self._record_skip(settings, REASON_SKIPPED_PROVIDER, where)
                    return None

                # Decider guard: memo decided with a different decider must not be reused
                memo_dec = getattr(memo, "decider", "")
                if memo_dec and memo_dec != settings.decider:
                    logger.info(
                        "jev-effort-router: memo decided with decider %r is incompatible with active decider %r; failing open",
                        memo_dec,
                        settings.decider,
                    )
                    self._record_skip(settings, REASON_SKIPPED_PROVIDER, where)
                    return None

                # Decider configuration guard: endpoint, decider model, fallback model, and key env
                memo_endpoint = getattr(memo, "decider_endpoint", "")
                curr_endpoint = settings.base_url_for(settings.decider)
                if memo_endpoint and curr_endpoint and memo_endpoint != curr_endpoint:
                    logger.info(
                        "jev-effort-router: memo decider endpoint %r != active %r; failing open",
                        memo_endpoint,
                        curr_endpoint,
                    )
                    self._record_skip(settings, REASON_SKIPPED_PROVIDER, where)
                    return None

                memo_model = getattr(memo, "decider_model", "")
                curr_model = settings.decider_model_for(settings.decider)
                if memo_model and curr_model and memo_model != curr_model:
                    logger.info(
                        "jev-effort-router: memo decider model %r != active %r; failing open",
                        memo_model,
                        curr_model,
                    )
                    self._record_skip(settings, REASON_SKIPPED_PROVIDER, where)
                    return None

                memo_fallback = getattr(memo, "decider_fallback_model", "")
                curr_fallback = (settings.console_jev_fallback_model or "").strip() if settings.decider == "jev_console" else ""
                if memo_fallback != curr_fallback:
                    logger.info(
                        "jev-effort-router: memo fallback model %r != active %r; failing open",
                        memo_fallback,
                        curr_fallback,
                    )
                    self._record_skip(settings, REASON_SKIPPED_PROVIDER, where)
                    return None

                memo_key_env = getattr(memo, "decider_key_env", "")
                curr_key_env = settings.api_key_env_for(settings.decider)
                if memo_key_env and curr_key_env and memo_key_env != curr_key_env:
                    logger.info(
                        "jev-effort-router: memo key env %r != active %r; failing open",
                        memo_key_env,
                        curr_key_env,
                    )
                    self._record_skip(settings, REASON_SKIPPED_PROVIDER, where)
                    return None

                if not api_key(curr_key_env):
                    logger.info(
                        "jev-effort-router: active decider key env %r has no key; failing open",
                        curr_key_env,
                    )
                    self._record_skip(settings, REASON_NO_API_KEY, where)
                    return None

                # Policy confidence threshold guard: replayed confidence must meet active threshold
                has_effort_conf = getattr(memo, "has_effort_confidence", None)
                if has_effort_conf is None:
                    has_effort_conf = bool(memo.effort_confidence > 0.0 or memo.effort_requested is not None)

                effort_failed = has_effort_conf and (memo.effort_confidence < effective.confidence_threshold)
                if (
                    memo.model_confidence < effective.confidence_threshold
                    or effort_failed
                ) and REASON_LOW_CONFIDENCE not in memo.fallback_reasons:
                    logger.info(
                        "jev-effort-router: memo confidence (model=%.2f, effort=%.2f) is below active threshold %.2f; failing open",
                        memo.model_confidence,
                        memo.effort_confidence,
                        effective.confidence_threshold,
                    )
                    self._record_skip(settings, REASON_LOW_CONFIDENCE, where)
                    return None

                # Grid guard: the replayed model must exist in the active provider's grid
                if effective.entry_for(memo.model) is None:
                    logger.info(
                        "jev-effort-router: memo model %r is not in grid for provider %r; failing open",
                        memo.model,
                        provider,
                    )
                    self._record_skip(settings, REASON_SKIPPED_MODEL, where)
                    return None

                # Grid signature guard: criteria and options must match active grid
                curr_grid_sig = tuple((e.model_id, e.description) for e in effective.grid)
                memo_grid_sig = getattr(memo, "grid_signature", ())
                if memo_grid_sig and memo_grid_sig != curr_grid_sig:
                    logger.info(
                        "jev-effort-router: memo grid criteria signature does not match active grid; failing open",
                    )
                    self._record_skip(settings, REASON_SKIPPED_MODEL, where)
                    return None

                # Effort map guard: re-resolve effort against the active provider's map and respect active max_effort cap
                req_effort = memo.effort_requested or memo.effort
                if req_effort is not None and effective.max_effort:
                    req_effort = _cap_effort(req_effort, effective.max_effort)

                re_effort = resolve_effort(
                    memo.model,
                    req_effort,
                    provider=provider,
                    effort_maps=settings.effort_maps,
                )
                decision = _decision_from_memo(memo, effort=re_effort)
                if req_effort != memo.effort_requested:
                    decision = replace(decision, effort_requested=req_effort)
            else:
                fetched = self._decide(settings, effective, request, where)
                if fetched is None:
                    return None
                decision = fetched
                # A decision naming a model the provider does not have is worse than no decision:
                # asking for it turns a degraded turn into a dead one (HTTP 404, no response).
                # Leave the request exactly as the operator configured it.
                if not self._provider_has(decision.model, provider=provider, decider=settings.decider):
                    logger.warning(
                        "jev-effort-router: Jev chose %r, which is not in the provider's catalog; "
                        "leaving the turn on the configured model",
                        decision.model,
                    )
                    self._record_skip(settings, REASON_SKIPPED_UNKNOWN_MODEL, where,
                                      extra={"chosen_model": decision.model})
                    return None
                memo = Memo(
                    model=decision.model,
                    effort=decision.effort,
                    effort_requested=decision.effort_requested,
                    model_confidence=decision.model_confidence,
                    effort_confidence=decision.effort_confidence,
                    choice=decision.model_choice,
                    fallback_reasons=decision.fallback_reasons,
                    decider=decision.decider,
                    provider=provider,
                    session_id=session_id,
                    decider_endpoint=settings.base_url_for(decision.decider),
                    decider_model=settings.decider_model_for(decision.decider),
                    decider_model_used=decision.decider_model,
                    decider_fallback_reason=decision.decider_fallback_reason,
                    confidence_threshold=effective.confidence_threshold,
                    max_effort=effective.max_effort,
                    decider_fallback_model=(settings.console_jev_fallback_model or "").strip() if decision.decider == "jev_console" else "",
                    decider_key_env=settings.api_key_env_for(decision.decider),
                    grid_signature=tuple((e.model_id, e.description) for e in effective.grid),
                    has_effort_confidence=getattr(decision, "has_effort_confidence", False),
                )
                # Storing on "no memo found" rather than on an api_call_count test is deliberate:
                # Hermes passes the *incremented* counter, so the first provider call of a turn
                # arrives with api_call_count == 1 (see agent/turn_iteration_prep.py). Keying the
                # store off that value silently never stored anything, and every call inside a
                # turn's tool loop re-decided. Absence of a memo is the reliable "first call of
                # this turn" signal.
                if settings.route_per_turn:
                    self._memo.put_turn(turn_id, memo)
                else:
                    self._memo.put_session(session_id, memo)

            assert decision is not None
            routed = self._apply(
                original_request,
                decision,
                api_mode=api_mode,
                wire_model=wire_model_for(provider, effective, decision.model),
            )
            decision = self._applied_decision(decision, routed, api_mode)
            self._record_route(
                settings,
                decision,
                where,
                api_call_count=api_call_count,
                replayed=replayed,
                wire_model=routed.get("model") or decision.model,
            )
            return {"request": routed, "source": "jev-effort-router", "reason": decision.model}
        except Exception as exc:  # noqa: BLE001 - a router must never break a turn
            logger.warning(
                "jev-effort-router: routing failed (%s: %s); leaving request untouched",
                type(exc).__name__,
                exc,
            )
            self._record_skip(settings, REASON_EXCEPTION, where)
            return None

    # -- decision plumbing -------------------------------------------------------

    def _provider_has(self, model_id: str, provider: str = "", decider: str = "") -> bool:
        """Whether the chosen model is safe to put on the wire, according to the provider catalog.

        Answers ``True`` whenever the catalog has nothing to say — an absent cache, an unreadable
        file, a non-Ollama provider, or a host that exposes none of it all mean "no evidence",
        and the router must not start refusing decisions on missing evidence. Only a catalog that
        lists models and does not contain this one is a No. An opt-in OpenCode route pins its
        own catalog, so ``decider`` is what makes an opt-in route's cap apply.
        """
        try:
            known = self._catalog.is_known(model_id, provider=provider, decider=decider)
        except Exception as exc:  # noqa: BLE001 - a catalog probe never blocks a turn
            logger.debug("jev-effort-router: catalog check failed (%s: %s)", type(exc).__name__, exc)
            return True
        return known is not False

    def _lookup(
        self, settings: Settings, turn_id: str, session_id: str
    ) -> Tuple[Optional[Memo], bool]:
        """The memoized decision for this request, and whether it is a replay.

        ``route_per_turn: true`` (the default) memoizes per turn, so the first request of a
        turn decides and the rest of the turn's tool loop replays it. ``false`` memoizes per
        session instead, which routes once per conversation.
        """
        if settings.route_per_turn:
            memo = self._memo.get_turn(turn_id, session_id=session_id)
            return memo, memo is not None
        memo = self._memo.get_session(session_id)
        if memo is not None:
            return memo, True
        # No session decision yet, but this turn may already have decided one: don't re-decide
        # inside the same turn's tool loop.
        memo = self._memo.get_turn(turn_id, session_id=session_id)
        return memo, memo is not None

    def _decide(
        self, settings: Settings, effective: EffectiveSettings, request: Dict[str, Any], where: "_Where"
    ) -> Optional[Decision]:
        messages = conversation_messages(request)
        if not messages or not last_user_message(messages).strip():
            self._record_skip(settings, REASON_SKIPPED_NO_MESSAGE, where)
            return None

        try:
            decision, reason = self.client(settings).decide(
                messages,
                effective,
                platform=where.platform,
                provider=where.provider,
            )
        except Exception as exc:  # noqa: BLE001 - belt and braces around the client
            logger.warning("jev-effort-router: decision call failed (%s); leaving request untouched", exc)
            self._record_skip(settings, REASON_EXCEPTION, where)
            return None

        if decision is None:
            if reason == REASON_NO_API_KEY:
                key_env = settings.api_key_env_for(settings.decider)
                logger.info("jev-effort-router: %s is not set; turns keep the configured model", key_env)
            self._record_skip(settings, reason or REASON_EXCEPTION, where)
            return None
        return decision

    def _apply(
        self,
        original_request: Dict[str, Any],
        decision: Decision,
        *,
        api_mode: str = "",
        wire_model: str = "",
    ) -> Dict[str, Any]:
        """Build the rewritten request from the pre-middleware payload.

        Working from ``original_request`` keeps this router idempotent when more than one
        middleware rewrites the same call, and makes "the request is unchanged" literally true
        on the no-route path.

        On ``chat_completions`` only the top-level ``reasoning_effort`` is written.
        ``reasoning_config`` is a *params-level* input that the provider profile consumes to
        derive that top-level field (``agent/transports/chat_completions.py::
        _build_kwargs_from_profile``, then ``plugins/model-providers/ollama-cloud/__init__.py``
        which returns only ``reasoning_effort``). It is never a wire kwarg, and leaving it in the
        final payload makes Ollama reject the entire call with "Completions.create() got an
        unexpected keyword argument 'reasoning_config'" — a hard turn failure, not a degraded
        route.

        On ``codex_responses`` the field is nested instead — see :meth:`_apply_responses_reasoning`.
        """
        routed = dict(original_request)
        routed["model"] = wire_model or decision.model
        if api_mode == RESPONSES_API_MODE:
            return self._apply_responses_reasoning(routed, decision.effort)
        if decision.effort:
            routed[EFFORT_WIRE_KEY] = decision.effort
        else:
            routed.pop(EFFORT_WIRE_KEY, None)
        return routed

    @staticmethod
    def _apply_responses_reasoning(routed: Dict[str, Any], effort: Optional[str]) -> Dict[str, Any]:
        """Rewrite ``reasoning.effort`` on an already-built Responses body.

        The transport decides whether this route takes a reasoning field at all, and emits the
        object from the session's ``reasoning_config``; a route it judged field-less carries no
        ``reasoning`` key, and adding one is exactly the 400 class that guard exists to avoid. So
        the level lands inside an existing object only, and an omitted level removes just the
        ``effort`` — ``summary``/``include`` are the transport's, not this router's.
        """
        reasoning = routed.get(RESPONSES_REASONING_KEY)
        if not isinstance(reasoning, dict):
            return routed
        updated = dict(reasoning)
        if effort:
            updated["effort"] = effort
        else:
            updated.pop("effort", None)
        routed[RESPONSES_REASONING_KEY] = updated
        return routed

    @staticmethod
    def _applied_decision(decision: Decision, routed: Dict[str, Any], api_mode: str) -> Decision:
        """The decision as it actually reached the payload.

        A Responses route the transport judged as taking no reasoning field carries no
        ``reasoning`` object, and this router will not invent one. The audit then reports the
        level as *not* sent (``effort: null``) while ``effort_requested`` keeps Jev's choice — a
        null effort is a statement about the wire, never about what the model executed.
        """
        if api_mode != RESPONSES_API_MODE or not decision.effort:
            return decision
        reasoning = routed.get(RESPONSES_REASONING_KEY)
        if isinstance(reasoning, dict) and reasoning.get("effort") == decision.effort:
            return decision
        return replace(decision, effort=None)

    # -- audit -------------------------------------------------------------------

    def _record_route(
        self,
        settings: Settings,
        decision: Decision,
        where: "_Where",
        *,
        api_call_count: Any = 0,
        replayed: bool = False,
        wire_model: str = "",
    ) -> None:
        # An audit record records what was measured and says ``null`` for what was not. On a
        # replayed turn the distributions, alternatives and effort-choice string were not stored,
        # so the record must present them as absent (`None`) — never as a value copied from a
        # neighbouring field. The confidences stay: they were measured as a pair and re-emitted
        # as that pair.
        #
        # ``model`` is the id the *decision* named and ``wire_model`` is the id that went in the
        # provider's ``model`` field. They differ on the Codex route's ``-900k`` picker aliases,
        # which the transport strips before the wire: a record that only carried ``model`` would
        # claim a route that did not happen.
        record: Dict[str, Any] = {
            "event": "route",
            "model": decision.model,
            "wire_model": wire_model or decision.model,
            "effort": decision.effort,
            "effort_requested": decision.effort_requested,
            "model_choice": decision.model_choice,
            # The route the decision was applied to. Skip records already name it; a route record
            # that omitted it could not be told apart from one written for another provider
            # (``opencode-go`` and ``commandcode`` now both send ``reasoning_effort``).
            "provider": where.provider,
            # ``effort_choice`` is a string on a fresh decision; a replay carries "" meaning
            # "not measured at replay", which the audit surfaces as null.
            "effort_choice": decision.effort_choice if decision.effort_choice else None,
            "model_confidence": round(decision.model_confidence, 4),
            "effort_confidence": round(decision.effort_confidence, 4),
            "model_probabilities": decision.model_probabilities if decision.model_probabilities else None,
            "effort_probabilities": decision.effort_probabilities if decision.effort_probabilities else None,
            "alternatives": list(decision.alternatives) if decision.alternatives else None,
            "latency_ms": decision.latency_ms,
            "fallback_reasons": list(decision.fallback_reasons),
            # The decider model that actually produced this decision. Reading it from the decision
            # (not from ``settings.jev_model``, the legacy OpenRouter id) is what keeps a
            # ``decider: jev_console`` record from naming a model that was never consulted;
            # ``decider_fallback_reason`` names why the Console fallback answered when it did.
            "jev_model": decision.decider_model or settings.decider_model_for(decision.decider),
            "decider_fallback_reason": decision.decider_fallback_reason,
            "decider": getattr(decision, "decider", settings.decider),
            "replayed": bool(replayed),
        }
        record.update(where.fields())
        record.update({key: value for key, value in {"api_call_count": api_call_count}.items() if value not in (None, "")})
        self.audit(settings).append(record)
        if decision.degraded:
            logger.info(
                "jev-effort-router: routed %s (effort %s) with degradation %s",
                decision.model,
                decision.effort,
                ",".join(decision.fallback_reasons),
            )
        else:
            logger.debug(
                "jev-effort-router: routed %s (effort %s) in %d ms",
                decision.model,
                decision.effort,
                decision.latency_ms,
            )

    def _record_skip(self, settings: Settings, reason: str, where: "_Where", *, extra: Optional[Dict[str, Any]] = None) -> None:
        if not settings.log_skips:
            return
        record: Dict[str, Any] = {
            "event": "skip",
            "reason": reason,
            "provider": where.provider,
            "configured_model": where.model,
        }
        record.update({key: value for key, value in (extra or {}).items() if value not in (None, "")})
        record.update(where.fields())
        self.audit(settings).append(record)

    # -- read-only surfaces used by the tools and commands -----------------------

    def tail(self, settings: Settings, limit: int = 10):
        return self.audit(settings).tail(limit)

    def count(self, settings: Settings) -> int:
        return self.audit(settings).count()

    def grid_report(self, settings: Settings, provider: Optional[str] = None) -> List[Dict[str, Any]]:
        """The grid annotated with what the provider catalog says about each entry.

        Empty when there is no catalog evidence, so a caller can tell "nothing to report" from
        "every entry is fine".
        """
        target_provider = (
            provider
            if provider is not None
            else (settings.routable_providers[0] if settings.routable_providers else "")
        )
        report: List[Dict[str, Any]] = []
        for entry in settings.grid_for(target_provider):
            try:
                known = self._catalog.is_known(
                    entry.model_id, provider=target_provider, decider=settings.decider
                )
            except Exception:  # noqa: BLE001 - a status view never raises
                known = None
            if known is None:
                return []
            report.append({"model": entry.model_id, "available": bool(known)})
        return report

    def forget(self) -> None:
        self._memo.clear()


class _Where:
    """The identifiers a routing record is stamped with."""

    __slots__ = ("turn_id", "session_id", "api_request_id", "platform", "provider", "model")

    def __init__(self, *, turn_id, session_id, api_request_id, platform, provider, model) -> None:
        self.turn_id = turn_id
        self.session_id = session_id
        self.api_request_id = api_request_id
        self.platform = platform
        self.provider = provider
        self.model = model

    def fields(self) -> Dict[str, Any]:
        return {
            key: value
            for key, value in {
                "turn_id": self.turn_id,
                "session_id": self.session_id,
                "api_request_id": self.api_request_id,
                "platform": self.platform,
            }.items()
            if value
        }


_UNSET = object()


def _decision_from_memo(memo: Memo, effort: Any = _UNSET) -> Decision:
    """Present a replayed memo through the same shape as a fresh decision.

    Only the fields that were actually measured and are still load-bearing at replay are
    re-emitted. The confidences were measured as a pair, so they are re-emitted as that pair
    (never collapsed, never copied one onto the other). Everything that was *not* stored —
    the per-criterion distributions, the full alternative list, the effort-choice string —
    is left empty/``None`` rather than reconstructed from a sibling field: an audit record
    must never report a value that was not measured at replay.
    """
    actual_effort = memo.effort if effort is _UNSET else effort
    return Decision(
        model=memo.model,
        effort=actual_effort,
        effort_requested=memo.effort_requested,
        model_confidence=memo.model_confidence,
        effort_confidence=memo.effort_confidence,
        model_choice=memo.choice,
        effort_choice="",
        alternatives=(),
        latency_ms=0,
        fallback_reasons=memo.fallback_reasons,
        decider=getattr(memo, "decider", "jev") or "jev",
        # Provenance of the replayed decision: the model that answered when the memo was written,
        # never the currently configured primary (which this replay did not consult).
        decider_model=memo.decider_model_used or memo.decider_model,
        decider_fallback_reason=memo.decider_fallback_reason,
        has_effort_confidence=bool(getattr(memo, "has_effort_confidence", False)),
    )
