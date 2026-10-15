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


def resolve_effort(
    model_id: Optional[str],
    effort: Optional[str],
    *,
    provider: str = "",
    effort_maps: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """The exact value to put on the wire for ``model_id``, or ``None`` to omit the field.

    When ``effort_maps`` specifies a map for ``provider``, it takes precedence.
    For unmapped non-Ollama providers (like opencode-go), reasoning effort is unverified
    and defaults to None (omitted on wire, never escalated).
    """
    if not effort:
        return None

    provider_key = (provider or "").strip().lower()

    # Check per-provider custom effort_maps if present
    if effort_maps and provider_key in effort_maps:
        pmap = effort_maps[provider_key]
        if isinstance(pmap, (dict, Mapping)):
            model_key = (model_id or "").strip().lower().rsplit("/", 1)[-1]
            # Look for exact match or slug match
            matched_spec = None
            for k, spec in pmap.items():
                cand = str(k).strip().lower()
                if cand == model_key or cand == (model_id or "").strip().lower() or cand in model_key:
                    matched_spec = spec
                    break
            if matched_spec is not None:
                return _resolve_custom_provider_effort(effort, matched_spec)
            # If model is not in provider's effort_map, omit
            return None

    # For providers other than ollama-cloud, if no effort map is declared, default is omit
    if provider_key and provider_key not in ("ollama-cloud", "ollama_cloud"):
        return None

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
