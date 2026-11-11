"""The opt-in ``claude-subscription-directsdk-experimental`` route.

This is **not** the native ``anthropic`` route (``tests/test_anthropic_route.py``), even though it
serves the same three Claude families. The provider is different — Claude Pro/Max driven through the
official Claude Code CLI — and so is the payload: ``api_mode=chat_completions``, with the decided
level at **``extra_body.reasoning.effort``**, one level below the kwargs.

Four properties are pinned here, all of them wire-relevant:

1. The route's catalog is the three native ids the provider's own ``model_catalog.py`` pins, with the
   descriptions the planner fixed — they are the criteria strings Jev is sent.
2. The effort caps are family-scoped: Opus and Sonnet stop at ``high``, Haiku keeps ``max``. A level
   outside a family's cap is *omitted*, never clamped onto a neighbour.
3. The rewrite is narrow: the level lands inside a ``reasoning`` object the transport already built
   inside an ``extra_body`` the transport already built. The plugin creates neither, never writes a
   top-level ``reasoning_effort`` (the provider's kwarg allowlist rejects it), preserves
   ``reasoning.enabled`` and every other key, and leaves a ``reasoning`` object the transport
   *disabled* untouched (the audit then reports ``effort: null``).
4. The payload branch is chosen by declaration (``config.wire_style``), not by a provider ``if`` in
   the router, and the routed kwargs stay inside the provider's own allowlists.
"""

from __future__ import annotations

import copy
import json

import pytest
from stubs import StubResponse, StubTransport, decision_payload, subscription_request
from test_router import build, route

from config import (
    CLAUDE_SUBSCRIPTION_ALLOWLIST,
    CLAUDE_SUBSCRIPTION_MODELS,
    ROUTE_WIRE_STYLES,
    ROUTED_API_MODES,
    WIRE_STYLE_EXTRA_BODY_REASONING,
    WIRE_STYLE_OUTPUT_CONFIG,
    WIRE_STYLE_RESPONSES,
    WIRE_STYLE_TOP_LEVEL,
    load_settings,
    route_allowlist,
    route_catalog,
    wire_style,
)
from effort import (
    ANTHROPIC_REASONING_POLICIES,
    DEFAULT_OFFERED_LEVELS,
    SUBSCRIPTION_MODEL_LEVELS,
    SUBSCRIPTION_REASONING_POLICIES,
    offered_levels,
    offered_levels_for_grid,
    resolve_effort,
)
from grid import SUBSCRIPTION_GRID

PROVIDER = "claude-subscription-directsdk-experimental"
HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"

SUBSCRIPTION_CONFIG = {"decider": "jev_console", "routable_providers": [PROVIDER]}

#: The provider's own allowlists, copied from ``directsdk.py::request_body`` (0.3.4). The plugin's
#: output has to stay inside them or the request dies in the provider before a process is spawned.
PROVIDER_ALLOWED_KWARGS = frozenset(
    {
        "model",
        "messages",
        "tools",
        "stream",
        "stream_options",
        "max_tokens",
        "max_completion_tokens",
        "temperature",
        "top_p",
        "stop",
        "extra_body",
        "extra_headers",
        "timeout",
        "tool_choice",
        "parallel_tool_calls",
        "n",
        "response_format",
        "hermes_auxiliary",
    }
)
PROVIDER_ALLOWED_EXTRA_BODY = frozenset(
    {"max_tokens", "temperature", "top_p", "stop_sequences", "reasoning", "response_format"}
)
PROVIDER_ALLOWED_REASONING = frozenset({"enabled", "effort"})
PROVIDER_EFFORT_VOCAB = ("none", "low", "medium", "high", "xhigh", "max")


def _settings(config=None):
    return load_settings(lambda key, default=None: (config or {}).get(key, default))


def subscription_route(router, request=None, **overrides):
    """``route`` with the subscription route's provider and payload shape."""
    kwargs = dict(
        model=SONNET,
        provider=PROVIDER,
        base_url="https://subscription.invalid",
        api_mode="chat_completions",
    )
    kwargs.update(overrides)
    return route(router, request if request is not None else subscription_request(), **kwargs)


def _effort_on_wire(routed):
    """The level as the provider's parser would read it — ``extra_body.reasoning.effort``."""
    return routed.get("extra_body", {}).get("reasoning", {}).get("effort")


# -- the catalog ---------------------------------------------------------------


def test_the_route_carries_the_three_native_claude_families():
    assert [entry.model_id for entry in SUBSCRIPTION_GRID] == [HAIKU, SONNET, OPUS]
    assert {entry.model_id: entry.description for entry in SUBSCRIPTION_GRID} == {
        HAIKU: "routine and well-scoped agentic coding, quick edits, summaries, extraction, "
        "classification and other high-volume chores",
        SONNET: "human-facing messages and writing, careful single-file or refined code changes, "
        "code review",
        OPUS: "planning, orchestration, architecture, multi-step debugging and deep analysis",
    }
    # Native ids, verbatim: no ``claude/`` prefix and no short alias (``opus``/``sonnet``/``haiku``),
    # which the provider resolves only for a name the operator typed.
    assert all("/" not in entry.model_id for entry in SUBSCRIPTION_GRID)
    assert all(entry.wire_model == entry.model_id for entry in SUBSCRIPTION_GRID)


def test_the_catalog_and_the_allowlist_are_the_same_three_ids():
    assert CLAUDE_SUBSCRIPTION_MODELS == (HAIKU, SONNET, OPUS)
    assert CLAUDE_SUBSCRIPTION_ALLOWLIST == frozenset(CLAUDE_SUBSCRIPTION_MODELS)
    assert route_catalog(PROVIDER) == SUBSCRIPTION_GRID
    assert route_catalog(PROVIDER.upper()) == SUBSCRIPTION_GRID
    # The two Claude routes are distinct providers: one catalog each, never merged.
    assert route_catalog(PROVIDER) is not route_catalog("anthropic")


def test_the_route_is_opt_in_and_the_cap_needs_the_console_decider():
    """Same contract as ``openai-codex`` / ``anthropic``: the cap is a ``jev_console`` feature."""
    assert route_allowlist(PROVIDER) is None
    assert route_allowlist(PROVIDER, decider="jev") is None
    assert route_allowlist(PROVIDER, decider="jev_console") == CLAUDE_SUBSCRIPTION_ALLOWLIST

    console = _settings(SUBSCRIPTION_CONFIG)
    grid = console.effective(None, provider=PROVIDER).grid
    assert [entry.model_id for entry in grid] == [HAIKU, SONNET, OPUS]
    # Opus is the route's default model (its reason to exist is planner/reviewer work), not the
    # first entry — Haiku.
    assert console.effective(None, provider=PROVIDER).default_model == OPUS


def test_an_operator_grid_outside_the_catalog_is_capped_back_to_the_route_catalog():
    settings = _settings(
        {
            "decider": "jev_console",
            "routable_providers": [PROVIDER],
            "grids": {PROVIDER: ["claude-opus-4-1: old opus", "gpt-9-ultra: nonsense"]},
        }
    )

    assert [entry.model_id for entry in settings.grid_for(PROVIDER)] == [HAIKU, SONNET, OPUS]


def test_the_route_is_declared_as_the_extra_body_wire_style():
    """The branch is declared per route; the router never tests the provider id itself."""
    assert ROUTE_WIRE_STYLES == {PROVIDER: WIRE_STYLE_EXTRA_BODY_REASONING}
    # ``chat_completions`` on its own still means the top-level field — this route is the exception.
    assert wire_style(PROVIDER, "chat_completions") == WIRE_STYLE_EXTRA_BODY_REASONING
    assert wire_style(PROVIDER.upper(), "chat_completions") == WIRE_STYLE_EXTRA_BODY_REASONING
    assert wire_style("ollama-cloud", "chat_completions") == WIRE_STYLE_TOP_LEVEL
    assert wire_style("opencode-go", "chat_completions") == WIRE_STYLE_TOP_LEVEL
    # The other routes keep the style their payload shape implies.
    assert wire_style("anthropic", "anthropic_messages") == WIRE_STYLE_OUTPUT_CONFIG
    assert wire_style("openai-codex", "codex_responses") == WIRE_STYLE_RESPONSES
    assert wire_style("", "") == WIRE_STYLE_TOP_LEVEL
    # Same payload shape as every OpenAI-compatible route, so no new api_mode was needed.
    assert "chat_completions" in ROUTED_API_MODES


# -- offered levels and caps ---------------------------------------------------


def test_opus_and_sonnet_offer_only_up_to_high_and_haiku_offers_max():
    assert offered_levels(PROVIDER, OPUS) == ("low", "medium", "high")
    assert offered_levels(PROVIDER, SONNET) == ("low", "medium", "high")
    assert offered_levels(PROVIDER, HAIKU) == ("low", "medium", "high", "max")


def test_the_shared_question_never_offers_a_level_a_family_caps_away():
    assert offered_levels_for_grid(SUBSCRIPTION_GRID, PROVIDER) == ("low", "medium", "high")
    haiku_only = tuple(entry for entry in SUBSCRIPTION_GRID if entry.model_id == HAIKU)
    assert offered_levels_for_grid(haiku_only, PROVIDER) == ("low", "medium", "high", "max")


def test_the_caps_do_not_widen_another_route_s_question():
    assert offered_levels("ollama-cloud", "kimi-k3") == DEFAULT_OFFERED_LEVELS
    assert offered_levels("anthropic", OPUS) == ("low", "medium", "high")


def test_an_operator_map_still_wins_over_the_built_in_offer():
    maps = {PROVIDER: {OPUS: ["low", "high"]}}

    assert offered_levels(PROVIDER, OPUS, maps) == ("low", "high")
    # A model the map does not name falls back to its own built-in declaration.
    assert offered_levels(PROVIDER, HAIKU, maps) == ("low", "medium", "high", "max")


def test_the_caps_and_the_policies_cannot_drift_apart():
    assert dict(SUBSCRIPTION_MODEL_LEVELS) == {
        model_id: policy.accepted_levels
        for model_id, policy in SUBSCRIPTION_REASONING_POLICIES.items()
    }
    for model_id, policy in SUBSCRIPTION_REASONING_POLICIES.items():
        assert policy.provider == PROVIDER
        assert policy.wire_parameter == "extra_body.reasoning.effort"
        assert policy.omitted is False
        assert policy.legacy_clamp is False
        assert policy.offered == policy.accepted_levels
        assert "build_api_kwargs_extras" in policy.evidence
        assert "directsdk.py" in policy.evidence
        assert model_id in policy.decision
    # The route declares its own ids and does not borrow the Messages route's policies.
    assert set(SUBSCRIPTION_REASONING_POLICIES).isdisjoint(ANTHROPIC_REASONING_POLICIES) is False
    assert (
        SUBSCRIPTION_REASONING_POLICIES[OPUS].wire_parameter
        != ANTHROPIC_REASONING_POLICIES[OPUS].wire_parameter
    )


# -- the wire value ------------------------------------------------------------


@pytest.mark.parametrize("level", ["low", "medium", "high"])
@pytest.mark.parametrize("model", [HAIKU, SONNET, OPUS])
def test_every_accepted_level_goes_out_verbatim(model, level):
    assert resolve_effort(model, level, provider=PROVIDER) == level


def test_max_is_sent_on_haiku_and_omitted_on_opus_and_sonnet():
    assert resolve_effort(HAIKU, "max", provider=PROVIDER) == "max"
    assert resolve_effort(SONNET, "max", provider=PROVIDER) is None
    assert resolve_effort(OPUS, "max", provider=PROVIDER) is None


@pytest.mark.parametrize("level", ["xhigh", "minimal", "ultra", "none", "bogus", "", None])
def test_an_undeclared_level_is_omitted(level):
    for model in (HAIKU, SONNET, OPUS):
        assert resolve_effort(model, level, provider=PROVIDER) is None


def test_a_model_outside_the_catalog_is_omitted():
    assert resolve_effort("claude-opus-4-1", "high", provider=PROVIDER) is None
    assert resolve_effort("gpt-6-luna", "high", provider=PROVIDER) is None


# -- the payload ---------------------------------------------------------------


def test_the_level_lands_in_extra_body_reasoning_and_nowhere_else(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key")
    transport = StubTransport(
        [StubResponse(decision_payload("2", 0.95, "high", 0.9, grid_size=3))]
    )
    router, settings = build(tmp_path, transport, config=SUBSCRIPTION_CONFIG)
    monkeypatch.setattr(router, "_data_dir", lambda: str(tmp_path / "audit"))

    result = subscription_route(router, turn_id="sub-1", session_id="sub")

    assert result is not None
    routed = result["request"]
    assert routed["model"] == SONNET
    assert _effort_on_wire(routed) == "high"
    # A top-level ``reasoning_effort`` is not in the provider's allowed kwargs; a dead call.
    assert "reasoning_effort" not in routed
    # Everything else is the transport's and survives.
    assert routed["messages"] == [{"role": "user", "content": "hello"}]
    assert routed["max_tokens"] == 4096
    assert routed["extra_body"]["reasoning"]["enabled"] is True

    record = router.tail(settings, limit=1)[0]
    assert record["event"] == "route"
    assert record["provider"] == PROVIDER
    assert record["model"] == SONNET
    assert record["wire_model"] == SONNET
    assert record["effort"] == "high"
    assert record["effort_requested"] == "high"
    assert record["fallback_reasons"] == []
    assert transport.call_count == 1


def test_other_extra_body_keys_survive_the_rewrite(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key")
    transport = StubTransport(
        [StubResponse(decision_payload("2", 0.95, "high", 0.9, grid_size=3))]
    )
    router, _ = build(tmp_path, transport, config=SUBSCRIPTION_CONFIG)
    monkeypatch.setattr(router, "_data_dir", lambda: str(tmp_path / "audit"))

    result = subscription_route(
        router,
        subscription_request(
            extra_body={"stop_sequences": ["</done>"], "top_p": 0.9},
        ),
        turn_id="sub-siblings",
        session_id="sub-siblings",
    )

    assert result is not None
    extra = result["request"]["extra_body"]
    assert extra["stop_sequences"] == ["</done>"]
    assert extra["top_p"] == 0.9
    assert extra["reasoning"] == {"enabled": True, "effort": "high"}


def test_every_reasoning_key_the_transport_wrote_is_preserved(tmp_path, monkeypatch):
    """``enabled`` is the transport's, and the provider accepts only ``enabled``/``effort``."""
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key")
    transport = StubTransport(
        [StubResponse(decision_payload("1", 0.95, "low", 0.9, grid_size=3))]
    )
    router, _ = build(tmp_path, transport, config=SUBSCRIPTION_CONFIG)
    monkeypatch.setattr(router, "_data_dir", lambda: str(tmp_path / "audit"))

    result = subscription_route(
        router,
        subscription_request(effort="xhigh"),
        turn_id="sub-enabled",
        session_id="sub-enabled",
    )

    assert result is not None
    reasoning = result["request"]["extra_body"]["reasoning"]
    assert reasoning == {"enabled": True, "effort": "low"}


def test_the_request_is_not_mutated_in_place(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key")
    transport = StubTransport(
        [StubResponse(decision_payload("3", 0.95, "high", 0.9, grid_size=3))]
    )
    router, _ = build(tmp_path, transport, config=SUBSCRIPTION_CONFIG)
    monkeypatch.setattr(router, "_data_dir", lambda: str(tmp_path / "audit"))
    request = subscription_request(effort="medium")
    snapshot = copy.deepcopy(request)

    result = subscription_route(router, request, turn_id="sub-immutable", session_id="sub-immutable")

    assert result is not None
    assert result["request"] is not request
    assert request == snapshot
    # The transport's own level is replaced by Jev's, not merged with it.
    assert _effort_on_wire(result["request"]) == "high"


def test_a_haiku_only_grid_can_put_max_on_the_wire(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key")
    transport = StubTransport(
        [
            StubResponse(
                decision_payload(
                    "1",
                    0.97,
                    "max",
                    0.93,
                    effort_probabilities={"low": 0.02, "medium": 0.02, "high": 0.03, "max": 0.93},
                    grid_size=1,
                )
            )
        ]
    )
    config = dict(SUBSCRIPTION_CONFIG, grids={PROVIDER: [f"{HAIKU}: chores"]})
    router, settings = build(tmp_path, transport, config=config)
    monkeypatch.setattr(router, "_data_dir", lambda: str(tmp_path / "audit"))

    result = subscription_route(
        router,
        subscription_request(model=HAIKU),
        model=HAIKU,
        turn_id="sub-haiku-max",
        session_id="sub-haiku-max",
    )

    assert result is not None
    assert result["request"]["model"] == HAIKU
    assert _effort_on_wire(result["request"]) == "max"
    record = router.tail(settings, limit=1)[0]
    assert record["effort"] == "max"
    assert record["effort_requested"] == "max"


def test_an_unoffered_max_is_recorded_and_dropped_from_the_reasoning_object(
    tmp_path, monkeypatch
):
    """Opus/Sonnet cap at ``high``: the answer survives in the audit, not on the wire."""
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key")
    transport = StubTransport(
        [
            StubResponse(
                decision_payload(
                    "3",
                    0.95,
                    "max",
                    0.9,
                    effort_probabilities={"low": 0.03, "medium": 0.03, "high": 0.04, "max": 0.9},
                    grid_size=3,
                )
            )
        ]
    )
    router, settings = build(tmp_path, transport, config=SUBSCRIPTION_CONFIG)
    monkeypatch.setattr(router, "_data_dir", lambda: str(tmp_path / "audit"))

    result = subscription_route(router, turn_id="sub-opus-max", session_id="sub-opus-max")

    assert result is not None
    assert result["request"]["model"] == OPUS
    # The transport's own level is removed with the decision — nothing is substituted (no ``high``
    # clamp) — so a ``null`` effort in the audit always means "no level on the wire".
    assert "effort" not in result["request"]["extra_body"]["reasoning"]
    assert result["request"]["extra_body"]["reasoning"]["enabled"] is True
    assert "reasoning_effort" not in result["request"]
    record = router.tail(settings, limit=1)[0]
    assert record["effort_requested"] == "max"
    assert record["effort"] is None


def test_no_extra_body_and_no_reasoning_are_ever_invented(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key")
    transport = StubTransport(
        [
            StubResponse(decision_payload("1", 0.95, "high", 0.9, grid_size=3)),
            StubResponse(decision_payload("1", 0.95, "high", 0.9, grid_size=3)),
        ]
    )
    router, settings = build(tmp_path, transport, config=SUBSCRIPTION_CONFIG)
    monkeypatch.setattr(router, "_data_dir", lambda: str(tmp_path / "audit"))

    without_extra_body = subscription_route(
        router,
        subscription_request(include_extra_body=False),
        turn_id="sub-no-extra-body",
        session_id="sub-no-extra-body",
    )
    without_reasoning = subscription_route(
        router,
        subscription_request(include_reasoning=False),
        turn_id="sub-no-reasoning",
        session_id="sub-no-reasoning",
    )

    assert without_extra_body is not None and without_reasoning is not None
    # The provider's ``build_api_kwargs_extras`` is the author of both containers; a route it left
    # without them gets none invented (an invented ``extra_body`` is a request it rejects).
    assert "extra_body" not in without_extra_body["request"]
    assert without_extra_body["request"]["model"] == HAIKU
    assert without_reasoning["request"]["extra_body"] == {}
    assert "reasoning_effort" not in without_extra_body["request"]
    assert "reasoning_effort" not in without_reasoning["request"]

    records = router.tail(settings, limit=2)
    assert [record["effort"] for record in records] == [None, None]
    assert [record["effort_requested"] for record in records] == ["high", "high"]


def test_a_disabled_reasoning_object_is_left_intact_and_the_audit_says_null(
    tmp_path, monkeypatch
):
    """``enabled: false`` is the session's decision, not the router's: don't level it."""
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key")
    transport = StubTransport(
        [StubResponse(decision_payload("2", 0.95, "high", 0.9, grid_size=3))]
    )
    router, settings = build(tmp_path, transport, config=SUBSCRIPTION_CONFIG)
    monkeypatch.setattr(router, "_data_dir", lambda: str(tmp_path / "audit"))
    request = subscription_request(enabled=False, effort="medium")
    snapshot = copy.deepcopy(request)

    result = subscription_route(router, request, turn_id="sub-disabled", session_id="sub-disabled")

    assert result is not None
    # Byte-identical reasoning block: the level the transport derived is what goes out.
    assert result["request"]["extra_body"]["reasoning"] == {"enabled": False, "effort": "medium"}
    assert request == snapshot
    assert "reasoning_effort" not in result["request"]
    record = router.tail(settings, limit=1)[0]
    assert record["effort"] is None
    assert record["effort_requested"] == "high"


def test_the_route_replays_the_same_body_without_a_second_decision(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key")
    transport = StubTransport(
        [StubResponse(decision_payload("2", 0.95, "high", 0.9, grid_size=3))]
    )
    router, _ = build(tmp_path, transport, config=SUBSCRIPTION_CONFIG)
    monkeypatch.setattr(router, "_data_dir", lambda: str(tmp_path / "audit"))
    request = subscription_request()

    first = subscription_route(router, request, turn_id="sub-replay", session_id="sub-replay")
    second = subscription_route(
        router, request, turn_id="sub-replay", session_id="sub-replay", api_call_count=1
    )

    assert first is not None and second is not None
    assert json.dumps(second["request"], sort_keys=True) == json.dumps(
        first["request"], sort_keys=True
    )
    assert transport.call_count == 1


# -- the provider's own parser ------------------------------------------------


@pytest.mark.parametrize("level", ["low", "medium", "high"])
@pytest.mark.parametrize("model", [HAIKU, SONNET, OPUS])
def test_the_routed_body_stays_inside_the_provider_allowlists(model, level, tmp_path, monkeypatch):
    """The reproduction of what ``directsdk.py`` receives, per model and level.

    ``directsdk.py::request_body`` raises ``ValueError`` on a kwarg outside its allowlist (which is
    why a top-level ``reasoning_effort`` would be fatal here), pops ``extra_body.reasoning``,
    refuses any key but ``enabled``/``effort``, and clamps the effort onto
    ``(none, low, medium, high, xhigh, max)`` before writing ``output_config: {effort: …}``. This
    test checks the routed payload against every one of those gates; the actual parse is reproduced
    for the three ids in ``docs/EVIDENCIAS_OPERACIONAIS.md`` §12.
    """
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key")
    choice = {HAIKU: "1", SONNET: "2", OPUS: "3"}[model]
    transport = StubTransport(
        [StubResponse(decision_payload(choice, 0.95, level, 0.9, grid_size=3))]
    )
    router, settings = build(tmp_path, transport, config=SUBSCRIPTION_CONFIG)
    monkeypatch.setattr(router, "_data_dir", lambda: str(tmp_path / "audit"))

    result = subscription_route(
        router,
        subscription_request(model=model, effort="medium"),
        model=model,
        turn_id=f"sub-contract-{model}-{level}",
        session_id=f"sub-contract-{model}-{level}",
    )

    assert result is not None
    routed = result["request"]
    assert set(routed) <= PROVIDER_ALLOWED_KWARGS
    assert set(routed["extra_body"]) <= PROVIDER_ALLOWED_EXTRA_BODY
    reasoning = routed["extra_body"]["reasoning"]
    assert set(reasoning) <= PROVIDER_ALLOWED_REASONING
    # The value the provider will put in ``output_config.effort`` and pass to ``--effort``.
    assert reasoning["effort"] == level
    assert reasoning["effort"] in PROVIDER_EFFORT_VOCAB
    assert router.tail(settings, limit=1)[0]["effort"] == level


# -- the opt-in gate -----------------------------------------------------------


def test_the_provider_is_left_untouched_when_it_is_not_declared(tmp_path, monkeypatch):
    """Without the provider in ``routable_providers`` the turn is byte-identical."""
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key")
    transport = StubTransport([])
    router, settings = build(
        tmp_path, transport, config={"decider": "jev_console", "routable_providers": ["ollama-cloud"]}
    )
    monkeypatch.setattr(router, "_data_dir", lambda: str(tmp_path / "audit"))
    request = subscription_request()

    assert subscription_route(router, request, turn_id="sub-off", session_id="sub-off") is None
    assert transport.call_count == 0
    record = router.tail(settings, limit=1)[0]
    assert record["event"] == "skip"
    assert record["reason"] == "provider_not_routed"
    assert record["provider"] == PROVIDER


def test_the_native_anthropic_route_is_unaffected_by_this_one(tmp_path, monkeypatch):
    """Two providers, two routes: declaring the subscription one does not route ``anthropic``."""
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key")
    transport = StubTransport([])
    router, settings = build(tmp_path, transport, config=SUBSCRIPTION_CONFIG)
    monkeypatch.setattr(router, "_data_dir", lambda: str(tmp_path / "audit"))

    assert subscription_route(router, provider="anthropic", turn_id="sub-native", session_id="sub-native") is None
    assert transport.call_count == 0
    assert router.tail(settings, limit=1)[0]["reason"] == "provider_not_routed"
