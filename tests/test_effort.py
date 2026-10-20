"""Effort translation per model family."""

from __future__ import annotations

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


def test_per_provider_effort_maps_honoured():
    effort_maps = {
        "opencode-go": {
            "deepseek-v4.1-flash": ["low", "medium"],
            "kimi-k3": {"accepted": ["low", "high"], "overrides": {"medium": "high"}},
        }
    }
    # Clamping without escalation: high clamped to medium
    assert (
        resolve_effort(
            "deepseek-v4.1-flash",
            "high",
            provider="opencode-go",
            effort_maps=effort_maps,
        )
        == "medium"
    )
    # Escalating override medium -> high is rejected; clamps to weaker 'low'
    assert (
        resolve_effort(
            "kimi-k3",
            "medium",
            provider="opencode-go",
            effort_maps=effort_maps,
        )
        == "low"
    )
    # Unmapped model under provider with effort_map omits effort
    assert (
        resolve_effort(
            "unknown-model",
            "medium",
            provider="opencode-go",
            effort_maps=effort_maps,
        )
        is None
    )


def test_per_provider_effort_maps_never_escalates_and_omits_when_unknown():
    # Higher floor: accepted has only high
    # low and medium cannot be fulfilled without escalation -> omit (None)
    spec_high = ["high"]
    assert resolve_effort("m", "low", provider="opencode-go", effort_maps={"opencode-go": {"m": spec_high}}) is None
    assert resolve_effort("m", "medium", provider="opencode-go", effort_maps={"opencode-go": {"m": spec_high}}) is None
    assert resolve_effort("m", "high", provider="opencode-go", effort_maps={"opencode-go": {"m": spec_high}}) == "high"

    # Empty or invalid specs omit
    assert resolve_effort("m", "low", provider="opencode-go", effort_maps={"opencode-go": {"m": []}}) is None
    assert resolve_effort("m", "low", provider="opencode-go", effort_maps={"opencode-go": {"m": {"accepted": []}}}) is None
    assert resolve_effort("m", "low", provider="opencode-go", effort_maps={"opencode-go": {"m": None}}) is None

    # Non-escalating override is honored
    override_deescalate = {"accepted": ["low", "medium"], "overrides": {"high": "medium"}}
    assert resolve_effort("m", "high", provider="opencode-go", effort_maps={"opencode-go": {"m": override_deescalate}}) == "medium"


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


def test_operator_effort_map_takes_precedence_over_the_go_builtin_policy():
    """An operator map still wins, and keeps its existing nearest-weaker clamp."""
    maps = {"opencode-go": {"deepseek-v4.1-flash": {"accepted": ["low"]}}}
    pol = model_reasoning_policy("deepseek-v4.1-flash", provider="opencode-go", effort_maps=maps)
    assert pol.wire_parameter == "reasoning_effort"
    assert pol.omitted is False
    assert pol.accepted_levels == ("low",)
    assert "operator" in pol.evidence.lower()
    # `high` cannot be sent to a route that only declared `low`: clamp down, never escalate.
    assert (
        resolve_effort("deepseek-v4.1-flash", "high", provider="opencode-go", effort_maps=maps)
        == "low"
    )


def test_opencode_go_effort_cannot_be_inferred_from_other_providers():
    # Same model id, different wire: ollama-cloud honours the level, the Go route does not.
    # MiMo is out of every catalog now, so the Go route answers with its outsider policy.
    assert resolve_effort("mimo-v2.6-flash", "high", provider="ollama-cloud") == "high"
    assert resolve_effort("mimo-v2.6-flash", "high", provider="opencode-go") is None
    assert is_omitted("mimo-v2.6-flash", "high", provider="opencode-go") is True


def test_commandcode_explicit_reasoning_policy():
    """The route omits the plugin's own field, on a two-component contract.

    CommandCode's profile delegates ``deepseek/*`` ids to the DeepSeek profile, which emits BOTH
    ``extra_body.thinking`` and a top-level ``reasoning_effort``. The policy must say that, and must
    justify the omission by the endpoint-level uncertainty — never by the field's absence.
    """
    for provider_name in ("commandcode", "commandcode-chat", "commandcode_chat"):
        for model_id in ("deepseek/deepseek-v4.1-flash", "deepseek-v4.1-flash"):
            pol = model_reasoning_policy(model_id, provider=provider_name)
            assert pol.provider == "commandcode"
            assert pol.model_id == "deepseek/deepseek-v4.1-flash"
            assert pol.wire_parameter is None
            assert pol.accepted_levels == ()
            assert pol.omitted is True
            # Both halves of the real contract are named, together with the deliberate removal.
            assert "thinking" in pol.evidence
            assert "reasoning_effort" in pol.evidence
            assert "thinking_toggle_extras" in pol.evidence
            assert "BOTH" in pol.evidence
            assert "drops a pre-existing top-level reasoning_effort" in pol.evidence
            assert "unconfirmed" in pol.evidence
            assert "never inferred from OpenCode" in pol.evidence
            # The false claim that the field is absent from the contract is gone.
            assert "no reasoning_effort field is part of this generation contract" not in pol.evidence
            assert "not a reasoning_effort field" not in pol.decision
            assert "Deliberate omission" in pol.decision
            assert "pre-existing top-level reasoning_effort is removed" in pol.decision
            assert "unconfirmed" in pol.decision
            assert "effort_requested" in pol.decision

            for lvl in ("low", "medium", "high"):
                assert resolve_effort(model_id, lvl, provider=provider_name) is None
                assert is_omitted(model_id, lvl, provider=provider_name) is True

    # An out-of-catalog model on this route is omitted too, never guessed from another wire.
    outsider = model_reasoning_policy("kimi-k3", provider="commandcode")
    assert outsider.omitted is True
    assert resolve_effort("kimi-k3", "high", provider="commandcode") is None

    # The same namespaced id on ollama-cloud is a different contract: it resolves normally.
    assert resolve_effort("deepseek/deepseek-v4.1-flash", "high", provider="ollama-cloud") == "high"


def test_opencode_console_effort_cannot_be_inferred_from_other_providers():
    # deepseek-v4.1-flash on ollama-cloud resolves reasoning_effort
    assert resolve_effort("deepseek-v4.1-flash", "high", provider="ollama-cloud") == "high"
    assert not is_omitted("deepseek-v4.1-flash", "high", provider="ollama-cloud")

    # But on opencode-console, support is NOT inferred and remains strictly omitted
    assert resolve_effort("deepseek-v4.1-flash", "high", provider="opencode-console") is None
    assert is_omitted("deepseek-v4.1-flash", "high", provider="opencode-console") is True


def test_opencode_console_custom_effort_map_overrides_omission():
    # If operator explicitly configures effort_maps for opencode-console, it is honored
    maps = {"opencode-console": {"deepseek-v4.1-flash": ["low", "medium"]}}
    pol = model_reasoning_policy("deepseek-v4.1-flash", provider="opencode-console", effort_maps=maps)
    assert pol.omitted is False
    assert pol.wire_parameter == "reasoning_effort"
    assert pol.accepted_levels == ("low", "medium")
    assert resolve_effort("deepseek-v4.1-flash", "low", provider="opencode-console", effort_maps=maps) == "low"
    assert resolve_effort("deepseek-v4.1-flash", "high", provider="opencode-console", effort_maps=maps) == "medium"

