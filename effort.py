"""Per-family reasoning-effort translation.

Hermes carries a wide internal ladder (``none`` … ``ultra``) and clamps it per provider.
The router only ever asks for ``low``/``medium``/``high``, so the job here is narrower:
land the chosen level on the vocabulary the *selected model's* wire really accepts, never
escalate, and omit the field rather than send a level the route would reject with a 400.

The vocabularies below mirror ``agent/reasoning_effort.py`` in hermes-agent, which is the
reference implementation of the clamping rule ("nearest weaker level, never stronger").
`docs/routing-grid.md` records where each row came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

#: Hermes' ladder, low to high. Used only for nearest-weaker arithmetic.
LADDER: Tuple[str, ...] = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")


@dataclass(frozen=True)
class Family:
    """One model family's effort contract on its wire."""

    name: str
    accepted: Tuple[str, ...]
    overrides: Dict[str, str]


#: Ollama:cloud /v1/chat/completions. ``max`` is undocumented but real; ``minimal`` 400s.
OLLAMA_CLOUD = Family(
    name="ollama-cloud",
    accepted=("none", "low", "medium", "high", "max"),
    overrides={"xhigh": "max"},
)

FAMILIES: Tuple[Family, ...] = (
    # DeepSeek V4 / V4.1 — graded, xhigh rounds up to max.
    Family("deepseek", ("low", "medium", "high", "max"), {"xhigh": "max"}),
    # Kimi K3 — documented set is low/high/max; `high` is its positional middle, so a
    # `medium` request maps onto `high` rather than down to `low`.
    Family("kimi-k3", ("low", "high", "max"), {"medium": "high", "xhigh": "max"}),
    # GLM-5.3 — graded low/medium/high/max, live-verified monotonic.
    Family("glm-53", ("low", "medium", "high", "max"), {"xhigh": "max"}),
    # GLM-5.2 — the older knob only has high and max.
    Family("glm-52", ("high", "max"), {"xhigh": "max"}),
    # MiniMax M3 — no narrower vocabulary published; the Ollama:cloud set applies.
    Family("minimax", OLLAMA_CLOUD.accepted, dict(OLLAMA_CLOUD.overrides)),
    # Nemotron 3 Nano — same, and `low` is the honest landing for its "simple tasks" profile.
    Family("nemotron", OLLAMA_CLOUD.accepted, dict(OLLAMA_CLOUD.overrides)),
)

#: Table of default families by provider
PROVIDER_FAMILIES: Dict[str, Tuple[Family, ...]] = {
    "ollama-cloud": FAMILIES,
    "ollama_cloud": FAMILIES,
}


def family_for(model_id: Optional[str], provider: str = "") -> Family:
    """Pick the family contract for a model id.

    Matching is a substring test on the bare slug, so ``kimi-k3``, ``kimi-k3-256k`` and a
    ``vendor/kimi-k3`` prefix all land on the same row. Unknown models get the
    Ollama:cloud vocabulary, which is the widest set this router's provider accepts.
    """
    slug = (model_id or "").strip().lower().rsplit("/", 1)[-1]
    if not slug:
        return OLLAMA_CLOUD
    if "kimi" in slug and _token(slug, "k3"):
        return FAMILIES[1]
    if "deepseek" in slug:
        return FAMILIES[0]
    if slug.startswith("glm"):
        return FAMILIES[3] if _token(slug, "5.2") else FAMILIES[2]
    if "minimax" in slug:
        return FAMILIES[4]
    if "nemotron" in slug:
        return FAMILIES[5]
    return OLLAMA_CLOUD


def _token(slug: str, needle: str) -> bool:
    """Boundary-ish containment so ``k2.6`` never matches ``k3`` and ``5.3`` never ``5.2``."""
    return needle in slug.replace("-", " ").replace("_", " ").split() or needle in slug


def clamp(
    effort: Optional[str],
    accepted: Sequence[str],
    overrides: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Clamp a requested level onto ``accepted``; ``None`` means "omit the field".

    Order: declared override, verbatim when accepted, else the nearest **weaker** level, and
    the provider's floor when nothing weaker exists. A level that is not a ladder rung at all
    (a bespoke provider vocabulary) is passed through untouched.
    """
    requested = str(effort or "").strip().lower()
    if not requested:
        return None

    normalised = [level for level in (str(item).strip().lower() for item in accepted) if level in LADDER]
    if not normalised:
        return requested
    if requested in normalised:
        return requested
    if overrides and overrides.get(requested) in normalised:
        return overrides[requested]
    if requested not in LADDER:
        return requested

    # `none` disables reasoning outright — never a landing target for an enabled request.
    candidates = [level for level in normalised if level != "none"]
    if not candidates:
        return requested
    request_index = LADDER.index(requested)
    weaker = [level for level in candidates if LADDER.index(level) < request_index]
    if weaker:
        return max(weaker, key=LADDER.index)
    return min(candidates, key=LADDER.index)


def _resolve_custom_provider_effort(
    requested_effort: str,
    spec: Any,
) -> Optional[str]:
    """Resolve effort for a custom provider map without escalation.

    Rules (O2):
    - Never escalate or invent a level: omit (return None) when no declared accepted
      level is equal to or weaker than requested.
    - An empty spec, empty accepted list, or unverified model returns None (omit).
    - An escalating override (mapping a level to a strictly stronger level) is ignored.
    - If requested is in accepted: return requested.
    - If requested is in LADDER: clamp only to the nearest *weaker* accepted level.
      If no weaker accepted level exists, return None (never escalate to a higher floor).
    """
    requested = requested_effort.strip().lower()
    if not requested:
        return None

    if isinstance(spec, (list, tuple, set)):
        accepted_raw = spec
        overrides = {}
    elif isinstance(spec, (dict, Mapping)):
        accepted_raw = spec.get("accepted", ())
        overrides = spec.get("overrides", {})
    else:
        return None

    accepted = [str(item).strip().lower() for item in accepted_raw if str(item).strip().lower() in LADDER]
    # 'none' disables reasoning outright - never a landing target
    accepted = [lvl for lvl in accepted if lvl != "none"]
    if not accepted:
        return None

    # Check overrides, but reject escalating overrides
    if overrides and requested in overrides:
        candidate_ovr = str(overrides[requested]).strip().lower()
        if candidate_ovr in accepted:
            if requested in LADDER and candidate_ovr in LADDER:
                if LADDER.index(candidate_ovr) <= LADDER.index(requested):
                    return candidate_ovr
            elif candidate_ovr == requested:
                return candidate_ovr

    if requested in accepted:
        return requested

    if requested not in LADDER:
        return None

    req_idx = LADDER.index(requested)
    weaker = [lvl for lvl in accepted if LADDER.index(lvl) < req_idx]
    if weaker:
        return max(weaker, key=LADDER.index)

    # No level equal or weaker exists -> omit, never escalate!
    return None


@dataclass(frozen=True)
class ModelReasoningPolicy:
    """Explicit reasoning effort policy for a model under a target provider."""

    provider: str
    model_id: str
    wire_parameter: Optional[str]
    accepted_levels: Tuple[str, ...]
    evidence: str
    omitted: bool
    decision: str


#: Explicit reasoning policies for the opt-in OpenCode routes. Support on one provider is never
#: inferred from another: the Console, Go and Ollama:cloud wires are different contracts, so the
#: Console model still omits the field while the Go model carries the probe-confirmed top-level
#: ``reasoning_effort``.
OPENCODE_CONSOLE_REASONING_POLICIES: Dict[str, ModelReasoningPolicy] = {
    "deepseek-v4.1-flash": ModelReasoningPolicy(
        provider="opencode-console",
        model_id="deepseek-v4.1-flash",
        wire_parameter=None,
        accepted_levels=(),
        evidence=(
            "Unconfirmed on opencode-console generation endpoint. Support on opencode-go (/zen/go/v1) "
            "or ollama-cloud cannot be inferred."
        ),
        omitted=True,
        decision=(
            "Explicit omission: Documentation and empirical probe evidence do not confirm reasoning_effort parameter "
            "support on the opencode-console generation endpoint for deepseek-v4.1-flash. The parameter is omitted on "
            "the wire to prevent HTTP 400 rejection. The Jev logical effort choice is recorded as effort_requested in "
            "audit trails, but wire effort remains None without claiming downstream model execution."
        ),
    ),
}

#: Explicit reasoning policies for the ``opencode-go`` generation model.
#:
#: Probed on 2026-10-20 against ``POST https://opencode.ai/zen/go/v1/chat/completions`` (the
#: ``x-opencode-session`` header is mandatory) with ``deepseek-v4.1-flash``: the top-level
#: ``reasoning_effort`` is the knob this route accepts, and its vocabulary stops at ``high``.
OPENCODE_GO_REASONING_POLICIES: Dict[str, ModelReasoningPolicy] = {
    "deepseek-v4.1-flash": ModelReasoningPolicy(
        provider="opencode-go",
        model_id="deepseek-v4.1-flash",
        wire_parameter="reasoning_effort",
        accepted_levels=("low", "medium", "high"),
        evidence=(
            "Probe-confirmed on 2026-10-20 against POST https://opencode.ai/zen/go/v1/chat/completions "
            "(mandatory x-opencode-session header) for deepseek-v4.1-flash: reasoning_effort=high and "
            "reasoning_effort=low both returned HTTP 200, and the responses carried reasoning_content plus "
            "usage.completion_tokens_details.reasoning_tokens. thinking=high returned HTTP 400 because this "
            "route requires thinking to be an object, so the parameter used is the top-level reasoning_effort. "
            "This route's vocabulary for the router is low|medium|high: nothing above high is invented or sent."
        ),
        omitted=False,
        decision=(
            "The level chosen by Jev is written to the wire as reasoning_effort, and only ever within "
            "low|medium|high: an out-of-vocabulary level (max, xhigh, minimal or anything else) is omitted, "
            "never invented and never escalated. The probe proves the route accepts the parameter (HTTP 200), "
            "not that the level is calibrated to a measured reasoning depth."
        ),
    ),
}

#: Explicit reasoning policies for the opt-in ``commandcode`` generation model.
#:
#: This route's contract is *not* the OpenCode one, and it has **two** components — both owned by
#: the provider layer. CommandCode's ``build_api_kwargs_extras`` delegates ``deepseek/*`` ids to the
#: DeepSeek profile, which calls ``thinking_toggle_extras(..., always_emit_toggle=True)`` and so
#: emits ``extra_body.thinking`` **and** a top-level ``reasoning_effort`` carrying the level clamped
#: to DeepSeek's V4 vocabulary (low/medium/high/max, xhigh->max). Both are derived from the
#: params-level ``reasoning_config``; the profile is their only author.
#:
#: The plugin omits *its own* effort on purpose. It builds the payload from the pre-middleware
#: request, and a top-level ``reasoning_effort`` left there would be merged by the transport *after*
#: the profile extras, so it could override or contradict the profile's derivation; ``_apply``
#: therefore drops it. What stands unconfirmed is whether the CommandCode endpoint grades a
#: Jev-chosen level at all: the profile proves Hermes sends the field, not that this provider
#: honours it, and no paid probe was run. The Jev choice is recorded in ``effort_requested`` and
#: never claimed on the wire; support is never inferred from the OpenCode routes.
COMMANDCODE_REASONING_POLICIES: Dict[str, ModelReasoningPolicy] = {
    model_id: ModelReasoningPolicy(
        provider="commandcode",
        model_id=model_id,
        wire_parameter=None,
        accepted_levels=(),
        evidence=(
            "Two-component contract, traced offline: CommandCode's build_api_kwargs_extras delegates "
            "deepseek/* ids to the DeepSeek profile, whose thinking_toggle_extras(..., "
            "always_emit_toggle=True) emits BOTH extra_body.thinking and a top-level reasoning_effort "
            "clamped to DeepSeek's V4 vocabulary (low/medium/high/max, xhigh->max). The plugin drops a "
            "pre-existing top-level reasoning_effort so it cannot override the profile's derivation, and "
            "does not claim the endpoint grades a Jev-chosen level: unconfirmed, never inferred from OpenCode."
        ),
        omitted=True,
        decision=(
            f"Deliberate omission of the plugin's own field: on commandcode the native reasoning controls for "
            f"{model_id} belong to the provider profile (extra_body.thinking plus its own top-level "
            "reasoning_effort derived from reasoning_config). A pre-existing top-level reasoning_effort is "
            "removed; extra_body.thinking and reasoning_config are left untouched. Stated uncertainty: the "
            "profile proves Hermes sends reasoning_effort on this wire, but whether CommandCode honours a "
            "Jev-chosen level is unconfirmed (no documented contract, no probe, no paid call). The Jev logical "
            "effort stays in effort_requested, wire effort stays null, and no claim is made that the model "
            "executed at that effort."
        ),
    )
    for model_id in ("deepseek/deepseek-v4.1-flash",)
}


def model_reasoning_policy(
    model_id: Optional[str],
    *,
    provider: str = "",
    effort_maps: Optional[Mapping[str, Any]] = None,
) -> ModelReasoningPolicy:
    """Return the explicit reasoning policy for a given model and provider."""
    provider_key = (provider or "").strip().lower()
    mid = (model_id or "").strip().lower().rsplit("/", 1)[-1]

    # Custom configured effort_maps
    if effort_maps and provider_key in effort_maps:
        pmap = effort_maps[provider_key]
        if isinstance(pmap, (dict, Mapping)):
            for k, spec in pmap.items():
                cand = str(k).strip().lower()
                if cand == mid or cand == (model_id or "").strip().lower() or cand in mid:
                    if isinstance(spec, (list, tuple, set)):
                        acc = tuple(
                            str(x).strip().lower()
                            for x in spec
                            if str(x).strip().lower() in LADDER and str(x).strip().lower() != "none"
                        )
                    elif isinstance(spec, (dict, Mapping)):
                        acc = tuple(
                            str(x).strip().lower()
                            for x in spec.get("accepted", ())
                            if str(x).strip().lower() in LADDER and str(x).strip().lower() != "none"
                        )
                    else:
                        acc = ()
                    if acc:
                        return ModelReasoningPolicy(
                            provider=provider_key,
                            model_id=model_id or "",
                            wire_parameter="reasoning_effort",
                            accepted_levels=acc,
                            evidence="Explicitly configured by operator via settings.effort_maps",
                            omitted=False,
                            decision=f"Operator declared effort map for {provider_key}/{model_id} with accepted levels {acc}.",
                        )
                    return ModelReasoningPolicy(
                            provider=provider_key,
                            model_id=model_id or "",
                            wire_parameter=None,
                            accepted_levels=(),
                            evidence="Empty or invalid operator effort map specification",
                            omitted=True,
                            decision=f"Operator configured empty or invalid effort map for {provider_key}/{model_id}; omitting on wire.",
                    )

    # opencode-console built-in policy
    if provider_key in ("opencode-console", "opencode_console"):
        for k, pol in OPENCODE_CONSOLE_REASONING_POLICIES.items():
            if k == mid or k in mid:
                return pol
        return ModelReasoningPolicy(
            provider=provider_key,
            model_id=model_id or "",
            wire_parameter=None,
            accepted_levels=(),
            evidence="Model not in opencode-console allowlist; unconfirmed reasoning support.",
            omitted=True,
            decision=f"Model {model_id} has no confirmed reasoning contract on {provider_key}; omitted on wire.",
        )

    # opencode-go built-in policy: the generation route of the opt-in Console decider.
    if provider_key in ("opencode-go", "opencode_go"):
        for k, pol in OPENCODE_GO_REASONING_POLICIES.items():
            if k == mid or k in mid:
                return pol
        return ModelReasoningPolicy(
            provider=provider_key,
            model_id=model_id or "",
            wire_parameter=None,
            accepted_levels=(),
            evidence="Model not in the opencode-go catalog; unconfirmed reasoning support.",
            omitted=True,
            decision=f"Model {model_id} has no confirmed reasoning contract on {provider_key}; omitted on wire.",
        )

    # commandcode built-in policy: namespaced ids, native DeepSeek thinking controls.
    if provider_key in ("commandcode", "commandcode-chat", "commandcode_chat"):
        for k, pol in COMMANDCODE_REASONING_POLICIES.items():
            if k == (model_id or "").strip().lower() or k.rsplit("/", 1)[-1] == mid:
                return pol
        return ModelReasoningPolicy(
            provider=provider_key,
            model_id=model_id or "",
            wire_parameter=None,
            accepted_levels=(),
            evidence="Model not in the commandcode allowlist; unconfirmed reasoning support.",
            omitted=True,
            decision=f"Model {model_id} has no confirmed reasoning contract on {provider_key}; omitted on wire.",
        )

    # ollama-cloud built-in policy
    if not provider_key or provider_key in ("ollama-cloud", "ollama_cloud"):
        fam = family_for(model_id, provider=provider_key)
        return ModelReasoningPolicy(
            provider="ollama-cloud",
            model_id=model_id or "",
            wire_parameter="reasoning_effort",
            accepted_levels=fam.accepted,
            evidence="Empirically verified against Ollama:cloud chat completions contract.",
            omitted=False,
            decision=f"Mapped to family '{fam.name}' with accepted levels {fam.accepted}.",
        )

    # Generic unmapped provider
    return ModelReasoningPolicy(
        provider=provider_key,
        model_id=model_id or "",
        wire_parameter=None,
        accepted_levels=(),
        evidence="Unmapped provider without confirmed contract or operator effort_maps.",
        omitted=True,
        decision=f"Provider {provider_key} is unmapped and lacks verified reasoning contract; omitted on wire.",
    )


#: Providers whose effort contract is a built-in per-model policy rather than a family table.
#: Their vocabulary is exact: no nearest-weaker clamp, because the Go route's DeepSeek family
#: would answer ``max`` for ``xhigh`` — a level that wire does not have.
BUILTIN_POLICY_PROVIDERS: tuple[str, ...] = (
    "opencode-console",
    "opencode_console",
    "opencode-go",
    "opencode_go",
    "commandcode",
    "commandcode-chat",
    "commandcode_chat",
)


def resolve_effort(
    model_id: Optional[str],
    effort: Optional[str],
    *,
    provider: str = "",
    effort_maps: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """The exact value to put on the wire for ``model_id``, or ``None`` to omit the field.

    When ``effort_maps`` specifies a map for ``provider``, it takes precedence and is clamped
    to its declared accepted levels. Built-in routes (opencode-console, opencode-go,
    commandcode) pass the requested level through only when it is one of the levels their
    policy declares; anything else is omitted rather than guessed, clamped or escalated.
    """
    if not effort:
        return None

    policy = model_reasoning_policy(model_id, provider=provider, effort_maps=effort_maps)
    if policy.omitted or not policy.wire_parameter:
        return None

    provider_key = (provider or "").strip().lower()

    # If resolved via custom effort_maps
    if effort_maps and provider_key in effort_maps:
        pmap = effort_maps[provider_key]
        if isinstance(pmap, (dict, Mapping)):
            model_key = (model_id or "").strip().lower().rsplit("/", 1)[-1]
            matched_spec = None
            for k, spec in pmap.items():
                cand = str(k).strip().lower()
                if cand == model_key or cand == (model_id or "").strip().lower() or cand in model_key:
                    matched_spec = spec
                    break
            if matched_spec is not None:
                return _resolve_custom_provider_effort(effort, matched_spec)

    # Built-in opencode/commandcode policy: only an exact accepted level goes out. These wires
    # do not share the Ollama family vocabulary, and the Go route's DeepSeek family would clamp
    # `xhigh` up to `max`, which that route does not accept.
    if provider_key in BUILTIN_POLICY_PROVIDERS:
        requested = str(effort or "").strip().lower()
        return requested if requested in policy.accepted_levels else None

    # Default built-in Ollama:cloud families
    family = family_for(model_id, provider=provider_key)
    clamped = clamp(effort, family.accepted, family.overrides)
    return clamped


def is_omitted(
    model_id: Optional[str],
    effort: Optional[str],
    *,
    provider: str = "",
    effort_maps: Optional[Mapping[str, Any]] = None,
) -> bool:
    """True when the request would go out without a reasoning-effort field."""
    return resolve_effort(model_id, effort, provider=provider, effort_maps=effort_maps) is None
