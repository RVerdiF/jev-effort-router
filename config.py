"""Resolved plugin settings.

Every tunable comes from one place so a callback, a tool handler, a slash command and the
CLI all read the same values. ``ctx.get_config`` already resolves
``plugins.entries.jev-effort-router.settings.<key>`` over the manifest default, so this module
only adds coercion, validation and the "value is missing or nonsense" backstops.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from .grid import (
    DEFAULT_GRID,
    OPENCODE_CONSOLE_GRID,
    OPENCODE_GO_GRID,
    Entry,
    parse_grid,
    parse_grid_strict,
)

#: Provider profile this router is benchmarked for. Anything else is skipped untouched.
ROUTED_PROVIDER = "ollama-cloud"

#: Provider profile name aliases that also count as routed.
ROUTED_PROVIDER_ALIASES = ("ollama_cloud",)

#: OpenCode Console provider and aliases.
OPENCODE_CONSOLE_PROVIDER = "opencode-console"
OPENCODE_CONSOLE_PROVIDER_ALIASES = ("opencode_console",)

#: OpenCode Go provider and aliases: the generation route for the opt-in Console decider.
OPENCODE_GO_PROVIDER = "opencode-go"
OPENCODE_GO_PROVIDER_ALIASES = ("opencode_go",)

#: Console serves generation for the one normal (non-free) id its public model list carries.
#: Normal MiMo-V2.6-Flash is a Go model, so it is deliberately absent here; the free variant
#: was refused by the owner and is absent from both catalogs.
OPENCODE_CONSOLE_MODELS: Tuple[str, ...] = ("deepseek-v4.1-flash",)
OPENCODE_CONSOLE_ALLOWLIST = frozenset(OPENCODE_CONSOLE_MODELS)

#: The Go generation route's initial catalog: the two authorized normal models.
OPENCODE_GO_MODELS: Tuple[str, ...] = ("deepseek-v4.1-flash", "mimo-v2.6-flash")
OPENCODE_GO_ALLOWLIST = frozenset(OPENCODE_GO_MODELS)

#: The provider/alias pairs an operator grid may be declared under.
_GRID_ALIASES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (ROUTED_PROVIDER, ROUTED_PROVIDER_ALIASES),
    (OPENCODE_CONSOLE_PROVIDER, OPENCODE_CONSOLE_PROVIDER_ALIASES),
    (OPENCODE_GO_PROVIDER, OPENCODE_GO_PROVIDER_ALIASES),
)

#: API modes this router understands. The Ollama:cloud profile is chat_completions;
#: a Responses or Anthropic-Messages route builds its payload elsewhere and is left alone.
ROUTED_API_MODES = ("chat_completions",)

EFFORT_LEVELS: Tuple[str, ...] = ("low", "medium", "high")

DECIDERS: Tuple[str, ...] = ("jev", "openai_compatible", "jev_console")
DEFAULT_DECIDER = "jev"
DEFAULT_ROUTABLE_PROVIDERS: Tuple[str, ...] = ("ollama-cloud",)
DEFAULT_OPENAI_COMPATIBLE_BASE_URL = "https://opencode.ai/zen/go/v1"
DEFAULT_OPENAI_COMPATIBLE_MODEL = "deepseek-v4.1-flash"
DEFAULT_OPENAI_COMPATIBLE_KEY_ENV = "OPENCODE_GO_API_KEY"

DEFAULT_CONSOLE_BASE_URL = "https://opencode.ai/inference/systemone/v1/systemone"
DEFAULT_CONSOLE_JEV_MODEL = "jev-1.13-free"
DEFAULT_CONSOLE_JEV_FALLBACK_MODEL = "jev-1.13"
DEFAULT_CONSOLE_KEY_ENV = "OPENCODE_CONSOLE_API_KEY"

DEFAULT_JEV_MODEL = "typesafe/jev-1.13"
DEFAULT_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_TIMEOUT_S = 2.0
DEFAULT_CONFIDENCE_THRESHOLD = 0.5
DEFAULT_MODEL = "deepseek-v4.1-flash"
DEFAULT_EFFORT = "medium"
DEFAULT_CONTEXT_TURNS = 4


def _as_optional_bool(value: Any) -> Optional[bool]:
    """Coerce to a bool, or ``None`` when the value is absent or unreadable.

    ``None`` is the "inherit the global setting" sentinel, so it has to stay distinguishable
    from a parsed ``False``.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    return None


def _as_bool(value: Any, default: bool) -> bool:
    parsed = _as_optional_bool(value)
    return default if parsed is None else parsed


def _as_optional_float(value: Any, *, minimum: Optional[float] = None) -> Optional[float]:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    if minimum is not None and result < minimum:
        return None
    return result


def _as_float(value: Any, default: float = 0.0, *, minimum: Optional[float] = None) -> float:
    parsed = _as_optional_float(value, minimum=minimum)
    return default if parsed is None else parsed


def _as_int(value: Any, default: int, *, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and result < minimum:
        return default
    if maximum is not None and result > maximum:
        return maximum
    return result


def _as_optional_choice(value: Any, allowed: Sequence[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text if text in allowed else None


def _as_choice(value: Any, allowed: Sequence[str], default: str) -> str:
    parsed = _as_optional_choice(value, allowed)
    return default if parsed is None else parsed


def _as_text(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text or default


def _entry_for(grid: Sequence[Entry], model_id: str) -> Optional[Entry]:
    """The grid entry whose model id is ``model_id``, or ``None``."""
    wanted = (model_id or "").strip()
    for entry in grid:
        if entry.model_id == wanted:
            return entry
    return None


def route_allowlist(provider: str, decider: str = "") -> Optional[frozenset]:
    """The allowlist that pins ``provider``'s catalog, or ``None`` when it has none.

    ``opencode-console`` is pinned at all times: its models are the Console's own. The
    ``opencode-go`` cap is **opt-in** — it is in force only while the ``jev_console`` decider
    is active, so a legacy ``jev``/``openai_compatible`` setup keeps whatever Go grid the
    operator configured, unchanged.
    """
    key = (provider or "").strip().lower()
    if key in (OPENCODE_CONSOLE_PROVIDER, *OPENCODE_CONSOLE_PROVIDER_ALIASES):
        return OPENCODE_CONSOLE_ALLOWLIST
    if key in (OPENCODE_GO_PROVIDER, *OPENCODE_GO_PROVIDER_ALIASES):
        return OPENCODE_GO_ALLOWLIST if (decider or "").strip().lower() == "jev_console" else None
    return None


def route_catalog(provider: str) -> Optional[Tuple[Entry, ...]]:
    """The built-in generation catalog of an opt-in route, or ``None`` for any other provider."""
    key = (provider or "").strip().lower()
    if key in (OPENCODE_CONSOLE_PROVIDER, *OPENCODE_CONSOLE_PROVIDER_ALIASES):
        return OPENCODE_CONSOLE_GRID
    if key in (OPENCODE_GO_PROVIDER, *OPENCODE_GO_PROVIDER_ALIASES):
        return OPENCODE_GO_GRID
    return None


def _capped(
    grid: Tuple[Entry, ...],
    allowlist: Optional[frozenset],
    fallback: Optional[Tuple[Entry, ...]],
) -> Tuple[Entry, ...]:
    """``grid`` restricted to ``allowlist``; ``fallback`` when that leaves nothing on it."""
    if allowlist is None:
        return grid
    capped = tuple(entry for entry in grid if entry.model_id in allowlist)
    if capped:
        return capped
    return fallback if fallback is not None else grid


@dataclass(frozen=True)
class PlatformPolicy:
    """One surface's routing policy, as declared under ``platform_overrides``.

    Every field is optional and ``None`` means "inherit the global setting", deliberately
    distinct from a parsed ``False``/``0.0``. ``grid`` is ``None`` unless a policy grid both
    was declared and parsed; the resolved grid then falls to the effective global one.
    """

    enabled: Optional[bool] = None
    grid: Optional[Tuple[Entry, ...]] = None
    confidence_threshold: Optional[float] = None
    max_effort: Optional[str] = None


@dataclass(frozen=True)
class EffectiveSettings:
    """The frozen per-request snapshot the decision path consumes.

    Built once per request from the global :class:`Settings` and, when a surface has one, its
    :class:`PlatformPolicy`. A policy field overrides the global value; everything else is the
    global setting unchanged.
    """

    grid: Tuple[Entry, ...] = field(default_factory=lambda: tuple(DEFAULT_GRID))
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    default_model: str = DEFAULT_MODEL
    default_effort: str = DEFAULT_EFFORT
    max_effort: Optional[str] = None
    #: Whether ``grid`` is the surface's own parsed policy grid (``True``) or the global grid
    #: (``False``, whether from an absent policy or one whose grid did not parse). The decision
    #: path needs the distinction: an off-grid ``default_model`` resolves to the effective grid
    #: only when a policy grid is genuinely in force, so a surface the policy does not name keeps
    #: today's configured default verbatim.
    grid_from_policy: bool = False

    def entry_for(self, model_id: str) -> Optional[Entry]:
        return _entry_for(self.grid, model_id)


def _make_hashable(val: Any) -> Any:
    if isinstance(val, (dict, Mapping)):
        return frozenset((k, _make_hashable(v)) for k, v in val.items())
    if isinstance(val, (list, tuple)):
        return tuple(_make_hashable(x) for x in val)
    if isinstance(val, (set, frozenset)):
        return frozenset(_make_hashable(x) for x in val)
    return val


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the plugin's effective settings."""

    enabled: bool = True
    jev_model: str = DEFAULT_JEV_MODEL
    endpoint: str = DEFAULT_ENDPOINT
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    timeout_s: float = DEFAULT_TIMEOUT_S
    default_model: str = DEFAULT_MODEL
    default_effort: str = DEFAULT_EFFORT
    context_turns: int = DEFAULT_CONTEXT_TURNS
    route_per_turn: bool = True
    audit_enabled: bool = True
    log_skips: bool = True
    include_user_message_in_audit: bool = False
    grid: Tuple[Entry, ...] = field(default_factory=lambda: tuple(DEFAULT_GRID))
    #: A read-only mapping, so the frozen snapshot cannot be mutated through the field.
    platform_overrides: Mapping[str, PlatformPolicy] = field(
        default_factory=lambda: MappingProxyType({})
    )
    decider: str = DEFAULT_DECIDER
    decider_base_url: str = ""
    decider_model: str = ""
    decider_api_key_env: str = ""
    console_endpoint: str = ""
    console_jev_model: str = ""
    console_jev_fallback_model: str = DEFAULT_CONSOLE_JEV_FALLBACK_MODEL
    console_api_key_env: str = ""
    routable_providers: Tuple[str, ...] = DEFAULT_ROUTABLE_PROVIDERS
    grids: Mapping[str, Tuple[Entry, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    effort_maps: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    confidence_threshold_per_decider: Mapping[str, float] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __hash__(self) -> int:
        """Hash the snapshot, treating read-only mappings as frozensets.

        A frozen dataclass hashes its fields by default, but ``MappingProxyType`` is hashable
        only from Python 3.12 — and this plugin's matrix includes 3.11, where the default
        would make ``Settings`` unhashable. Hashing the mapping's items keeps the snapshot
        hashable on every supported interpreter without making it mutable.
        """
        parts = []
        for spec in fields(self):
            value = getattr(self, spec.name)
            parts.append((spec.name, _make_hashable(value)))
        return hash(tuple(parts))

    @property
    def grid_ids(self) -> Tuple[str, ...]:
        return tuple(entry.model_id for entry in self.grid)

    def entry_for(self, model_id: str) -> Optional[Entry]:
        return _entry_for(self.grid, model_id)

    def policy_for(self, platform: str) -> Optional[PlatformPolicy]:
        """The policy declared for ``platform``, or ``None`` when it is unlisted."""
        key = (platform or "").strip().lower()
        return self.platform_overrides.get(key)

    def grid_for(self, provider: str = "") -> Tuple[Entry, ...]:
        """The grid for ``provider``, or the global grid when unlisted.

        An opt-in OpenCode route is pinned by its catalog (see :func:`route_allowlist`): an
        entry outside the catalog is dropped, and a grid the cap empties falls back to the
        route's built-in catalog. Choice, fallback, alternatives and replay all read their
        options from here, so none of them can name a model the route does not serve.
        """
        key = (provider or "").strip().lower()
        configured: Optional[Tuple[Entry, ...]] = None
        if key in self.grids:
            configured = self.grids[key]
        else:
            for name, aliases in _GRID_ALIASES:
                if key in aliases and name in self.grids:
                    configured = self.grids[name]
                    break
        allowlist = route_allowlist(key, self.decider)
        catalog = route_catalog(key)
        if allowlist is None:
            return configured if configured is not None else self.grid
        return _capped(
            configured if configured is not None else (catalog or self.grid),
            allowlist,
            catalog,
        )

    def effort_map_for(self, provider: str = "") -> Optional[Any]:
        """The effort map for ``provider``, or None when unlisted."""
        key = (provider or "").strip().lower()
        if key in self.effort_maps:
            return self.effort_maps[key]
        if key in ROUTED_PROVIDER_ALIASES and ROUTED_PROVIDER in self.effort_maps:
            return self.effort_maps[ROUTED_PROVIDER]
        return None

    def api_key_env_for(self, decider: Optional[str] = None) -> str:
        """The environment variable name holding the API key for ``decider``."""
        target = (decider or self.decider).strip().lower()
        if target == "openai_compatible":
            return self.decider_api_key_env or DEFAULT_OPENAI_COMPATIBLE_KEY_ENV
        if target == "jev_console":
            return self.console_api_key_env or DEFAULT_CONSOLE_KEY_ENV
        return "OPENROUTER_API_KEY"

    def base_url_for(self, decider: Optional[str] = None) -> str:
        """The base URL for ``decider``."""
        target = (decider or self.decider).strip().lower()
        if target == "openai_compatible":
            return self.decider_base_url or DEFAULT_OPENAI_COMPATIBLE_BASE_URL
        if target == "jev_console":
            return self.console_endpoint or DEFAULT_CONSOLE_BASE_URL
        return self.endpoint

    @property
    def base_url(self) -> str:
        """The effective base URL for the active decider."""
        return self.base_url_for()

    def decider_model_for(self, decider: Optional[str] = None) -> str:
        """The decision model id for ``decider``."""
        target = (decider or self.decider).strip().lower()
        if target == "openai_compatible":
            return self.decider_model or DEFAULT_OPENAI_COMPATIBLE_MODEL
        if target == "jev_console":
            return self.console_jev_model or DEFAULT_CONSOLE_JEV_MODEL
        return self.jev_model

    def confidence_threshold_for(self, decider: Optional[str] = None) -> float:
        """The confidence threshold for ``decider``."""
        target = (decider or self.decider).strip().lower()
        if target in self.confidence_threshold_per_decider:
            return self.confidence_threshold_per_decider[target]
        return self.confidence_threshold

    def is_provider_routable(self, provider: str) -> bool:
        """Whether ``provider`` is in the configured routable providers list."""
        key = (provider or "").strip().lower()
        if not key:
            return any(
                p.strip().lower() in (ROUTED_PROVIDER, *ROUTED_PROVIDER_ALIASES)
                for p in self.routable_providers
            )
        for p in self.routable_providers:
            cand = p.strip().lower()
            if cand == key:
                return True
            if key in ROUTED_PROVIDER_ALIASES and cand == ROUTED_PROVIDER:
                return True
            if cand in ROUTED_PROVIDER_ALIASES and key == ROUTED_PROVIDER:
                return True
            if key in OPENCODE_CONSOLE_PROVIDER_ALIASES and cand == OPENCODE_CONSOLE_PROVIDER:
                return True
            if cand in OPENCODE_CONSOLE_PROVIDER_ALIASES and key == OPENCODE_CONSOLE_PROVIDER:
                return True
        return False

    def effective(self, policy: Optional[PlatformPolicy], provider: str = "") -> EffectiveSettings:
        """Resolve one frozen snapshot, applying ``policy`` over the global settings.

        A policy field of ``None`` inherits the global value; a policy ``grid`` that did not
        parse also inherits, so a bad policy grid never replaces the operator's global grid.
        """
        base_grid = self.grid_for(provider)
        key = (provider or "").strip().lower()
        allowlist = route_allowlist(key, self.decider)
        base_threshold = self.confidence_threshold_for(self.decider)
        default_model = self.default_model
        if allowlist is not None and default_model not in allowlist:
            default_model = base_grid[0].model_id if base_grid else DEFAULT_MODEL
        if policy is None:
            return EffectiveSettings(
                grid=base_grid,
                confidence_threshold=base_threshold,
                default_model=default_model,
                default_effort=self.default_effort,
                max_effort=None,
                grid_from_policy=False,
            )
        grid = policy.grid if policy.grid is not None else base_grid
        if allowlist is not None and policy.grid is not None:
            grid = _capped(policy.grid, allowlist, base_grid)
        threshold = (
            policy.confidence_threshold
            if policy.confidence_threshold is not None
            else base_threshold
        )
        return EffectiveSettings(
            grid=grid,
            confidence_threshold=threshold,
            default_model=default_model,
            default_effort=self.default_effort,
            max_effort=policy.max_effort,
            grid_from_policy=policy.grid is not None,
        )


def parse_platform_overrides(raw: Any) -> Mapping[str, PlatformPolicy]:
    """Parse ``platform_overrides`` into frozen per-platform policies.

    An entry is kept only when its value is a mapping with at least one recognised key that
    parsed. Anything else — a non-map value, an unknown key, or a value that does not coerce —
    is dropped outright, so "unusable" and "unparseable grid alone" are observably the same
    thing: no policy, the global settings in force. Never raises.

    Returns a read-only ``MappingProxyType``: ``Settings`` is frozen, so the mapping it carries
    must be too, or the snapshot is silently mutable through the field.
    """
    if not isinstance(raw, dict):
        return MappingProxyType({})

    policies: Dict[str, PlatformPolicy] = {}
    for key, value in raw.items():
        platform = str(key or "").strip().lower()
        if not platform or not isinstance(value, dict):
            continue

        enabled = _as_optional_bool(value["enabled"]) if "enabled" in value else None
        threshold = (
            _as_optional_float(value["confidence_threshold"], minimum=0.0)
            if "confidence_threshold" in value
            else None
        )
        max_effort = (
            _as_optional_choice(value["max_effort"], EFFORT_LEVELS) if "max_effort" in value else None
        )
        grid = parse_grid_strict(value["grid"]) if "grid" in value else None

        if enabled is None and threshold is None and max_effort is None and grid is None:
            continue
        policies[platform] = PlatformPolicy(
            enabled=enabled,
            grid=grid,
            confidence_threshold=threshold,
            max_effort=max_effort,
        )
    return MappingProxyType(policies)


def parse_grids(raw: Any) -> Mapping[str, Tuple[Entry, ...]]:
    """Parse per-provider grid overrides."""
    if not isinstance(raw, dict):
        return MappingProxyType({})
    result: Dict[str, Tuple[Entry, ...]] = {}
    for key, value in raw.items():
        provider = str(key or "").strip().lower()
        if not provider:
            continue
        parsed = parse_grid_strict(value)
        if parsed:
            result[provider] = parsed
    return MappingProxyType(result)


def _freeze_effort_spec(spec: Any) -> Any:
    """Freeze an effort map spec into immutable structures."""
    if isinstance(spec, (list, tuple, set)):
        return tuple(str(x).strip().lower() for x in spec if str(x).strip())
    if isinstance(spec, (dict, Mapping)):
        acc_raw = spec.get("accepted", ())
        if isinstance(acc_raw, (list, tuple, set)):
            acc = tuple(str(x).strip().lower() for x in acc_raw if str(x).strip())
        else:
            acc = ()
        ovr_raw = spec.get("overrides", {})
        if isinstance(ovr_raw, (dict, Mapping)):
            ovr = MappingProxyType({
                str(k).strip().lower(): str(v).strip().lower()
                for k, v in ovr_raw.items()
                if str(k).strip() and str(v).strip()
            })
        else:
            ovr = MappingProxyType({})
        return MappingProxyType({"accepted": acc, "overrides": ovr})
    return spec


def parse_effort_maps(raw: Any) -> Mapping[str, Any]:
    """Parse per-provider effort maps into deep immutable structures."""
    if not isinstance(raw, dict):
        return MappingProxyType({})
    result: Dict[str, Any] = {}
    for key, value in raw.items():
        provider = str(key or "").strip().lower()
        if not provider:
            continue
        if isinstance(value, (dict, Mapping)):
            provider_map = {
                str(k).strip().lower(): _freeze_effort_spec(v)
                for k, v in value.items()
                if str(k).strip()
            }
            result[provider] = MappingProxyType(provider_map)
        else:
            result[provider] = MappingProxyType({})
    return MappingProxyType(result)


def parse_confidence_threshold_per_decider(raw: Any) -> Mapping[str, float]:
    """Parse per-decider confidence threshold overrides."""
    if not isinstance(raw, dict):
        return MappingProxyType({})
    result: Dict[str, float] = {}
    for key, value in raw.items():
        decider_name = str(key or "").strip().lower()
        parsed = _as_optional_float(value, minimum=0.0)
        if parsed is not None:
            result[decider_name] = parsed
    return MappingProxyType(result)


def load_settings(get_config: Optional[Callable[..., Any]] = None) -> Settings:
    """Build a :class:`Settings` from ``ctx.get_config`` (or from no config at all)."""
    read = get_config if callable(get_config) else (lambda _key, default=None: default)

    raw_grid = read("grid", None)
    grid = parse_grid(raw_grid) if raw_grid else tuple(DEFAULT_GRID)

    raw_routable = read("routable_providers", None)
    if raw_routable is None:
        routable_providers = DEFAULT_ROUTABLE_PROVIDERS
    elif isinstance(raw_routable, str):
        routable_providers = tuple(p.strip() for p in raw_routable.split(",") if p.strip())
    elif isinstance(raw_routable, Sequence):
        routable_providers = tuple(str(p).strip() for p in raw_routable if str(p).strip())
    else:
        routable_providers = DEFAULT_ROUTABLE_PROVIDERS

    _marker = object()
    raw_console_model = read("console_jev_model", _marker)
    if raw_console_model is _marker or raw_console_model is None:
        console_jev_model = ""
    else:
        console_jev_model = str(raw_console_model).strip()

    raw_fallback = read("console_jev_fallback_model", _marker)
    if raw_fallback is _marker or raw_fallback is None:
        console_jev_fallback = DEFAULT_CONSOLE_JEV_FALLBACK_MODEL
    else:
        txt = str(raw_fallback).strip()
        if txt.lower() in ("none", "false", "off", "disabled") or txt == "":
            console_jev_fallback = ""
        else:
            console_jev_fallback = txt

    settings = Settings(
        enabled=_as_bool(read("enabled", True), True),
        jev_model=_as_text(read("jev_model", DEFAULT_JEV_MODEL), DEFAULT_JEV_MODEL),
        endpoint=_as_text(read("endpoint", DEFAULT_ENDPOINT), DEFAULT_ENDPOINT),
        confidence_threshold=_as_float(
            read("confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD),
            DEFAULT_CONFIDENCE_THRESHOLD,
            minimum=0.0,
        ),
        timeout_s=_as_float(read("timeout_s", DEFAULT_TIMEOUT_S), DEFAULT_TIMEOUT_S, minimum=0.05),
        default_model=_as_text(read("default_model", DEFAULT_MODEL), DEFAULT_MODEL),
        default_effort=_as_choice(read("default_effort", DEFAULT_EFFORT), EFFORT_LEVELS, DEFAULT_EFFORT),
        context_turns=_as_int(
            read("context_turns", DEFAULT_CONTEXT_TURNS), DEFAULT_CONTEXT_TURNS, minimum=0, maximum=50
        ),
        route_per_turn=_as_bool(read("route_per_turn", True), True),
        audit_enabled=_as_bool(read("audit_enabled", True), True),
        log_skips=_as_bool(read("log_skips", True), True),
        include_user_message_in_audit=_as_bool(read("include_user_message_in_audit", False), False),
        grid=grid,
        platform_overrides=parse_platform_overrides(read("platform_overrides", None)),
        decider=_as_choice(read("decider", DEFAULT_DECIDER), DECIDERS, DEFAULT_DECIDER),
        decider_base_url=_as_text(read("decider_base_url", ""), ""),
        decider_model=_as_text(read("decider_model", ""), ""),
        decider_api_key_env=_as_text(read("decider_api_key_env", ""), ""),
        console_endpoint=_as_text(read("console_endpoint", ""), ""),
        console_jev_model=console_jev_model,
        console_jev_fallback_model=console_jev_fallback,
        console_api_key_env=_as_text(read("console_api_key_env", ""), ""),
        routable_providers=routable_providers if routable_providers else DEFAULT_ROUTABLE_PROVIDERS,
        grids=parse_grids(read("grids", None)),
        effort_maps=parse_effort_maps(read("effort_maps", None)),
        confidence_threshold_per_decider=parse_confidence_threshold_per_decider(
            read("confidence_threshold_per_decider", None)
        ),
    )
    return settings


def api_key(env_name: Optional[str] = None) -> str:
    """The API key, from the environment only.

    Defaults to OPENROUTER_API_KEY when env_name is not provided.
    Never read from ``config.yaml`` and never returned anywhere it could be logged.
    """
    key_name = (env_name or "OPENROUTER_API_KEY").strip()
    return (os.environ.get(key_name) or "").strip()


def redact(value: Any, limit: int = 200, env_name: Optional[str] = None) -> str:
    """Render a value for a log line with any credential-looking substring removed."""
    text = str(value)
    keys_to_redact = set()
    if env_name:
        k = api_key(env_name)
        if k:
            keys_to_redact.add(k)
    k_default = api_key("OPENROUTER_API_KEY")
    if k_default:
        keys_to_redact.add(k_default)
    k_opencode = api_key("OPENCODE_GO_API_KEY")
    if k_opencode:
        keys_to_redact.add(k_opencode)
    k_console = api_key("OPENCODE_CONSOLE_API_KEY")
    if k_console:
        keys_to_redact.add(k_console)

    for key in keys_to_redact:
        text = text.replace(key, "[REDACTED]")
    if len(text) > limit:
        text = text[:limit] + "..."
    return text
