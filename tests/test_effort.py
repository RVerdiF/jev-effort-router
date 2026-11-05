"""Effort translation per model family."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from config import load_settings
from effort import (
    ModelReasoningPolicy,
    OPENCODE_CONSOLE_REASONING_POLICIES,
    clamp,
    family_for,
    is_omitted,
    model_reasoning_policy,
    resolve_effort,
)


def test_family_detection():
    assert family_for("kimi-k3").name == "kimi-k3"
    assert family_for("kimi-k3-256k").name == "kimi-k3"
    assert family_for("vendor/kimi-k3").name == "kimi-k3"
    assert family_for("kimi-k2.6").name == "ollama-cloud"
    assert family_for("deepseek-v4.1-flash").name == "deepseek"
    assert family_for("glm-5.3").name == "glm-53"
    assert family_for("glm-5.3-flash").name == "glm-53"
    assert family_for("glm-5.2").name == "glm-52"
    assert family_for("minimax-m3").name == "minimax"
    assert family_for("nemotron-3-nano:30b").name == "nemotron"
    assert family_for("mystery-model").name == "ollama-cloud"
    assert family_for("").name == "ollama-cloud"


def test_deepseek_passes_the_three_levels_through():
    assert resolve_effort("deepseek-v4.1-flash", "low") == "low"
    assert resolve_effort("deepseek-v4.1-flash", "medium") == "medium"
    assert resolve_effort("deepseek-v4.1-flash", "high") == "high"


def test_kimi_k3_medium_lands_on_high_via_the_declared_override():
    # K3's documented set is low/high/max and `high` is its positional middle.
    assert resolve_effort("kimi-k3", "low") == "low"
    assert resolve_effort("kimi-k3", "medium") == "high"
    assert resolve_effort("kimi-k3", "high") == "high"


def test_glm53_is_graded():
    for level in ("low", "medium", "high"):
        assert resolve_effort("glm-5.3", level) == level
        assert resolve_effort("glm-5.3-flash", level) == level


def test_glm52_never_escalates_below_its_floor():
    # The older knob only has high and max; every request lands on high, never on a
    # level the route would reject.
    assert resolve_effort("glm-5.2", "low") == "high"
    assert resolve_effort("glm-5.2", "medium") == "high"
    assert resolve_effort("glm-5.2", "high") == "high"


def test_ollama_cloud_family_accepts_the_three_levels():
    assert resolve_effort("minimax-m3", "high") == "high"
    assert resolve_effort("nemotron-3-nano", "low") == "low"
    assert resolve_effort("mystery-model", "medium") == "medium"


def test_clamp_never_escalates():
    assert clamp("minimal", ("low", "medium", "high")) == "low"
    assert clamp("xhigh", ("low", "medium", "high")) == "high"
    assert clamp("high", ("low", "medium")) == "medium"
    assert clamp("low", ("high", "max")) == "high"


def test_clamp_prefers_a_declared_override():
    assert clamp("medium", ("low", "high"), {"medium": "high"}) == "high"
    assert clamp("xhigh", ("low", "max"), {"xhigh": "max"}) == "max"


def test_clamp_never_lands_on_none():
    assert clamp("low", ("none", "medium")) == "medium"


def test_clamp_passes_a_bespoke_vocabulary_through():
    assert clamp("thinking", ("low", "high")) == "thinking"


def test_empty_effort_means_omit():
    assert resolve_effort("deepseek-v4.1-flash", None) is None
    assert resolve_effort("deepseek-v4.1-flash", "") is None
    assert is_omitted("deepseek-v4.1-flash", "")
    assert not is_omitted("deepseek-v4.1-flash", "medium")


def test_unmapped_provider_defaults_to_omitting_effort():
    # A provider with no policy and no operator map never gets a guessed field.
    assert resolve_effort("deepseek-v4.1-flash", "medium", provider="openrouter") is None
    assert is_omitted("deepseek-v4.1-flash", "medium", provider="openrouter")


def test_per_provider_effort_maps_declare_the_vocabulary():
    effort_maps = {
        "opencode-go": {
            "deepseek-v4.1-flash": ["low", "medium"],
            "kimi-k3": {"accepted": ["low", "high"], "overrides": {"medium": "high"}},
        }
    }
    # A declared level goes out verbatim; one the map does not declare is omitted, not clamped.
    assert (
        resolve_effort(
            "deepseek-v4.1-flash",
            "low",
            provider="opencode-go",
            effort_maps=effort_maps,
        )
        == "low"
    )
    assert (
        resolve_effort(
            "deepseek-v4.1-flash",
            "high",
            provider="opencode-go",
            effort_maps=effort_maps,
        )
        is None
    )
    # `medium` is not accepted, but the override that carries it to `high` is written down.
    assert (
        resolve_effort(
            "kimi-k3",
            "medium",
            provider="opencode-go",
            effort_maps=effort_maps,
        )
        == "high"
    )
    # A model the map does not mention keeps the route's built-in contract: the Go catalog does
    # not serve it, so the field is omitted rather than guessed from another wire.
    assert (
        resolve_effort(
            "unknown-model",
            "medium",
            provider="opencode-go",
            effort_maps=effort_maps,
        )
        is None
    )


def test_effort_maps_use_one_declarative_rule():
    """Membership, then a declared override with an accepted target, then omission."""
    high_only = {"opencode-go": {"m": ["high"]}}
    # No nearest-weaker fallback and no escalation to the floor.
    assert resolve_effort("m", "low", provider="opencode-go", effort_maps=high_only) is None
    assert resolve_effort("m", "medium", provider="opencode-go", effort_maps=high_only) is None
    assert resolve_effort("m", "high", provider="opencode-go", effort_maps=high_only) == "high"

    # Empty, null and non-sequence specs omit instead of revealing the built-in policy.
    for spec in ([], {"accepted": []}, None, "low", 3, True, {"accepted": "low"}):
        assert (
            resolve_effort(
                "m", "low", provider="opencode-go", effort_maps={"opencode-go": {"m": spec}}
            )
            is None
        )

    # An ascending override is honoured when its target is accepted — it is declared, not inferred.
    ascending = {"opencode-go": {"m": {"accepted": ["low", "high"], "overrides": {"medium": "high"}}}}
    assert resolve_effort("m", "medium", provider="opencode-go", effort_maps=ascending) == "high"

    # A target outside `accepted` is ignored, and overrides are never chained.
    outside = {
        "opencode-go": {"m": {"accepted": ["low"], "overrides": {"medium": "high", "high": "max"}}}
    }
    assert resolve_effort("m", "medium", provider="opencode-go", effort_maps=outside) is None
    assert resolve_effort("m", "high", provider="opencode-go", effort_maps=outside) is None
    chained = {
        "opencode-go": {
            "m": {"accepted": ["low", "max"], "overrides": {"medium": "high", "high": "max"}}
        }
    }
    assert resolve_effort("m", "medium", provider="opencode-go", effort_maps=chained) is None

    # An accepted level always wins, even when an override names another accepted one.
    conflict = {
        "opencode-go": {"m": {"accepted": ["low", "high"], "overrides": {"high": "low"}}}
    }
    assert resolve_effort("m", "high", provider="opencode-go", effort_maps=conflict) == "high"

    # A vocabulary the core's ladder has never heard of is legal when it is declared.
    bespoke = {"opencode-go": {"m": {"accepted": ["careful"], "overrides": {"xhigh": "careful"}}}}
    assert resolve_effort("m", "careful", provider="opencode-go", effort_maps=bespoke) == "careful"
    assert resolve_effort("m", "xhigh", provider="opencode-go", effort_maps=bespoke) == "careful"
    assert resolve_effort("m", "high", provider="opencode-go", effort_maps=bespoke) is None


def test_none_string_is_a_declared_level_while_python_none_omits():
    maps = {"p": {"m": {"accepted": ["none"]}}}
    assert resolve_effort("m", "none", provider="p", effort_maps=maps) == "none"
    assert is_omitted("m", "none", provider="p", effort_maps=maps) is False
    policy = model_reasoning_policy("m", provider="p", effort_maps=maps)
    assert policy.omitted is False
    assert policy.accepted_levels == ("none",)

    # A missing request is omission, never the level `none`.
    assert resolve_effort("m", None, provider="p", effort_maps=maps) is None
    assert resolve_effort("m", "", provider="p", effort_maps=maps) is None
    assert resolve_effort("m", "   ", provider="p", effort_maps=maps) is None

    # Nulls, numbers and booleans in the declaration are not levels either.
    nulls = {"p": {"m": {"accepted": [None, 3, True, ""], "overrides": {None: "none"}}}}
    assert resolve_effort("m", "none", provider="p", effort_maps=nulls) is None
    assert model_reasoning_policy("m", provider="p", effort_maps=nulls).omitted is True


def test_opencode_console_explicit_reasoning_policies():
    # The one normal generation model of the Console catalog has an explicit omission policy.
    for provider_name in ("opencode-console", "opencode_console"):
        for model_id in ("deepseek-v4.1-flash",):
            pol = model_reasoning_policy(model_id, provider=provider_name)
            assert isinstance(pol, ModelReasoningPolicy)
            assert pol.provider == "opencode-console"
            assert pol.model_id == model_id
            assert pol.wire_parameter is None
            assert pol.accepted_levels == ()
            assert pol.omitted is True
            assert "Unconfirmed" in pol.evidence
            assert "cannot be inferred" in pol.evidence or "no documented reasoning" in pol.evidence
            assert "Explicit omission" in pol.decision
            assert "effort_requested" in pol.decision

            # Every effort level resolves to None (omitted on wire)
            for lvl in ("low", "medium", "high"):
                assert resolve_effort(model_id, lvl, provider=provider_name) is None
                assert is_omitted(model_id, lvl, provider=provider_name) is True


def test_opencode_go_explicit_reasoning_policies():
    """The Go generation model carries the core's DeepSeek V4 reasoning contract."""
    for provider_name in ("opencode-go", "opencode_go"):
        for model_id in ("deepseek-v4.1-flash",):
            pol = model_reasoning_policy(model_id, provider=provider_name)
            assert pol.provider == "opencode-go"
            assert pol.model_id == model_id
            assert pol.wire_parameter == "reasoning_effort"
            # The core's DEEPSEEK_V4_EFFORTS / DEEPSEEK_V4_OVERRIDES, not a narrower guess.
            assert pol.accepted_levels == ("low", "medium", "high", "max")
            assert dict(pol.overrides) == {"xhigh": "max"}
            assert pol.omitted is False
            # The evidence names the real probe, the core contract it mirrors, and the limit of it.
            assert "POST https://opencode.ai/zen/go/v1/chat/completions" in pol.evidence
            assert "x-opencode-session" in pol.evidence
            assert "2026-10-20" in pol.evidence
            assert "HTTP 200" in pol.evidence
            assert "reasoning_content" in pol.evidence
            assert "usage.completion_tokens_details.reasoning_tokens" in pol.evidence
            assert "thinking=high" in pol.evidence
            assert "HTTP 400" in pol.evidence
            assert "DEEPSEEK_V4_EFFORTS" in pol.evidence
            assert "DEEPSEEK_V4_OVERRIDES" in pol.evidence
            assert "does not validate the value" in pol.evidence
            assert "unconfirmed" not in pol.evidence
            assert "No documented" not in pol.evidence
            assert "reasoning_effort" in pol.decision
            assert "xhigh" in pol.decision
            assert "HTTP 200" in pol.decision

            # Every declared level goes out verbatim, `max` included.
            for lvl in ("low", "medium", "high", "max"):
                assert resolve_effort(model_id, lvl, provider=provider_name) == lvl
                assert is_omitted(model_id, lvl, provider=provider_name) is False

    # `xhigh` asks for the top tier, exactly like the core's declared override.
    assert resolve_effort("deepseek-v4.1-flash", "xhigh", provider="opencode-go") == "max"
    assert is_omitted("deepseek-v4.1-flash", "xhigh", provider="opencode-go") is False

    # No invented vocabulary and no clamp: anything the policy does not declare is omitted.
    for lvl in ("minimal", "none", "bogus", "ultra", ""):
        assert resolve_effort("deepseek-v4.1-flash", lvl, provider="opencode-go") is None
        assert is_omitted("deepseek-v4.1-flash", lvl, provider="opencode-go") is True

    # A model outside the Go catalog is omitted too, never guessed from its Ollama family.
    outsider = model_reasoning_policy("kimi-k3", provider="opencode-go")
    assert outsider.omitted is True
    assert resolve_effort("kimi-k3", "high", provider="opencode-go") is None


def test_declared_override_cannot_invent_a_level_outside_the_policy(monkeypatch):
    """An override maps onto a declared level; it is never a way to smuggle a new one out."""
    import effort as effort_module

    policy = ModelReasoningPolicy(
        provider="opencode-go",
        model_id="deepseek-v4.1-flash",
        wire_parameter="reasoning_effort",
        accepted_levels=("low", "medium"),
        evidence="test fixture",
        omitted=False,
        decision="test fixture",
        overrides={"xhigh": "ultra", "high": "medium"},
    )
    monkeypatch.setitem(effort_module.OPENCODE_GO_REASONING_POLICIES, "deepseek-v4.1-flash", policy)

    # The override target is undeclared, so nothing is invented or escalated.
    assert resolve_effort("deepseek-v4.1-flash", "xhigh", provider="opencode-go") is None
    # A declared target is honoured, including a benign de-escalation.
    assert resolve_effort("deepseek-v4.1-flash", "high", provider="opencode-go") == "medium"


def test_operator_effort_map_replaces_the_builtin_vocabulary():
    """An operator map is authoritative: it replaces the built-in set instead of merging."""
    maps = {"opencode-go": {"deepseek-v4.1-flash": {"accepted": ["low"]}}}
    pol = model_reasoning_policy("deepseek-v4.1-flash", provider="opencode-go", effort_maps=maps)
    assert pol.wire_parameter == "reasoning_effort"
    assert pol.omitted is False
    assert pol.accepted_levels == ("low",)
    assert dict(pol.overrides) == {}
    assert "operator" in pol.evidence.lower()
    assert "settings.effort_maps" in pol.evidence
    # `high` is outside the declared set, and the built-in `xhigh -> max` did not survive the
    # replacement: both are omitted rather than clamped onto `low` or mapped by a hidden override.
    assert (
        resolve_effort("deepseek-v4.1-flash", "high", provider="opencode-go", effort_maps=maps)
        is None
    )
    assert (
        resolve_effort("deepseek-v4.1-flash", "xhigh", provider="opencode-go", effort_maps=maps)
        is None
    )
    assert (
        resolve_effort("deepseek-v4.1-flash", "low", provider="opencode-go", effort_maps=maps)
        == "low"
    )


def test_a_partial_map_keeps_the_builtin_contract_for_unmentioned_models():
    """A provider map that mentions one model does not disable the route for the others."""
    maps = {"openai-codex": {"gpt-6-luna": {"accepted": ["low"]}}}
    # The mentioned model is restricted, and its `none` from the built-in set is gone.
    assert resolve_effort("gpt-6-luna", "none", provider="openai-codex", effort_maps=maps) is None
    assert resolve_effort("gpt-6-luna", "low", provider="openai-codex", effort_maps=maps) == "low"
    # The prefix rule means the `-900k` picker alias inherits that same entry, deliberately.
    assert resolve_effort("gpt-6-luna-900k", "low", provider="openai-codex", effort_maps=maps) == "low"
    assert resolve_effort("gpt-6-luna-900k", "none", provider="openai-codex", effort_maps=maps) is None
    # A model the map does not cover at all keeps the built-in policy, `gpt-6.1-sol` without `none`.
    assert resolve_effort("gpt-6.1-sol", "high", provider="openai-codex", effort_maps=maps) == "high"
    assert resolve_effort("gpt-6.1-sol", "none", provider="openai-codex", effort_maps=maps) is None


def test_a_map_on_a_new_provider_is_not_ignored():
    """A provider with no built-in policy is exactly what a map is for."""
    maps = {"acme": {"chat-1": {"accepted": ["low", "medium"]}}}
    pol = model_reasoning_policy("chat-1", provider="acme", effort_maps=maps)
    assert pol.omitted is False
    assert pol.wire_parameter == "reasoning_effort"
    assert resolve_effort("chat-1", "low", provider="acme", effort_maps=maps) == "low"
    assert resolve_effort("chat-1", "high", provider="acme", effort_maps=maps) is None
    # Without the declaration the same route is unmapped and omits everything.
    assert resolve_effort("chat-1", "low", provider="acme") is None
    assert model_reasoning_policy("chat-1", provider="acme").omitted is True


def test_a_restrictive_map_on_a_builtin_provider_is_not_ignored():
    """The built-in policy would answer `high`; the operator's map says it may not."""
    maps = {"opencode-console": {"deepseek-v4.1-flash": {"accepted": ["low"]}}}
    assert resolve_effort("deepseek-v4.1-flash", "low", provider="opencode-console", effort_maps=maps) == "low"
    assert resolve_effort("deepseek-v4.1-flash", "high", provider="opencode-console", effort_maps=maps) is None
    # The built-in policy for this route is an omission; the map is what makes a level possible.
    assert resolve_effort("deepseek-v4.1-flash", "low", provider="opencode-console") is None


def test_effort_maps_prefer_the_exact_model_over_a_prefix():
    """`gpt-6-luna-900k` has its own vocabulary and keeps it, in either insertion order."""
    specific = {"accepted": ["careful"]}
    broad = {"accepted": ["low", "high"]}
    for order in (
        {"gpt-6-luna": broad, "gpt-6-luna-900k": specific},
        {"gpt-6-luna-900k": specific, "gpt-6-luna": broad},
    ):
        maps = {"openai-codex": order}
        assert (
            resolve_effort("gpt-6-luna-900k", "careful", provider="openai-codex", effort_maps=maps)
            == "careful"
        )
        # The prefix entry's vocabulary is not mixed in.
        assert resolve_effort("gpt-6-luna-900k", "low", provider="openai-codex", effort_maps=maps) is None
        assert resolve_effort("gpt-6-luna", "low", provider="openai-codex", effort_maps=maps) == "low"


def test_effort_maps_require_a_real_prefix_boundary():
    """Only `-`-delimited continuations match; a key merely contained in the id does not."""
    # `luna` and `6-luna` are *inside* `gpt-6-luna`; neither starts it, so neither may claim it.
    assert resolve_effort("gpt-6-luna", "low", provider="p", effort_maps={"p": {"luna": ["low"]}}) is None
    assert (
        resolve_effort("gpt-6-luna-900k", "low", provider="p", effort_maps={"p": {"6-luna": ["low"]}})
        is None
    )
    # A true prefix — the id continues with `-` — does match.
    assert (
        resolve_effort("gpt-6-luna-900k", "low", provider="p", effort_maps={"p": {"gpt-6-luna": ["low"]}})
        == "low"
    )

    # The longest true prefix wins when several could apply.
    longest = {
        "p": {
            "gpt-6": {"accepted": ["low"]},
            "gpt-6-luna": {"accepted": ["medium"]},
        }
    }
    assert resolve_effort("gpt-6-luna-900k", "medium", provider="p", effort_maps=longest) == "medium"
    assert resolve_effort("gpt-6-luna-900k", "low", provider="p", effort_maps=longest) is None


def test_effort_map_keys_cannot_cross_namespaces():
    """A qualified key is compared with the full id, a bare one with the slug — never both."""
    from effort import select_model_spec

    qualified = {"deepseek/deepseek-v4.1-flash": ["low"]}
    assert select_model_spec(qualified, "deepseek/deepseek-v4.1-flash")[0] is True
    # The namespaced key does not claim the bare slug...
    assert select_model_spec({"deepseek/deepseek-v4.1-flash": ["low"]}, "deepseek-v4.1-flash") == (False, None)
    # ...while a namespace-free key follows the slug, whichever namespace the id came from.
    assert select_model_spec({"deepseek-v4.1-flash": ["low"]}, "other/deepseek-v4.1-flash")[0] is True
    # A different namespace's qualified key never matches.
    assert select_model_spec({"other/deepseek-v4.1-flash": ["low"]}, "deepseek/deepseek-v4.1-flash") == (False, None)


def test_an_empty_map_entry_shadows_the_builtin_instead_of_missing_it():
    from effort import select_model_spec

    assert select_model_spec({"gpt-6-luna": None}, "gpt-6-luna") == (True, None)
    assert select_model_spec({"gpt-6-luna": []}, "gpt-6-luna-900k")[0] is True
    for spec in (None, [], {}, {"accepted": []}):
        maps = {"openai-codex": {"gpt-6-luna": spec}}
        assert resolve_effort("gpt-6-luna", "high", provider="openai-codex", effort_maps=maps) is None
        assert model_reasoning_policy("gpt-6-luna", provider="openai-codex", effort_maps=maps).omitted is True


def test_provider_aliases_resolve_to_the_canonical_map_without_escaping_it():
    """The alias lookup reuses ``config._GRID_ALIASES``; it never bypasses the restriction."""
    maps = {"openai-codex": {"gpt-6-luna": {"accepted": ["low"]}}}
    for alias in ("codex", "openai_codex"):
        assert resolve_effort("gpt-6-luna", "low", provider=alias, effort_maps=maps) == "low"
        assert resolve_effort("gpt-6-luna", "high", provider=alias, effort_maps=maps) is None
    # The custom policy reports the field the transport actually uses for this route.
    assert model_reasoning_policy("gpt-6-luna", provider="codex", effort_maps=maps).wire_parameter == "reasoning"
    # An exact provider key wins over the canonical one.
    both = {
        "openai-codex": {"gpt-6-luna": {"accepted": ["low"]}},
        "codex": {"gpt-6-luna": {"accepted": ["medium"]}},
    }
    assert resolve_effort("gpt-6-luna", "medium", provider="codex", effort_maps=both) == "medium"
    # The canonical route does not read the alias's entry: the table maps aliases to canonical
    # names, never the other way round.
    assert resolve_effort("gpt-6-luna", "medium", provider="openai-codex", effort_maps=both) is None


def test_opencode_go_effort_cannot_be_inferred_from_other_providers():
    # Same model id, different wire: ollama-cloud honours the level, the Go route does not.
    # MiMo is out of every catalog now, so the Go route answers with its outsider policy.
    assert resolve_effort("mimo-v2.6-flash", "high", provider="ollama-cloud") == "high"
    assert resolve_effort("mimo-v2.6-flash", "high", provider="opencode-go") is None
    assert is_omitted("mimo-v2.6-flash", "high", provider="opencode-go") is True


def test_commandcode_explicit_reasoning_policy():
    """The route carries the core's DeepSeek V4 contract on its top-level field.

    CommandCode's profile delegates ``deepseek/*`` ids to the DeepSeek profile, which emits
    ``extra_body.thinking``; the plugin authors the top-level level itself, because the transport
    merges the routed payload *after* the profile extras. The policy must state both halves.
    """
    for provider_name in ("commandcode", "commandcode-chat", "commandcode_chat"):
        for model_id in ("deepseek/deepseek-v4.1-flash", "deepseek-v4.1-flash"):
            pol = model_reasoning_policy(model_id, provider=provider_name)
            assert pol.provider == "commandcode"
            assert pol.model_id == "deepseek/deepseek-v4.1-flash"
            assert pol.wire_parameter == "reasoning_effort"
            # The core's DEEPSEEK_V4_EFFORTS / DEEPSEEK_V4_OVERRIDES, not a narrower guess.
            assert pol.accepted_levels == ("low", "medium", "high", "max")
            assert dict(pol.overrides) == {"xhigh": "max"}
            assert pol.omitted is False
            # Both halves of the real contract are named, together with the split of authorship.
            assert "thinking" in pol.evidence
            assert "reasoning_effort" in pol.evidence
            assert "thinking_toggle_extras" in pol.evidence
            assert "extra_body.thinking" in pol.evidence
            assert "turn_api_request" in pol.evidence
            # The probe behind the change, with the endpoint and its result.
            assert "POST https://api.commandcode.ai/provider/v1/chat/completions" in pol.evidence
            assert "2026-10-20" in pol.evidence
            assert "HTTP 200" in pol.evidence
            assert "DEEPSEEK_V4_EFFORTS" in pol.evidence
            assert "DEEPSEEK_V4_OVERRIDES" in pol.evidence
            assert "unconfirmed" not in pol.evidence
            assert "Deliberate omission" not in pol.evidence
            assert "not a reasoning_effort field" not in pol.decision
            assert "reasoning_effort" in pol.decision
            assert "xhigh" in pol.decision
            assert "effort_requested" in pol.decision

            # Every declared level goes out verbatim, `max` included.
            for lvl in ("low", "medium", "high", "max"):
                assert resolve_effort(model_id, lvl, provider=provider_name) == lvl
                assert is_omitted(model_id, lvl, provider=provider_name) is False

    # `xhigh` asks for the top tier, exactly like the core's declared override.
    assert resolve_effort("deepseek/deepseek-v4.1-flash", "xhigh", provider="commandcode") == "max"
    assert is_omitted("deepseek/deepseek-v4.1-flash", "xhigh", provider="commandcode") is False

    # No invented vocabulary and no clamp: anything the policy does not declare is omitted.
    for lvl in ("minimal", "none", "bogus", "ultra", ""):
        assert resolve_effort("deepseek/deepseek-v4.1-flash", lvl, provider="commandcode") is None
        assert is_omitted("deepseek/deepseek-v4.1-flash", lvl, provider="commandcode") is True

    # An out-of-catalog model on this route is omitted too, never guessed from another wire.
    outsider = model_reasoning_policy("kimi-k3", provider="commandcode")
    assert outsider.omitted is True
    assert resolve_effort("kimi-k3", "high", provider="commandcode") is None

    # The same namespaced id on ollama-cloud is a different contract: it resolves normally.
    assert resolve_effort("deepseek/deepseek-v4.1-flash", "high", provider="ollama-cloud") == "high"


def test_codex_policies_split_the_two_family_vocabularies():
    """Luna keeps the full ``none..max`` set; the Sol family's ladder has no ``none`` at all.

    Both vocabularies are the core's own declarations (``CODEX_GPT56_EFFORTS`` and
    ``CODEX_ASTRA_EFFORTS`` via ``codex_supported_efforts``), and the wire they ride is the
    Responses body, where the level goes in ``reasoning.effort`` — not a ``reasoning_effort``
    top-level kwarg.
    """
    luna = ("gpt-6-luna", "gpt-6-luna-900k")
    sol = ("gpt-6.1-sol",)

    for provider_name in ("openai-codex", "codex", "openai_codex"):
        for model_id in luna:
            pol = model_reasoning_policy(model_id, provider=provider_name)
            assert pol.provider == "openai-codex"
            assert pol.wire_parameter == "reasoning"
            assert pol.accepted_levels == ("none", "low", "medium", "high", "xhigh", "max")
            assert pol.omitted is False
            assert "CODEX_GPT56_EFFORTS" in pol.evidence
            assert "reasoning.effort" in pol.evidence
            assert "HERMES_DUMP_REQUESTS" in pol.evidence
            assert "2026-10-20" in pol.evidence
            assert "unconfirmed" not in pol.evidence
            for level in ("none", "low", "medium", "high", "xhigh", "max"):
                assert resolve_effort(model_id, level, provider=provider_name) == level

        for model_id in sol:
            pol = model_reasoning_policy(model_id, provider=provider_name)
            assert pol.provider == "openai-codex"
            assert pol.wire_parameter == "reasoning"
            assert pol.accepted_levels == ("low", "medium", "high", "xhigh", "max")
            assert pol.omitted is False
            assert "CODEX_ASTRA_EFFORTS" in pol.evidence
            # ``none`` is not a level on this route and would 400: it is omitted, never sent.
            assert resolve_effort(model_id, "none", provider=provider_name) is None
            assert is_omitted(model_id, "none", provider=provider_name) is True
            for level in ("low", "medium", "high", "xhigh", "max"):
                assert resolve_effort(model_id, level, provider=provider_name) == level

    # No invented vocabulary and no clamp onto an Ollama rung the route never declared.
    for model_id in (*luna, *sol):
        for level in ("minimal", "ultra", "bogus", ""):
            assert resolve_effort(model_id, level, provider="openai-codex") is None

    # An out-of-catalog model on this route is omitted, never guessed from another wire.
    assert model_reasoning_policy("gpt-6-astra", provider="openai-codex").omitted is True
    assert resolve_effort("gpt-6-astra", "high", provider="openai-codex") is None

    # The same slug on ollama-cloud is a different contract and still resolves.
    assert resolve_effort("gpt-6-luna", "high", provider="ollama-cloud") == "high"


def test_opencode_console_effort_cannot_be_inferred_from_other_providers():
    # deepseek-v4.1-flash on ollama-cloud resolves reasoning_effort
    assert resolve_effort("deepseek-v4.1-flash", "high", provider="ollama-cloud") == "high"
    assert not is_omitted("deepseek-v4.1-flash", "high", provider="ollama-cloud")

    # But on opencode-console, support is NOT inferred and remains strictly omitted
    assert resolve_effort("deepseek-v4.1-flash", "high", provider="opencode-console") is None
    assert is_omitted("deepseek-v4.1-flash", "high", provider="opencode-console") is True


def test_opencode_console_custom_effort_map_overrides_omission():
    # An explicit operator map is what enables the route, and the map is the whole vocabulary.
    maps = {"opencode-console": {"deepseek-v4.1-flash": ["low", "medium"]}}
    pol = model_reasoning_policy("deepseek-v4.1-flash", provider="opencode-console", effort_maps=maps)
    assert pol.omitted is False
    assert pol.wire_parameter == "reasoning_effort"
    assert pol.accepted_levels == ("low", "medium")
    assert resolve_effort("deepseek-v4.1-flash", "low", provider="opencode-console", effort_maps=maps) == "low"
    # `high` is not declared: the map does not clamp it onto `medium` either.
    assert resolve_effort("deepseek-v4.1-flash", "high", provider="opencode-console", effort_maps=maps) is None


#: The settings block the ``code`` profile is being migrated to
#: (``plugins.entries.jev-effort-router.settings`` in its ``config.yaml``). All three routable
#: routes declare their vocabulary now — that is what the uniform contract buys: the same
#: ``accepted``/``overrides`` shape means the same thing whether or not the route also carries a
#: built-in policy, so the declaration is the authority instead of a clamp.
#:
#: The five entries carry the routes' real vocabularies:
#:
#: * ``opencode-go``/``deepseek-v4.1-flash`` and ``commandcode``/``deepseek/deepseek-v4.1-flash``:
#:   ``low``/``medium``/``high``/``max`` with the declared ``xhigh → max``;
#: * ``openai-codex``/``gpt-6-luna`` and ``gpt-6-luna-900k``: the full ``none..max`` set;
#: * ``openai-codex``/``gpt-6.1-sol``: that set without ``none``, which the route 400s on.
#:
#: ``gpt-6.1-sol-900k`` is deliberately absent: that id is not in this account's catalog (a live
#: turn returns HTTP 400 "model entitlement" and Hermes falls back), so a declaration for it
#: would document a route that never happens.
COMMANDCODE_MODEL = "deepseek/deepseek-v4.1-flash"

GO_LEVELS: tuple[str, ...] = ("low", "medium", "high", "max")
CODEX_LUNA_LEVELS: tuple[str, ...] = ("none", "low", "medium", "high", "xhigh", "max")
CODEX_SOL_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
CODEX_DELIVERED_MODELS = ("gpt-6-luna", "gpt-6-luna-900k", "gpt-6.1-sol")


def _declared(levels: tuple[str, ...], overrides: dict | None = None) -> dict:
    spec: dict = {"accepted": list(levels)}
    if overrides:
        spec["overrides"] = dict(overrides)
    return spec


DELIVERED_CODE_PROFILE_SETTINGS = {
    "enabled": True,
    "decider": "jev_console",
    "routable_providers": ["opencode-go", "commandcode", "openai-codex"],
    "grids": {
        "opencode-go": ["deepseek-v4.1-flash: general work, explanation, ordinary coding and tool use"],
        "commandcode": [
            "deepseek/deepseek-v4.1-flash: general work, explanation, ordinary coding and tool use"
        ],
        "openai-codex": [
            "gpt-6-luna: general work, coding, explanation and tool use",
            "gpt-6-luna-900k: general work and coding over a 900k context window "
            "(large repos, long documents)",
            "gpt-6.1-sol: harder reasoning, architecture, planning and long-context analysis",
        ],
    },
    "effort_maps": {
        "opencode-go": {"deepseek-v4.1-flash": _declared(GO_LEVELS, {"xhigh": "max"})},
        "commandcode": {COMMANDCODE_MODEL: _declared(GO_LEVELS, {"xhigh": "max"})},
        "openai-codex": {
            "gpt-6-luna": _declared(CODEX_LUNA_LEVELS),
            "gpt-6-luna-900k": _declared(CODEX_LUNA_LEVELS),
            "gpt-6.1-sol": _declared(CODEX_SOL_LEVELS),
        },
    },
}


def _assert_go_contract(settings) -> None:
    """The Go route's vocabulary, resolved through the operator's own ``effort_maps``."""
    maps = settings.effort_maps
    assert "opencode-go" in maps
    for level in GO_LEVELS:
        assert (
            resolve_effort("deepseek-v4.1-flash", level, provider="opencode-go", effort_maps=maps)
            == level
        )
        assert (
            is_omitted("deepseek-v4.1-flash", level, provider="opencode-go", effort_maps=maps)
            is False
        )
    # `xhigh` reaches `max` because the override is declared, not because of any ladder ordering.
    assert (
        resolve_effort("deepseek-v4.1-flash", "xhigh", provider="opencode-go", effort_maps=maps)
        == "max"
    )
    # Nothing undeclared is invented or clamped: the field is omitted.
    for level in ("none", "minimal", "ultra", "bogus", ""):
        assert (
            resolve_effort("deepseek-v4.1-flash", level, provider="opencode-go", effort_maps=maps)
            is None
        )
        assert (
            is_omitted("deepseek-v4.1-flash", level, provider="opencode-go", effort_maps=maps)
            is True
        )


def _assert_commandcode_contract(settings) -> None:
    """The route's contract, resolved through the operator's own ``effort_maps``."""
    maps = settings.effort_maps
    assert "commandcode" in maps
    for provider_name in ("commandcode", "commandcode-chat", "commandcode_chat"):
        for level in GO_LEVELS:
            assert (
                resolve_effort(COMMANDCODE_MODEL, level, provider=provider_name, effort_maps=maps)
                == level
            )
            assert (
                is_omitted(COMMANDCODE_MODEL, level, provider=provider_name, effort_maps=maps)
                is False
            )
        # The declared override, honoured by the same rule the built-in policy used to apply.
        assert (
            resolve_effort(COMMANDCODE_MODEL, "xhigh", provider=provider_name, effort_maps=maps)
            == "max"
        )
        # Nothing undeclared is invented, clamped or escalated: the field is omitted.
        for level in ("minimal", "none", "bogus", "ultra", ""):
            assert (
                resolve_effort(COMMANDCODE_MODEL, level, provider=provider_name, effort_maps=maps)
                is None
            )
            assert (
                is_omitted(COMMANDCODE_MODEL, level, provider=provider_name, effort_maps=maps)
                is True
            )


def _assert_codex_contract(settings) -> None:
    """The Codex route's contract, resolved from the declared maps.

    Luna takes the full ``none..max`` set (``none`` is a real level here, not an omission), Sol
    has no ``none`` and omits it rather than sending one, and both ids are matched exactly — the
    ``-900k`` picker alias has its own entry, not Luna's by prefix luck.
    """
    maps = settings.effort_maps
    assert "openai-codex" in maps
    assert set(maps["openai-codex"]) == set(CODEX_DELIVERED_MODELS)
    assert [entry.model_id for entry in settings.grid_for("openai-codex")] == list(
        CODEX_DELIVERED_MODELS
    )
    for provider_name in ("openai-codex", "codex", "openai_codex"):
        for model_id in ("gpt-6-luna", "gpt-6-luna-900k"):
            for level in CODEX_LUNA_LEVELS:
                assert (
                    resolve_effort(model_id, level, provider=provider_name, effort_maps=maps)
                    == level
                )
            assert not is_omitted("gpt-6-luna", "none", provider=provider_name, effort_maps=maps)
            for level in ("minimal", "ultra", "bogus"):
                assert (
                    resolve_effort(model_id, level, provider=provider_name, effort_maps=maps)
                    is None
                )
        for level in CODEX_SOL_LEVELS:
            assert (
                resolve_effort("gpt-6.1-sol", level, provider=provider_name, effort_maps=maps)
                == level
            )
        # ``none`` is a Luna level and not a Sol one: it is omitted, never invented.
        assert (
            resolve_effort("gpt-6.1-sol", "none", provider=provider_name, effort_maps=maps) is None
        )
        assert (
            resolve_effort("gpt-6.1-sol", "minimal", provider=provider_name, effort_maps=maps)
            is None
        )


def test_the_delivered_code_profile_config_holds_the_uniform_contract():
    """Regression for the delivered configuration, across all three routes.

    The earlier round proved the contract with the built-in policy alone while the profile also
    carried an ``effort_maps`` entry for the same route; that entry ran through the custom path,
    whose clamp answered ``xhigh`` with ``high`` and ``ultra`` with ``max``. The map is now the
    declaration, not a shadow: this test loads what the profile is being migrated to and checks
    the whole matrix through it.
    """
    settings = load_settings(lambda key, default=None: DELIVERED_CODE_PROFILE_SETTINGS.get(key, default))

    # Each route is configured with its catalog's model(s) and nothing else.
    assert [entry.model_id for entry in settings.grid_for("commandcode")] == [COMMANDCODE_MODEL]
    _assert_go_contract(settings)
    _assert_commandcode_contract(settings)
    _assert_codex_contract(settings)


def test_the_parsed_maps_stay_immutable_and_normalised():
    """``load_settings`` freezes the declaration and keeps its order and strings."""
    settings = load_settings(lambda key, default=None: DELIVERED_CODE_PROFILE_SETTINGS.get(key, default))
    luna = settings.effort_maps["openai-codex"]["gpt-6-luna"]
    assert luna["accepted"] == CODEX_LUNA_LEVELS
    assert dict(luna["overrides"]) == {}
    assert settings.effort_maps["opencode-go"]["deepseek-v4.1-flash"]["accepted"] == GO_LEVELS
    assert dict(settings.effort_maps["opencode-go"]["deepseek-v4.1-flash"]["overrides"]) == {
        "xhigh": "max"
    }
    with pytest.raises(TypeError):
        settings.effort_maps["openai-codex"]["gpt-6-luna"]["accepted"] = ()  # type: ignore[index]
    with pytest.raises(TypeError):
        settings.effort_maps["opencode-go"]["deepseek-v4.1-flash"]["overrides"][  # type: ignore[index]
            "xhigh"
        ] = "high"


def test_load_settings_never_turns_a_null_level_into_a_level():
    """YAML ``null`` is not the level ``none``, and a null key is not a model name."""
    raw = {
        "effort_maps": {
            "openai-codex": {
                "gpt-6-luna": {
                    "accepted": ["none", None, 3, True, " HIGH "],
                    "overrides": {"xhigh": "max", None: "max", "low": None},
                }
            },
            "p": {"m": None},
            None: {"m": ["low"]},
        }
    }
    settings = load_settings(lambda key, default=None: raw.get(key, default))
    luna = settings.effort_maps["openai-codex"]["gpt-6-luna"]
    assert luna["accepted"] == ("none", "high")
    assert dict(luna["overrides"]) == {"xhigh": "max"}
    # The null provider key never became the name "none".
    assert None not in settings.effort_maps
    assert "none" not in settings.effort_maps
    # A matched entry whose spec is null is an empty contract, not an absent one.
    assert settings.effort_maps["p"]["m"] is None
    assert resolve_effort("m", "low", provider="p", effort_maps=settings.effort_maps) is None
    assert resolve_effort("gpt-6-luna", "none", provider="openai-codex", effort_maps=settings.effort_maps) == "none"
    assert resolve_effort("gpt-6-luna", "high", provider="openai-codex", effort_maps=settings.effort_maps) == "high"
    assert resolve_effort("gpt-6-luna", "low", provider="openai-codex", effort_maps=settings.effort_maps) is None


def test_the_live_code_profile_config_holds_the_uniform_contract():
    """Same contract, read from the operator's real profile file when the run points at it.

    Opt-in so the suite stays hermetic: a checkout without that file has nothing to read.
    ``JEV_PROFILE_CONFIG=/path/to/config.yaml`` enables the check.

    The file is migrated in the next step of this work: until then this check fails on purpose
    rather than asserting the old block, which would make the migration look complete.
    """
    raw_path = os.environ.get("JEV_PROFILE_CONFIG", "")
    if not raw_path or not Path(raw_path).is_file():
        pytest.skip("JEV_PROFILE_CONFIG not set to a readable profile config")
    yaml = pytest.importorskip("yaml")
    block = yaml.safe_load(Path(raw_path).read_text(encoding="utf-8"))["plugins"]["entries"][
        "jev-effort-router"
    ]["settings"]
    settings = load_settings(lambda key, default=None: block.get(key, default))
    _assert_go_contract(settings)
    _assert_commandcode_contract(settings)
    _assert_codex_contract(settings)
