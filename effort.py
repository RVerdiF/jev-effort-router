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

from dataclasses import dataclass, field
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
    #: Declared vendor mapping consulted before the exact-membership test, mirroring the core's
    #: `DEEPSEEK_V4_OVERRIDES` (``{"xhigh": "max"}``). Only a target that is itself an accepted
    #: level is honoured; the mapping never adds a level the policy did not declare.
    overrides: Mapping[str, str] = field(default_factory=dict)


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
#: ``reasoning_effort`` is the knob this route accepts, and its vocabulary is the DeepSeek V4 one
#: the core already drives on this same wire (``low``/``medium``/``high``/``max``, ``xhigh``→``max``).
OPENCODE_GO_REASONING_POLICIES: Dict[str, ModelReasoningPolicy] = {
    "deepseek-v4.1-flash": ModelReasoningPolicy(
        provider="opencode-go",
        model_id="deepseek-v4.1-flash",
        wire_parameter="reasoning_effort",
        accepted_levels=("low", "medium", "high", "max"),
        evidence=(
            "Probe-confirmed on 2026-10-20 against POST https://opencode.ai/zen/go/v1/chat/completions "
            "(mandatory x-opencode-session header) for deepseek-v4.1-flash: reasoning_effort=low, "
            "reasoning_effort=medium, reasoning_effort=high, reasoning_effort=xhigh and "
            "reasoning_effort=max all returned HTTP 200, and the accepted responses carried reasoning_content "
            "plus usage.completion_tokens_details.reasoning_tokens. thinking=high returned HTTP 400 because "
            "this route requires thinking to be an object, so the parameter used is the top-level "
            "reasoning_effort. The vocabulary mirrors the core's DeepSeek V4 contract, which "
            "core/plugins/model-providers/opencode-zen already applies to deepseek-v4* ids on this same "
            "wire: DEEPSEEK_V4_EFFORTS = (low, medium, high, max) with DEEPSEEK_V4_OVERRIDES = {xhigh: max}. "
            "An out-of-vocabulary value also returns HTTP 200, so the route does not validate the value: "
            "the accepted set is the contract, not a 400 boundary."
        ),
        omitted=False,
        decision=(
            "The level chosen by Jev is written to the wire as reasoning_effort: low/medium/high verbatim, "
            "max verbatim, and xhigh mapped to max by the declared override — the same mapping the core "
            "applies on this route. Anything else (minimal, none, an unknown string) is omitted, never "
            "invented and never clamped. The probe proves the route accepts the parameter (HTTP 200, for an "
            "invalid level too), not that the level is graded or calibrated to a measured reasoning depth."
        ),
        overrides={"xhigh": "max"},
    ),
}

#: Explicit reasoning policies for the opt-in ``commandcode`` generation model.
#:
#: The wire carries **two** reasoning components, and they now have two different authors. The
#: ``extra_body.thinking`` toggle belongs to the provider layer: CommandCode's
#: ``build_api_kwargs_extras`` delegates ``deepseek/*`` ids to the DeepSeek profile, which calls
#: ``thinking_toggle_extras(..., always_emit_toggle=True)`` and so emits the toggle from the
#: params-level ``reasoning_config``. The **top-level ``reasoning_effort`` is the plugin's**, with
#: the level Jev decided on this turn.
#:
#: That split is what the transport's order allows, traced in ``agent/turn_api_request.py``
#: (``build_api_request``): ``api_kwargs`` is built from the provider profile first, the
#: ``llm_request`` middleware runs on that payload, and its result **replaces** the payload the
#: call is made with. The routed payload is built from the pre-middleware ``original_request``
#: (which already carries the profile's ``extra_body.thinking``) and only rewrites ``model`` and
#: ``EFFORT_WIRE_KEY``, so the plugin's level lands **after** the profile's derivation: it
#: overrides the effort the profile would have sent from the session's ``reasoning_config`` and
#: leaves the toggle untouched. The wire therefore carries ``thinking: {"type": "enabled"}``
#: **plus** ``reasoning_effort: <level chosen by Jev>``, not one at the expense of the other.
#:
#: Probed on 2026-10-20 against ``POST https://api.commandcode.ai/provider/v1/chat/completions``
#: with ``deepseek/deepseek-v4.1-flash``: ``reasoning_effort=low``, ``=medium``, ``=high``,
#: ``=xhigh`` and ``=max`` all returned HTTP 200. The vocabulary is the DeepSeek V4 one the core
#: already declares (``DEEPSEEK_V4_EFFORTS``/``DEEPSEEK_V4_OVERRIDES``), so ``xhigh`` maps onto
#: ``max`` by the same declared override rather than by a guess.
COMMANDCODE_REASONING_POLICIES: Dict[str, ModelReasoningPolicy] = {
    model_id: ModelReasoningPolicy(
        provider="commandcode",
        model_id=model_id,
        wire_parameter="reasoning_effort",
        accepted_levels=("low", "medium", "high", "max"),
        evidence=(
            "Probe-confirmed on 2026-10-20 against POST https://api.commandcode.ai/provider/v1/chat/"
            "completions for deepseek/deepseek-v4.1-flash: reasoning_effort=low, reasoning_effort=medium, "
            "reasoning_effort=high, reasoning_effort=xhigh and reasoning_effort=max all returned HTTP 200. "
            "The route's reasoning contract has two components with two authors, traced in "
            "agent/turn_api_request.py::build_api_request: api_kwargs is built from the provider profile "
            "first (CommandCode delegates deepseek/* to the DeepSeek profile, whose "
            "thinking_toggle_extras(..., always_emit_toggle=True) emits extra_body.thinking and its own "
            "top-level reasoning_effort from the session reasoning_config), and the llm_request middleware "
            "payload replaces that payload afterwards. Building the routed request from the pre-middleware "
            "original_request therefore keeps extra_body.thinking intact while the plugin's top-level "
            "reasoning_effort wins over the profile's derivation. The accepted set is the core's DeepSeek V4 "
            "contract (DEEPSEEK_V4_EFFORTS = low, medium, high, max with DEEPSEEK_V4_OVERRIDES = {xhigh: "
            "max}), which is the vocabulary this endpoint accepts. A 200 proves the field is accepted, not "
            "that the level is graded or calibrated to a measured reasoning depth."
        ),
        omitted=False,
        decision=(
            f"The level chosen by Jev is written to the wire as the top-level reasoning_effort for "
            f"{model_id}: low/medium/high/max verbatim and xhigh mapped to max by the declared override, "
            "the same mapping the core applies to DeepSeek V4 ids. Anything else (minimal, none, an unknown "
            "string) is omitted, never invented and never clamped. extra_body.thinking and reasoning_config "
            "are left exactly as the provider profile built them, so the toggle and the level coexist on the "
            "wire instead of one replacing the other. The audit records the level as sent (effort) and keeps "
            "effort_requested as the Jev choice; the probe shows the endpoint accepts the parameter, not that "
            "it honours a depth."
        ),
        overrides={"xhigh": "max"},
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
#: Their vocabulary is exact: the requested level goes out only when the policy declares it, or
#: when a declared override maps it onto a declared level. No nearest-weaker clamp, because the
#: family fallback would answer a level the route never declared (``minimal``/``ultra``/an unknown
#: string would land on some Ollama rung).
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
    commandcode) pass the requested level through only when their policy declares it, or when
    a declared override maps it onto a declared level; anything else is omitted rather than
    guessed, clamped or escalated.
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

    # Built-in opencode/commandcode policy: only a declared level goes out. These wires do not
    # share the Ollama family vocabulary, so a level the policy did not declare is omitted
    # instead of being clamped onto some family rung; a declared override (the Go route's
    # `xhigh` -> `max`, same as the core's DEEPSEEK_V4_OVERRIDES) is honoured first.
    if provider_key in BUILTIN_POLICY_PROVIDERS:
        requested = str(effort or "").strip().lower()
        if requested in policy.accepted_levels:
            return requested
        mapped = (policy.overrides or {}).get(requested)
        return mapped if mapped in policy.accepted_levels else None

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
