"""The ``platform_overrides`` settings side: parse, resolve, and the compatibility backstops.

Every row of the ticket's I/O matrix that is about settings lands here. The router-side rows
(a disabled surface skips, an unlisted surface still routes) live in ``tests/test_router.py``.
"""

from __future__ import annotations

import dataclasses

import pytest

from config import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_CONSOLE_BASE_URL,
    DEFAULT_CONSOLE_JEV_FALLBACK_MODEL,
    DEFAULT_CONSOLE_JEV_MODEL,
    DEFAULT_CONSOLE_KEY_ENV,
    DEFAULT_MODEL,
    EFFORT_LEVELS,
    PlatformPolicy,
    Settings,
    load_settings,
)
from grid import DEFAULT_GRID, Entry


def settings_for(config):
    return load_settings(lambda key, default=None: config.get(key, default))


# -- happy path --------------------------------------------------------------------


def test_a_cron_policy_reads_all_four_keys_and_resolves_once():
    settings = settings_for(
        {
            "platform_overrides": {
                "cron": {
                    "enabled": True,
                    "grid": "glm-5.3: rigorous reasoning",
                    "confidence_threshold": 0.7,
                    "max_effort": "medium",
                }
            }
        }
    )

    policy = settings.policy_for("cron")

    assert policy is not None
    assert policy.enabled is True
    assert policy.grid == (Entry("glm-5.3", "rigorous reasoning"),)
    assert policy.confidence_threshold == 0.7
    assert policy.max_effort == "medium"

    effective = settings.effective(policy)
    assert effective.grid == (Entry("glm-5.3", "rigorous reasoning"),)
    assert effective.confidence_threshold == 0.7
    assert effective.max_effort == "medium"
    # A policy that carried a parsed grid is a policy grid.
    assert effective.grid_from_policy is True
    # The rest of the snapshot is the global setting unchanged.
    assert effective.default_model == settings.default_model
    assert effective.default_effort == settings.default_effort


def test_a_policy_field_may_inherit_and_the_policy_key_is_normalised():
    settings = settings_for({"platform_overrides": {"cron": {"enabled": True}}})

    assert settings.policy_for("CRON") is not None
    policy = settings.policy_for("cron")
    assert policy.enabled is True
    assert policy.grid is None
    assert policy.confidence_threshold is None
    assert policy.max_effort is None

    effective = settings.effective(policy)
    assert effective.grid == settings.grid
    assert effective.confidence_threshold == settings.confidence_threshold
    assert effective.max_effort is None
    # No policy grid was declared, so this is the global grid, not a policy grid.
    assert effective.grid_from_policy is False


# -- unlisted / absent / null / unusable -------------------------------------------


def test_unlisted_platform_has_no_policy():
    settings = settings_for({"platform_overrides": {"cron": {"enabled": True}}})

    assert settings.policy_for("telegram") is None
    assert settings.effective(None).grid == settings.grid
    assert settings.effective(None).grid_from_policy is False


@pytest.mark.parametrize(
    "config",
    [
        {},  # no key
        {"platform_overrides": None},  # null
        {"platform_overrides": []},  # not a map
        {"platform_overrides": {"cron": "off"}},  # value is not a map
        {"platform_overrides": {"cron": {}}},  # map with no recognised key
        {"platform_overrides": {"cron": {"nope": 1}}},  # map with only unknown keys
        {"platform_overrides": {"cron": {"enabled": "banana"}}},  # uncoercible
        {"platform_overrides": {"cron": {"grid": "???"}}},  # grid alone, unparseable
        {"platform_overrides": {"cron": {"grid": 123}}},  # grid alone, unparseable
        {"platform_overrides": {"cron": {"grid": []}}},  # grid alone, empty
    ],
)
def test_absent_null_unlisted_and_unusable_leave_settings_default(config):
    settings = settings_for(config)

    assert settings.platform_overrides == {}
    # Field for field equal to a default-constructed Settings.
    assert settings == Settings()
    for field in dataclasses.fields(Settings):
        assert getattr(settings, field.name) == getattr(Settings(), field.name)


def test_an_unusable_policy_grid_falls_to_the_global_grid_not_the_builtin():
    custom = "custom-model: does the custom thing"
    settings = settings_for(
        {
            "grid": custom,
            "platform_overrides": {"cron": {"grid": "???"}},
        }
    )

    assert settings.grid == (Entry("custom-model", "does the custom thing"),)
    # The entry was dropped, so no policy and the effective grid is the operator's own.
    assert settings.policy_for("cron") is None
    assert settings.effective(None).grid == settings.grid
    assert settings.effective(None).grid != DEFAULT_GRID


# -- an unparseable policy grid alongside a readable key ---------------------------


@pytest.mark.parametrize("bad_grid", [123, [], "", None, "   "])
def test_unparseable_policy_grid_keeps_the_other_keys_and_drops_only_the_grid(bad_grid):
    settings = settings_for(
        {
            "grid": "custom-model: does the custom thing",
            "platform_overrides": {
                "cron": {"enabled": True, "grid": bad_grid, "confidence_threshold": 0.8}
            },
        }
    )

    policy = settings.policy_for("cron")
    assert policy is not None
    assert policy.enabled is True
    assert policy.confidence_threshold == 0.8
    assert policy.grid is None

    effective = settings.effective(policy)
    # The grid falls to the *global* grid, never to parse_grid's built-in six.
    assert effective.grid == settings.grid
    assert effective.grid != DEFAULT_GRID
    # A policy whose grid alone was unparseable is not a policy grid.
    assert effective.grid_from_policy is False


# -- vocabulary --------------------------------------------------------------------


def test_max_effort_is_the_effort_vocabulary_and_rejects_anything_else():
    for level in EFFORT_LEVELS:
        settings = settings_for({"platform_overrides": {"cron": {"max_effort": level}}})
        assert settings.policy_for("cron").max_effort == level

    settings = settings_for({"platform_overrides": {"cron": {"max_effort": "xhigh"}}})
    assert settings.policy_for("cron") is None


def test_confidence_threshold_rejects_a_negative():
    settings = settings_for({"platform_overrides": {"cron": {"confidence_threshold": -0.1}}})

    assert settings.policy_for("cron") is None


def test_platform_policy_is_frozen_and_keeps_none_distinct_from_false():
    policy = PlatformPolicy(enabled=False)
    assert policy.enabled is False

    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.enabled = True  # type: ignore[misc]


def test_the_settings_snapshot_cannot_be_mutated_through_its_mapping():
    settings = settings_for({"platform_overrides": {"cron": {"enabled": True}}})
    assert settings.policy_for("cron") is not None

    with pytest.raises(TypeError):
        settings.platform_overrides["cli"] = PlatformPolicy(enabled=False)  # type: ignore[index]

    # The empty default is immutable too.
    with pytest.raises(TypeError):
        Settings().platform_overrides["cli"] = None  # type: ignore[index]


def test_the_settings_snapshot_stays_hashable():
    settings = settings_for({"platform_overrides": {"cron": {"enabled": True}}})

    assert hash(settings) == hash(settings)
    assert hash(Settings()) == hash(Settings())
    # Equal snapshots hash equal, different ones need not.
    assert len({settings, Settings()}) == 2

    # Nested effort maps stay hashable and immutable
    settings_with_effort = settings_for({
        "effort_maps": {
            "opencode-go": {
                "deepseek-v4.1-flash": ["low", "medium"],
                "kimi-k3": {"accepted": ["low", "high"], "overrides": {"medium": "high"}},
            }
        }
    })
    assert hash(settings_with_effort) == hash(settings_with_effort)
    assert len({settings, Settings(), settings_with_effort}) == 3
    with pytest.raises(TypeError):
        settings_with_effort.effort_maps["opencode-go"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        settings_with_effort.effort_maps["opencode-go"]["deepseek-v4.1-flash"] = ()  # type: ignore[index]
    with pytest.raises(TypeError):
        settings_with_effort.effort_maps["opencode-go"]["kimi-k3"]["accepted"] = ()  # type: ignore[index]

    # Mutating raw input does not affect parsed settings
    input_list = ["low", "medium"]
    input_spec = {"accepted": ["low", "high"], "overrides": {"medium": "high"}}
    raw_maps = {"opencode-go": {"deepseek-v4.1-flash": input_list, "kimi-k3": input_spec}}
    settings_copy = settings_for({"effort_maps": raw_maps})
    input_list.append("high")
    input_spec["accepted"].append("extra")
    assert settings_copy.effort_maps["opencode-go"]["deepseek-v4.1-flash"] == ("low", "medium")
    assert settings_copy.effort_maps["opencode-go"]["kimi-k3"]["accepted"] == ("low", "high")


def test_defaults_are_unchanged_when_the_key_is_absent():
    settings = settings_for({})

    assert settings.confidence_threshold == DEFAULT_CONFIDENCE_THRESHOLD
    assert settings.default_model == DEFAULT_MODEL
    assert settings.decider == "jev"
    assert settings.decider_base_url == ""
    assert settings.decider_model == ""
    assert settings.decider_api_key_env == ""
    assert settings.console_endpoint == ""
    assert settings.console_jev_model == ""
    assert settings.console_jev_fallback_model == DEFAULT_CONSOLE_JEV_FALLBACK_MODEL
    assert settings.console_api_key_env == ""
    assert settings.base_url_for("jev_console") == DEFAULT_CONSOLE_BASE_URL
    assert settings.decider_model_for("jev_console") == DEFAULT_CONSOLE_JEV_MODEL
    assert settings.api_key_env_for("jev_console") == DEFAULT_CONSOLE_KEY_ENV
    assert settings.routable_providers == ("ollama-cloud",)
    assert len(settings.grids) == 0
    assert len(settings.effort_maps) == 0
    assert len(settings.confidence_threshold_per_decider) == 0
    assert settings.is_provider_routable("ollama-cloud") is True
    assert settings.is_provider_routable("opencode-go") is False


def test_new_config_options_parsed_and_queried():
    config = {
        "decider": "openai_compatible",
        "decider_base_url": "https://opencode.ai/zen/go/v1",
        "decider_model": "deepseek-v4.1-flash",
        "decider_api_key_env": "OPENCODE_GO_API_KEY",
        "routable_providers": ["ollama-cloud", "opencode-go"],
        "confidence_threshold_per_decider": {"openai_compatible": 0.8, "jev": 0.5},
    }
    settings = settings_for(config)

    assert settings.decider == "openai_compatible"
    assert settings.decider_base_url == "https://opencode.ai/zen/go/v1"
    assert settings.decider_model == "deepseek-v4.1-flash"
    assert settings.decider_api_key_env == "OPENCODE_GO_API_KEY"
    assert settings.routable_providers == ("ollama-cloud", "opencode-go")
    assert settings.is_provider_routable("ollama-cloud") is True
    assert settings.is_provider_routable("opencode-go") is True
    assert settings.is_provider_routable("openrouter") is False
    assert settings.api_key_env_for("openai_compatible") == "OPENCODE_GO_API_KEY"
    assert settings.api_key_env_for("jev") == "OPENROUTER_API_KEY"
    assert settings.confidence_threshold_for("openai_compatible") == 0.8
    assert settings.confidence_threshold_for("jev") == 0.5


def test_console_config_options_parsed_and_queried():
    config = {
        "decider": "jev_console",
        "console_endpoint": "https://custom.opencode.ai/v1",
        "console_jev_model": "custom-jev-free",
        "console_jev_fallback_model": "custom-jev-paid",
        "console_api_key_env": "CUSTOM_CONSOLE_KEY",
        "routable_providers": ["ollama-cloud", "opencode-console"],
    }
    settings = settings_for(config)

    assert settings.decider == "jev_console"
    assert settings.console_endpoint == "https://custom.opencode.ai/v1"
    assert settings.console_jev_model == "custom-jev-free"
    assert settings.console_jev_fallback_model == "custom-jev-paid"
    assert settings.console_api_key_env == "CUSTOM_CONSOLE_KEY"
    assert settings.base_url_for("jev_console") == "https://custom.opencode.ai/v1"
    assert settings.decider_model_for("jev_console") == "custom-jev-free"
    assert settings.api_key_env_for("jev_console") == "CUSTOM_CONSOLE_KEY"
    assert settings.is_provider_routable("opencode-console") is True
    assert settings.is_provider_routable("opencode_console") is True


def test_opencode_console_grid_resolution():
    settings = settings_for({})
    grid = settings.grid_for("opencode-console")
    # Console serves only the normal generation id its own catalog lists; normal MiMo is Go-only.
    assert [e.model_id for e in grid] == ["deepseek-v4.1-flash"]
    assert settings.grid_for("opencode_console") == grid


def test_opencode_go_grid_is_the_single_model_catalog_under_the_console_decider():
    settings = settings_for({"decider": "jev_console"})
    for provider in ("opencode-go", "opencode_go"):
        assert [e.model_id for e in settings.grid_for(provider)] == ["deepseek-v4.1-flash"]
        effective = settings.effective(None, provider=provider)
        assert [e.model_id for e in effective.grid] == ["deepseek-v4.1-flash"]
        # No legacy Ollama model can leak into the opt-in Go route.
        assert "kimi-k3" not in [e.model_id for e in effective.grid]


def test_opencode_go_grid_cap_applies_to_operator_grids_without_widening_them():
    settings = settings_for(
        {
            "decider": "jev_console",
            "grids": {
                "opencode-go": [
                    "deepseek-v4.1-flash: routine",
                    "unauthorized-model: invented",
                ],
            },
        }
    )
    assert [e.model_id for e in settings.grid_for("opencode-go")] == ["deepseek-v4.1-flash"]

    # A Go grid with nothing authorized at all falls back to the built-in one-model catalog.
    only_outsiders = settings_for(
        {"decider": "jev_console", "grids": {"opencode-go": ["glm-5-free: invented"]}}
    )
    assert [e.model_id for e in only_outsiders.grid_for("opencode-go")] == ["deepseek-v4.1-flash"]

    # A surface policy grid cannot widen the catalog either.
    policy_settings = settings_for(
        {
            "decider": "jev_console",
            "platform_overrides": {
                "cli": {"grid": ["deepseek-v4.1-flash: a", "glm-5-free: b"]},
            },
        }
    )
    policy = policy_settings.effective(policy_settings.policy_for("cli"), provider="opencode-go")
    assert [e.model_id for e in policy.grid] == ["deepseek-v4.1-flash"]


def test_opencode_go_grid_untouched_for_legacy_deciders():
    settings = settings_for(
        {"grids": {"opencode-go": ["deepseek-v4.1-flash: fast", "kimi-k3: code"]}}
    )
    assert [e.model_id for e in settings.grid_for("opencode-go")] == [
        "deepseek-v4.1-flash",
        "kimi-k3",
    ]
    # ... and with no configured grid at all, a legacy decider keeps the global grid.
    bare = settings_for({})
    assert [e.model_id for e in bare.grid_for("opencode-go")] == [
        e.model_id for e in bare.grid
    ]


def test_commandcode_grid_is_the_single_namespaced_catalog_under_the_console_decider():
    settings = settings_for({"decider": "jev_console"})
    for provider in ("commandcode", "commandcode-chat", "commandcode_chat"):
        assert [e.model_id for e in settings.grid_for(provider)] == [
            "deepseek/deepseek-v4.1-flash"
        ]
        effective = settings.effective(None, provider=provider)
        assert [e.model_id for e in effective.grid] == ["deepseek/deepseek-v4.1-flash"]
        assert effective.default_model == "deepseek/deepseek-v4.1-flash"
        # The bare OpenCode id is a different contract and cannot enter this route.
        assert "deepseek-v4.1-flash" not in [e.model_id for e in effective.grid]


def test_commandcode_grid_cap_applies_to_operator_grids_without_widening_them():
    settings = settings_for(
        {
            "decider": "jev_console",
            "grids": {
                "commandcode": [
                    "deepseek/deepseek-v4.1-flash: routine",
                    "deepseek-v4.1-flash: bare OpenCode id",
                    "deepseek/deepseek-v4.1-flash-fast: not authorized",
                ],
            },
        }
    )
    # The alias resolves to the same configured grid, and only the authorized id survives.
    assert [e.model_id for e in settings.grid_for("commandcode-chat")] == [
        "deepseek/deepseek-v4.1-flash"
    ]

    # A grid with nothing authorized falls back to the built-in one-model catalog.
    only_outsiders = settings_for(
        {"decider": "jev_console", "grids": {"commandcode": ["glm-5-free: invented"]}}
    )
    assert [e.model_id for e in only_outsiders.grid_for("commandcode")] == [
        "deepseek/deepseek-v4.1-flash"
    ]

    # A surface policy grid cannot widen the catalog either.
    policy_settings = settings_for(
        {
            "decider": "jev_console",
            "platform_overrides": {
                "cli": {"grid": ["deepseek/deepseek-v4.1-flash: a", "kimi-k3: b"]},
            },
        }
    )
    policy = policy_settings.effective(
        policy_settings.policy_for("cli"), provider="commandcode"
    )
    assert [e.model_id for e in policy.grid] == ["deepseek/deepseek-v4.1-flash"]


def test_commandcode_grid_untouched_for_legacy_deciders():
    settings = settings_for(
        {
            "grids": {
                "commandcode": [
                    "deepseek/deepseek-v4.1-flash: fast",
                    "kimi-k3: code",
                ]
            }
        }
    )
    assert [e.model_id for e in settings.grid_for("commandcode")] == [
        "deepseek/deepseek-v4.1-flash",
        "kimi-k3",
    ]
    # ... and with no configured grid at all, a legacy decider keeps the global grid.
    bare = settings_for({})
    assert [e.model_id for e in bare.grid_for("commandcode")] == [
        e.model_id for e in bare.grid
    ]


def test_codex_grid_is_the_three_model_catalog_under_the_console_decider():
    settings = settings_for({"decider": "jev_console"})
    for provider in ("openai-codex", "codex", "openai_codex"):
        assert [e.model_id for e in settings.grid_for(provider)] == [
            "gpt-6-luna",
            "gpt-6-luna-900k",
            "gpt-6.1-sol",
        ]
        effective = settings.effective(None, provider=provider)
        assert [e.model_id for e in effective.grid] == [
            "gpt-6-luna",
            "gpt-6-luna-900k",
            "gpt-6.1-sol",
        ]
        # The route's own first entry becomes the configured default, so an off-catalog
        # ``default_model`` cannot pin a model this route does not serve.
        assert effective.default_model == "gpt-6-luna"


def test_codex_picker_alias_knows_the_wire_id_the_backend_serves():
    """``-900k`` is a Hermes-side suffix; only an eligible base can carry it on the wire."""
    settings = settings_for({"decider": "jev_console"})
    grid = {entry.model_id: entry for entry in settings.grid_for("openai-codex")}
    assert grid["gpt-6-luna"].wire_model == "gpt-6-luna"
    assert grid["gpt-6-luna-900k"].wire_model == "gpt-6-luna"
    # ``gpt-6.1-sol-900k`` is not a variant this backend serves (HTTP 400 "model entitlement")
    # and not an eligible base in the core's alias table, so the route does not carry it at all:
    # a catalog entry the endpoint rejects is the dead-turn bug the catalog exists to prevent.
    assert "gpt-6.1-sol-900k" not in grid


def test_codex_grid_cap_applies_to_operator_grids_without_widening_them():
    settings = settings_for(
        {
            "decider": "jev_console",
            "grids": {
                "openai-codex": [
                    "gpt-6-luna: routine",
                    "gpt-6.1-sol: hard",
                    "gpt-6-astra: not authorized",
                    "gpt-5.6-sol: not authorized",
                ],
            },
        }
    )
    # The alias resolves to the same configured grid, and only catalog ids survive.
    assert [e.model_id for e in settings.grid_for("codex")] == ["gpt-6-luna", "gpt-6.1-sol"]

    # A grid with nothing authorized falls back to the built-in catalog.
    from grid import CODEX_GRID

    only_outsiders = settings_for(
        {"decider": "jev_console", "grids": {"openai-codex": ["gpt-6-astra: invented"]}}
    )
    assert [e.model_id for e in only_outsiders.grid_for("openai-codex")] == [
        entry.model_id for entry in CODEX_GRID
    ]


def test_codex_grid_untouched_for_legacy_deciders():
    settings = settings_for(
        {"grids": {"openai-codex": ["gpt-6-luna: routine", "kimi-k3: code"]}}
    )
    assert [e.model_id for e in settings.grid_for("openai-codex")] == [
        "gpt-6-luna",
        "kimi-k3",
    ]
    # ... and with no configured grid at all, a legacy decider keeps the global grid.
    bare = settings_for({})
    assert [e.model_id for e in bare.grid_for("openai-codex")] == [
        e.model_id for e in bare.grid
    ]


def test_is_provider_routable_normalises_every_route_alias():
    """A canonical name and any alias of the same route match in both directions."""
    pairs = (
        ("ollama-cloud", "ollama_cloud"),
        ("ollama_cloud", "ollama-cloud"),
        ("opencode-console", "opencode_console"),
        ("opencode-console", "opencode-console"),
        ("opencode-go", "opencode_go"),
        ("opencode-go", "opencode-go"),
        ("commandcode", "commandcode-chat"),
        ("commandcode", "commandcode_chat"),
        ("commandcode-chat", "commandcode"),
        ("openai-codex", "codex"),
        ("openai-codex", "openai_codex"),
        ("codex", "openai-codex"),
        ("openai_codex", "codex"),
    )
    for declared, queried in pairs:
        assert settings_for({"routable_providers": [declared]}).is_provider_routable(
            queried
        ) is True, (declared, queried)

    # The opencode-go asymmetry this replaced: a declared canonical name never matched its
    # underscore alias, so the route was skipped silently.
    assert settings_for({"routable_providers": ["opencode-go"]}).is_provider_routable(
        "opencode_go"
    ) is True

    assert settings_for({}).is_provider_routable("commandcode") is False
    assert settings_for({}).is_provider_routable("openrouter") is False


def _load_schema_defaults():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    manifest_path = root / "plugin.yaml"
    text = manifest_path.read_text(encoding="utf-8")
    defaults = {}
    in_schema = False
    current_key = None
    for line in text.splitlines():
        if line.startswith("config_schema:"):
            in_schema = True
            continue
        if in_schema:
            if line and not line.startswith(" "):
                break
            if line.startswith("  ") and not line.startswith("    "):
                key = line.strip().rstrip(":")
                if key:
                    current_key = key
            elif current_key and line.startswith("    default:"):
                val_str = line.split(":", 1)[1].strip()
                if val_str.startswith('"') and val_str.endswith('"'):
                    defaults[current_key] = val_str[1:-1]
                elif val_str == "true":
                    defaults[current_key] = True
                elif val_str == "false":
                    defaults[current_key] = False
                elif val_str == "null":
                    defaults[current_key] = None
                elif val_str.startswith("[") and val_str.endswith("]"):
                    items = [x.strip().strip("'\"") for x in val_str[1:-1].split(",") if x.strip()]
                    defaults[current_key] = items
                else:
                    try:
                        defaults[current_key] = int(val_str)
                    except ValueError:
                        try:
                            defaults[current_key] = float(val_str)
                        except ValueError:
                            defaults[current_key] = val_str
    return defaults


def test_materialized_schema_defaults():
    defaults = _load_schema_defaults()
    assert defaults["console_jev_model"] == "jev-1.13-free"
    assert defaults["console_jev_fallback_model"] == "jev-1.13"
    assert defaults["decider"] == "jev"

    settings = settings_for(defaults)
    assert settings.decider == "jev"
    assert settings.console_jev_model == "jev-1.13-free"
    assert settings.console_jev_fallback_model == "jev-1.13"
    assert settings.decider_model_for("jev_console") == "jev-1.13-free"
    assert settings.base_url_for("jev_console") == "https://opencode.ai/inference/systemone/v1/systemone"
    assert settings.api_key_env_for("jev_console") == "OPENCODE_CONSOLE_API_KEY"
    assert settings.is_provider_routable("opencode-console") is False
    assert settings.is_provider_routable("ollama-cloud") is True

