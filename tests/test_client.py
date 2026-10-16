"""Client wire contract: payload shape, answer interpretation, failure containment."""

from __future__ import annotations

import hashlib
import json

import pytest

from client import (
    REASON_LOW_CONFIDENCE,
    REASON_MALFORMED,
    REASON_NO_API_KEY,
    REASON_TIMEOUT,
    REASON_UNKNOWN_CHOICE,
    REASON_UPSTREAM_ERROR,
    JevClient,
)
from stubs import ReadTimeout, StubResponse, StubTransport, decision_payload
from config import load_settings

MESSAGES = [
    {"role": "user", "content": "première question"},
    {"role": "assistant", "content": "première réponse"},
    {"role": "user", "content": "refactore le module de paiement"},
]


def make(transport, config=None):
    settings = load_settings(lambda key, default=None: (config or {}).get(key, default))
    return JevClient(settings, transport=transport), settings


def test_payload_matches_the_documented_shape(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    transport = StubTransport([StubResponse(decision_payload())])
    client, settings = make(transport)

    client.decide(MESSAGES, settings.effective(None), platform="cli", provider="ollama-cloud")

    payload = transport.last_payload
    assert payload["model"] == settings.jev_model == "typesafe/jev-1.13"
    assert transport.calls[0]["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer sk-or-test"

    state = payload["state"]
    assert state["user_message"] == "refactore le module de paiement"
    assert "première question" in state["recent_context"]
    assert state["surface"] == "cli"
    assert state["provider"] == "ollama-cloud"
    # The configured model is deliberately NOT sent: it anchors the decision on itself.
    assert "currently_configured_model" not in state

    questions = payload["questions"]
    assert questions["model_route"]["type"] == "choice"
    assert set(questions["model_route"]["criteria"]) == {"1", "2", "3", "4", "5", "6"}
    assert questions["model_route"]["criteria"]["1"].startswith("deepseek-v4.1-flash: ")
    assert questions["reasoning_effort"]["type"] == "choice"
    assert set(questions["reasoning_effort"]["criteria"]) == {"low", "medium", "high"}


def test_default_jev_payload_is_frozen(monkeypatch):
    """The legacy Jev request body must not drift when the decider becomes pluggable."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    transport = StubTransport([StubResponse(decision_payload())])
    client, settings = make(transport)

    client.decide(MESSAGES, settings.effective(None), platform="cli", provider="ollama-cloud")

    canonical = json.dumps(
        transport.last_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == (
        "f6e505a715f828b0c4c7be839c0d08e8e5af38b8ff8b76d5e66e688d7f28884b"
    )


def test_payload_is_stated_in_one_language(monkeypatch):
    """The whole payload must be English — instructions, criteria and effort levels alike.

    A half-translated payload is a half-measured one: the grid profiles, the two question
    instructions and the effort criteria all travel in the same request and are all weighed.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    transport = StubTransport([StubResponse(decision_payload())])
    client, settings = make(transport)

    client.decide(MESSAGES, settings.effective(None), platform="cli", provider="ollama-cloud")

    french = set("àâäçéèêëîïôöùûüœ")
    questions = transport.last_payload["questions"]
    for question in questions.values():
        strings = [question["instructions"], *question["criteria"].values()]
        for text in strings:
            offenders = sorted(french & set(text.lower()))
            assert not offenders, f"French text in the Jev payload: {offenders} in {text!r}"


def test_context_window_is_bounded(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    transport = StubTransport([StubResponse(decision_payload())])
    client, settings = make(transport, config={"context_turns": 1})
    long_messages = [
        {"role": "user", "content": f"tour {index} " + "x" * 5000} for index in range(6)
    ]

    client.decide(long_messages, settings.effective(None))

    state = transport.last_payload["state"]
    assert "tour 0" not in state["recent_context"]
    assert "tour 5" not in state["recent_context"]  # the last user turn is `user_message`
    assert len(state["recent_context"]) <= 12001
    assert len(state["user_message"]) <= 4001


def test_decision_interpretation(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    transport = StubTransport([StubResponse(decision_payload("2", 0.91, "high", 0.66))])
    client, settings = make(transport)

    decision, reason = client.decide(MESSAGES, settings.effective(None))

    assert reason is None
    assert decision.model == "kimi-k3"
    assert decision.effort == "high"
    assert decision.model_confidence == pytest.approx(0.91)
    assert decision.effort_confidence == pytest.approx(0.66)
    assert decision.model_choice == "2"
    assert decision.latency_ms >= 0

    # The grid position is offered to Jev; `choice` returns that key, and `probabilities`
    # is the full distribution over the criteria we sent (docs/jev-decisions-api.md).
    assert set(decision.model_probabilities) == {"1", "2", "3", "4", "5", "6"}
    assert decision.model_probabilities["2"] == pytest.approx(0.91)


def test_kimi_effort_override_is_applied(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    transport = StubTransport([StubResponse(decision_payload("2", 0.9, "medium", 0.9))])
    client, settings = make(transport)

    decision, _ = client.decide(MESSAGES, settings.effective(None))

    assert decision.effort_requested == "medium"
    assert decision.effort == "high"


def test_missing_api_key_short_circuits(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    transport = StubTransport([])
    client, settings = make(transport)

    decision, reason = client.decide(MESSAGES, settings.effective(None))

    assert decision is None
    assert reason == REASON_NO_API_KEY
    assert transport.call_count == 0


def test_timeout(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    def handler(*_args, **_kwargs):
        raise ReadTimeout("slow")

    client, settings = make(StubTransport(handler=handler))

    decision, reason = client.decide(MESSAGES, settings.effective(None))
    assert decision is None
    assert reason == REASON_TIMEOUT


def test_upstream_error(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    client, settings = make(StubTransport([StubResponse(status_code=500, text="boom")]))

    decision, reason = client.decide(MESSAGES, settings.effective(None))
    assert decision is None
    assert reason == REASON_UPSTREAM_ERROR


def test_non_json_body(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    client, settings = make(StubTransport([StubResponse(payload=None, status_code=200, text="<html>")]))

    decision, reason = client.decide(MESSAGES, settings.effective(None))
    assert decision is None
    assert reason == REASON_MALFORMED


def test_missing_answers(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    client, settings = make(StubTransport([StubResponse({"answers": {}})]))

    decision, reason = client.decide(MESSAGES, settings.effective(None))
    assert decision is None
    assert reason == REASON_MALFORMED


def test_unknown_model_choice_degrades_to_the_fallback(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    client, settings = make(StubTransport([StubResponse(decision_payload("99", 0.99))]))

    decision, reason = client.decide(MESSAGES, settings.effective(None))

    assert reason is None
    assert decision.model == "deepseek-v4.1-flash"
    assert REASON_UNKNOWN_CHOICE in decision.fallback_reasons


def test_low_confidence_degrades(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    client, settings = make(StubTransport([StubResponse(decision_payload("2", 0.2, "high", 0.2))]))

    decision, _ = client.decide(MESSAGES, settings.effective(None))

    assert decision.model == "deepseek-v4.1-flash"
    assert decision.effort == "medium"
    assert decision.fallback_reasons.count(REASON_LOW_CONFIDENCE) == 2


def test_a_confident_model_with_an_unconfident_effort_only_degrades_the_effort(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    client, settings = make(StubTransport([StubResponse(decision_payload("2", 0.95, "high", 0.10))]))

    decision, _ = client.decide(MESSAGES, settings.effective(None))

    assert decision.model == "kimi-k3"
    # Degraded to the configured default ("medium"), then translated for the model family:
    # Kimi K3's documented middle is `high`, so that is what goes on the wire.
    assert decision.effort_requested == "medium"
    assert decision.effort == "high"
    assert decision.fallback_reasons == (REASON_LOW_CONFIDENCE,)


def test_non_choice_answer_types_are_treated_as_malformed(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    client, settings = make(
        StubTransport(
            [StubResponse(decision_payload("1", 0.9, "medium", 0.9, model_type="noul", effort_type="noul"))]
        )
    )

    decision, _ = client.decide(MESSAGES, settings.effective(None))

    # `choice` is empty on a wrongly-typed answer, so the model is off-grid and the turn
    # falls back rather than reading a probability as a model name.
    assert decision.model == "deepseek-v4.1-flash"
    assert REASON_UNKNOWN_CHOICE in decision.fallback_reasons


def test_custom_endpoint_is_honoured(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    transport = StubTransport([StubResponse(decision_payload())])
    client, settings = make(transport, config={"endpoint": "https://example.invalid/decisions"})

    client.decide(MESSAGES, settings.effective(None))

    assert transport.calls[0]["url"] == "https://example.invalid/decisions"


# -- a surface policy drives the decision path -------------------------------------
#
# The snapshot the router builds once per request reaches the client in place of the grid:
# the grid offered to Jev, both threshold reads, the fallback model and the fallback effort
# all come from it, and `max_effort` caps the level the router emits before the per-family
# table resolves it.


def _policy_config(**policy):
    return {"platform_overrides": {"cron": policy}}


def test_a_policy_grid_is_the_criteria_sent_to_jev(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    transport = StubTransport([StubResponse(decision_payload("1", 0.9, "medium", 0.9, grid_size=1))])
    client, settings = make(transport, config=_policy_config(grid="glm-5.3: rigorous reasoning"))

    effective = settings.effective(settings.policy_for("cron"))
    client.decide(MESSAGES, effective, platform="cron", provider="ollama-cloud")

    criteria = transport.last_payload["questions"]["model_route"]["criteria"]
    assert criteria == {"1": "glm-5.3: rigorous reasoning"}


def test_the_policy_threshold_degrades_below_it(monkeypatch):
    """A one-entry policy grid and a confidence under the policy threshold degrade the model."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    transport = StubTransport([StubResponse(decision_payload("1", 0.2, "medium", 0.9, grid_size=1))])
    client, settings = make(
        transport,
        config=_policy_config(grid="glm-5.3: rigorous reasoning", confidence_threshold=0.7),
    )

    effective = settings.effective(settings.policy_for("cron"))
    decision, reason = client.decide(MESSAGES, effective, platform="cron")

    assert reason is None
    # The single-entry grid is the fallback grid too, so the degraded answer stays inside it.
    assert decision.model == "glm-5.3"
    assert REASON_LOW_CONFIDENCE in decision.fallback_reasons


def test_an_off_grid_answer_degrades_to_the_policy_grid(monkeypatch):
    """A one-entry policy grid with Jev answering an off-grid position falls back inside it."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    transport = StubTransport([StubResponse(decision_payload("2", 0.9, "medium", 0.9, grid_size=1))])
    client, settings = make(transport, config=_policy_config(grid="glm-5.3: rigorous reasoning"))

    effective = settings.effective(settings.policy_for("cron"))
    decision, _ = client.decide(MESSAGES, effective, platform="cron")

    assert decision.model == "glm-5.3"
    assert REASON_UNKNOWN_CHOICE in decision.fallback_reasons


def test_a_cap_lowers_a_high_answer_and_leaves_low_and_medium_untouched(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    cases = [("low", "low"), ("medium", "medium"), ("high", "medium")]
    for answer, expected in cases:
        transport = StubTransport([StubResponse(decision_payload("1", 0.9, answer, 0.9))])
        client, settings = make(transport, config=_policy_config(max_effort="medium"))

        effective = settings.effective(settings.policy_for("cron"))
        decision, _ = client.decide(MESSAGES, effective, platform="cron")

        assert decision.model == "deepseek-v4.1-flash"
        assert decision.effort_requested == expected, answer
        # DeepSeek V4.1 accepts the three levels, so the emitted value is the requested one.
        assert decision.effort == expected


def test_a_cap_lowers_the_default_effort_fallback_too(monkeypatch):
    """`max_effort` caps the fallback path, not only Jev's answer."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    transport = StubTransport([StubResponse(decision_payload("1", 0.9, "high", 0.10))])
    client, settings = make(
        transport, config={**_policy_config(max_effort="low"), "default_effort": "high"}
    )

    effective = settings.effective(settings.policy_for("cron"))
    decision, _ = client.decide(MESSAGES, effective, platform="cron")

    assert decision.effort_requested == "low"
    assert decision.effort == "low"
    assert REASON_LOW_CONFIDENCE in decision.fallback_reasons


def test_a_cap_never_raises_a_confident_low_answer(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    transport = StubTransport([StubResponse(decision_payload("1", 0.9, "low", 0.9))])
    client, settings = make(transport, config=_policy_config(max_effort="medium"))

    effective = settings.effective(settings.policy_for("cron"))
    decision, _ = client.decide(MESSAGES, effective, platform="cron")

    assert decision.effort_requested == "low"
    assert decision.effort == "low"


def test_an_absent_effort_answer_is_omitted_without_a_cap(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    transport = StubTransport([StubResponse(decision_payload("1", 0.9, include_effort=False))])
    client, settings = make(transport)

    decision, _ = client.decide(MESSAGES, settings.effective(None))

    assert decision.effort is None
    assert decision.effort_requested is None


def test_an_absent_effort_answer_emits_the_cap_when_one_is_in_force(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    transport = StubTransport([StubResponse(decision_payload("1", 0.9, include_effort=False))])
    client, settings = make(transport, config=_policy_config(max_effort="medium"))

    effective = settings.effective(settings.policy_for("cron"))
    decision, _ = client.decide(MESSAGES, effective, platform="cron")

    assert decision.effort_requested == "medium"
    assert decision.effort == "medium"


def test_a_policy_grid_resolves_the_fallback_inside_it_when_the_default_is_off_grid(monkeypatch):
    """A policy surface never emits the off-grid global default; it re-pins inside its grid."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    transport = StubTransport([StubResponse(decision_payload("99", 0.99, "medium", 0.9, grid_size=1))])
    client, settings = make(
        transport,
        config={
            **_policy_config(grid="glm-5.3: rigorous reasoning"),
            "default_model": "deepseek-v4.1-flash",  # absent from the one-entry cron grid
        },
    )

    effective = settings.effective(settings.policy_for("cron"))
    assert effective.grid_from_policy is True
    decision, _ = client.decide(MESSAGES, effective, platform="cron")

    assert decision.model == "glm-5.3"
    assert REASON_UNKNOWN_CHOICE in decision.fallback_reasons


def test_a_snapshot_without_a_policy_grid_emits_the_off_grid_default_verbatim(monkeypatch):
    """Review finding 1: the off-grid re-pin is a policy-surface behaviour only.

    An operator whose global grid excludes the configured ``default_model`` and who never sets
    ``platform_overrides`` must keep that configured model on the wire, exactly as before this
    change — the global grid is not a policy grid.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    transport = StubTransport([StubResponse(decision_payload("99", 0.99, "medium", 0.9, grid_size=1))])
    client, settings = make(
        transport,
        config={
            "grid": "kimi-k3: code",  # a global grid that excludes the configured default
            "default_model": "deepseek-v4.1-flash",
        },
    )

    effective = settings.effective(None)
    assert effective.grid_from_policy is False
    decision, _ = client.decide(MESSAGES, effective)

    assert decision.model == "deepseek-v4.1-flash"
    assert decision.model != "kimi-k3"


# -- openai_compatible backend tests ------------------------------------------


def test_openai_compatible_decider_happy_path(monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-opencode-secret-123")
    resp_payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "model": "deepseek-v4.1-flash",
                            "effort": "high",
                            "confidence": 0.95,
                        }
                    )
                }
            }
        ]
    }
    transport = StubTransport([StubResponse(resp_payload)])
    client, settings = make(
        transport,
        config={
            "decider": "openai_compatible",
            "decider_base_url": "https://opencode.ai/zen/go/v1",
            "decider_model": "deepseek-v4.1-flash",
            "decider_api_key_env": "OPENCODE_GO_API_KEY",
            "routable_providers": ["opencode-go"],
        },
    )
    effective = settings.effective(None)
    decision, reason = client.decide(MESSAGES, effective, provider="opencode-go")

    assert reason is None
    assert decision is not None
    assert decision.model == "deepseek-v4.1-flash"
    assert decision.effort_requested == "high"
    assert decision.model_confidence == 0.95
    assert decision.decider == "openai_compatible"

    # Verify wire request
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"] == "https://opencode.ai/zen/go/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-opencode-secret-123"
    assert call["json"]["model"] == "deepseek-v4.1-flash"
    assert call["json"]["response_format"] == {"type": "json_object"}


def test_openai_compatible_missing_api_key(monkeypatch):
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    transport = StubTransport([])
    client, settings = make(
        transport,
        config={
            "decider": "openai_compatible",
            "decider_base_url": "https://opencode.ai/zen/go/v1",
            "decider_api_key_env": "OPENCODE_GO_API_KEY",
        },
    )
    decision, reason = client.decide(MESSAGES, settings.effective(None))
    assert decision is None
    assert reason == REASON_NO_API_KEY
    assert transport.call_count == 0


def test_openai_compatible_timeout(monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-test")
    transport = StubTransport([ReadTimeout("timed out")])
    client, settings = make(
        transport,
        config={
            "decider": "openai_compatible",
            "decider_base_url": "https://opencode.ai/zen/go/v1",
            "decider_api_key_env": "OPENCODE_GO_API_KEY",
        },
    )
    decision, reason = client.decide(MESSAGES, settings.effective(None))
    assert decision is None
    assert reason == REASON_TIMEOUT


def test_openai_compatible_upstream_error(monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-test")
    transport = StubTransport([StubResponse(status_code=500, text="Internal Error")])
    client, settings = make(
        transport,
        config={
            "decider": "openai_compatible",
            "decider_base_url": "https://opencode.ai/zen/go/v1",
            "decider_api_key_env": "OPENCODE_GO_API_KEY",
        },
    )
    decision, reason = client.decide(MESSAGES, settings.effective(None))
    assert decision is None
    assert reason == REASON_UPSTREAM_ERROR


def test_openai_compatible_malformed_json_content(monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-test")
    resp_payload = {"choices": [{"message": {"content": "not-valid-json"}}]}
    transport = StubTransport([StubResponse(resp_payload)])
    client, settings = make(
        transport,
        config={
            "decider": "openai_compatible",
            "decider_base_url": "https://opencode.ai/zen/go/v1",
            "decider_api_key_env": "OPENCODE_GO_API_KEY",
        },
    )
    decision, reason = client.decide(MESSAGES, settings.effective(None))
    assert decision is None
    assert reason == REASON_MALFORMED


def test_openai_compatible_markdown_json_fence_handled(monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-test")
    fenced = "```json\n{\"model\": \"kimi-k3\", \"effort\": \"medium\", \"confidence\": 0.88}\n```"
    resp_payload = {"choices": [{"message": {"content": fenced}}]}
    transport = StubTransport([StubResponse(resp_payload)])
    client, settings = make(
        transport,
        config={
            "decider": "openai_compatible",
            "decider_base_url": "https://opencode.ai/zen/go/v1",
            "decider_api_key_env": "OPENCODE_GO_API_KEY",
        },
    )
    decision, reason = client.decide(MESSAGES, settings.effective(None))
    assert reason is None
    assert decision is not None
    assert decision.model == "kimi-k3"


def test_openai_compatible_off_grid_model_fails_open(monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-test")
    resp_payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {"model": "non-existent-model", "effort": "high", "confidence": 0.99}
                    )
                }
            }
        ]
    }
    transport = StubTransport([StubResponse(resp_payload)])
    client, settings = make(
        transport,
        config={
            "decider": "openai_compatible",
            "decider_base_url": "https://opencode.ai/zen/go/v1",
            "decider_api_key_env": "OPENCODE_GO_API_KEY",
            "default_model": "deepseek-v4.1-flash",
        },
    )
    decision, reason = client.decide(MESSAGES, settings.effective(None))
    assert decision is None
    assert reason == REASON_UNKNOWN_CHOICE


def test_openai_compatible_low_confidence_fails_open(monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-test")
    resp_payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {"model": "kimi-k3", "effort": "high", "confidence": 0.2}
                    )
                }
            }
        ]
    }
    transport = StubTransport([StubResponse(resp_payload)])
    client, settings = make(
        transport,
        config={
            "decider": "openai_compatible",
            "decider_base_url": "https://opencode.ai/zen/go/v1",
            "decider_api_key_env": "OPENCODE_GO_API_KEY",
            "confidence_threshold": 0.5,
            "default_model": "deepseek-v4.1-flash",
        },
    )
    decision, reason = client.decide(MESSAGES, settings.effective(None))
    assert decision is None
    assert reason == REASON_LOW_CONFIDENCE


def test_openai_compatible_invalid_confidence_fails_open(monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-test")
    for bad_conf in ("garbage", -0.1, 1.5, None):
        resp_payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"model": "kimi-k3", "effort": "high", "confidence": bad_conf}
                        )
                    }
                }
            ]
        }
        transport = StubTransport([StubResponse(resp_payload)])
        client, settings = make(
            transport,
            config={
                "decider": "openai_compatible",
                "decider_base_url": "https://opencode.ai/zen/go/v1",
                "decider_api_key_env": "OPENCODE_GO_API_KEY",
            },
        )
        decision, reason = client.decide(MESSAGES, settings.effective(None))
        assert decision is None
        assert reason == REASON_MALFORMED


# -- jev_console backend tests ------------------------------------------------

def test_jev_console_decider_happy_path(monkeypatch):
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key-123")
    resp_payload = decision_payload(model_choice="1", model_confidence=0.9, grid_size=2)
    transport = StubTransport([StubResponse(resp_payload)])
    client, settings = make(
        transport,
        config={
            "decider": "jev_console",
            "confidence_threshold": 0.5,
        },
    )
    decision, reason = client.decide(
        MESSAGES,
        settings.effective(None, provider="opencode-console"),
        provider="opencode-console",
    )
    assert reason is None
    assert decision is not None
    assert decision.decider == "jev_console"
    assert decision.model == "deepseek-v4.1-flash"
    assert decision.model_confidence == 0.9

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"] == "https://opencode.ai/inference/systemone/v1/systemone"
    assert call["headers"]["Authorization"] == "Bearer test-console-key-123"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["json"]["model"] == "jev-1.13-free"
    assert "state" in call["json"]
    assert "questions" in call["json"]


def test_jev_console_decider_fallback_to_paid_jev(monkeypatch):
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key-123")
    # First call (free jev) fails with 500; second call (paid jev) succeeds with 200
    resp_payload = decision_payload(model_choice="2", model_confidence=0.88, grid_size=2)
    transport = StubTransport([
        StubResponse({"error": "free quota exhausted"}, status_code=500),
        StubResponse(resp_payload, status_code=200),
    ])
    client, settings = make(
        transport,
        config={
            "decider": "jev_console",
        },
    )
    decision, reason = client.decide(
        MESSAGES,
        settings.effective(None, provider="opencode-console"),
        provider="opencode-console",
    )
    assert reason is None
    assert decision is not None
    assert decision.model == "mimo-v2.6-flash"
    assert len(transport.calls) == 2
    assert transport.calls[0]["json"]["model"] == "jev-1.13-free"
    assert transport.calls[1]["json"]["model"] == "jev-1.13"


def test_jev_console_decider_missing_api_key(monkeypatch):
    monkeypatch.delenv("OPENCODE_CONSOLE_API_KEY", raising=False)
    transport = StubTransport([])
    client, settings = make(
        transport,
        config={
            "decider": "jev_console",
        },
    )
    decision, reason = client.decide(MESSAGES, settings.effective(None))
    assert decision is None
    assert reason == REASON_NO_API_KEY
    assert len(transport.calls) == 0


def test_jev_console_decider_both_models_fail(monkeypatch):
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key-123")
    transport = StubTransport([
        StubResponse({"error": "server error 1"}, status_code=500),
        StubResponse({"error": "server error 2"}, status_code=502),
    ])
    client, settings = make(
        transport,
        config={
            "decider": "jev_console",
        },
    )
    decision, reason = client.decide(MESSAGES, settings.effective(None))
    assert decision is None
    assert reason == REASON_UPSTREAM_ERROR
    assert len(transport.calls) == 2


def test_jev_console_decider_malformed_json(monkeypatch):
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key-123")
    transport = StubTransport([StubResponse({"invalid": "structure"})])
    client, settings = make(
        transport,
        config={
            "decider": "jev_console",
            "console_jev_fallback_model": "",
        },
    )
    decision, reason = client.decide(MESSAGES, settings.effective(None))
    assert decision is None
    assert reason == REASON_MALFORMED


def test_jev_console_decider_low_confidence_degrades(monkeypatch):
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key-123")
    resp_payload = decision_payload(model_choice="2", model_confidence=0.2, grid_size=2)
    transport = StubTransport([StubResponse(resp_payload)])
    client, settings = make(
        transport,
        config={
            "decider": "jev_console",
            "confidence_threshold": 0.5,
            "console_jev_fallback_model": "",
        },
    )
    decision, reason = client.decide(
        MESSAGES,
        settings.effective(None, provider="opencode-console"),
        provider="opencode-console",
    )
    assert decision is None
    assert reason == REASON_LOW_CONFIDENCE


def test_jev_console_decider_fallback_model(monkeypatch):
    # Explicit alias matching EVIDENCIAS_OPERACIONAIS.md
    return test_jev_console_decider_fallback_to_paid_jev(monkeypatch)


def test_jev_console_decider_network_failure_fail_open(monkeypatch):
    # Explicit alias matching EVIDENCIAS_OPERACIONAIS.md
    return test_jev_console_decider_both_models_fail(monkeypatch)


def test_jev_console_decider_timeout(monkeypatch):
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key-123")
    transport = StubTransport([ReadTimeout("connection timeout")])
    client, settings = make(
        transport,
        config={
            "decider": "jev_console",
            "console_jev_fallback_model": "",
        },
    )
    decision, reason = client.decide(MESSAGES, settings.effective(None))
    assert decision is None
    assert reason == REASON_TIMEOUT


@pytest.mark.parametrize("status_code", [401, 403, 429, 500, 503])
def test_jev_console_decider_http_error_matrix(monkeypatch, status_code, caplog):
    key = "test-console-key-secret-999"
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", key)
    transport = StubTransport([StubResponse({"error": f"error with {key}"}, status_code=status_code)])
    client, settings = make(
        transport,
        config={
            "decider": "jev_console",
            "console_jev_fallback_model": "",
        },
    )
    decision, reason = client.decide(MESSAGES, settings.effective(None))
    assert decision is None
    assert reason == REASON_UPSTREAM_ERROR
    # Verify secret is never logged in plaintext
    assert key not in caplog.text


def test_jev_console_decider_real_non_json_body(monkeypatch, caplog):
    key = "test-console-key-secret-888"
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", key)
    transport = StubTransport([StubResponse(payload=None, status_code=200, text=f"<html>Bad Gateway {key}</html>")])
    client, settings = make(
        transport,
        config={
            "decider": "jev_console",
            "console_jev_fallback_model": "",
        },
    )
    decision, reason = client.decide(MESSAGES, settings.effective(None))
    assert decision is None
    assert reason == REASON_MALFORMED
    # Verify secret is redacted
    assert key not in caplog.text


def test_jev_console_decider_response_defined_probability_domain(monkeypatch):
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key-123")
    payload = decision_payload("2", 0.99, "high", 0.8, grid_size=2)
    answer = payload["answers"]["model_route"]
    answer["criteria"] = {"outsider": "unrequested option"}
    answer["probabilities"] = {"outsider": 1.0}
    transport = StubTransport([StubResponse(payload)])
    client, settings = make(
        transport,
        config={
            "decider": "jev_console",
            "console_jev_fallback_model": "",
        },
    )
    decision, reason = client.decide(
        MESSAGES,
        settings.effective(None, provider="opencode-console"),
        provider="opencode-console",
    )
    assert decision is None
    assert reason == REASON_MALFORMED


def test_jev_console_decider_invalid_types_and_float_choice(monkeypatch):
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key-123")
    # Float choice
    payload_float = decision_payload("2", 0.99, "high", 0.8, grid_size=2)
    payload_float["answers"]["model_route"]["choice"] = 2.0
    # Non-choice type
    payload_bad_type = decision_payload("2", 0.99, "high", 0.8, grid_size=2)
    payload_bad_type["answers"]["model_route"]["type"] = "free_text"
    # Numeric effort choice
    payload_bad_effort = decision_payload("2", 0.99, "high", 0.8, grid_size=2)
    payload_bad_effort["answers"]["reasoning_effort"]["choice"] = 3
    payload_bad_effort["answers"]["reasoning_effort"]["probabilities"] = {"3": 1.0}

    transport = StubTransport([
        StubResponse(payload_float),
        StubResponse(payload_bad_type),
        StubResponse(payload_bad_effort),
    ])
    client, settings = make(
        transport,
        config={
            "decider": "jev_console",
            "console_jev_fallback_model": "",
        },
    )
    # 1: float choice -> unknown choice
    dec1, r1 = client.decide(MESSAGES, settings.effective(None, provider="opencode-console"), provider="opencode-console")
    assert dec1 is None
    assert r1 == REASON_UNKNOWN_CHOICE

    # 2: bad type -> malformed
    dec2, r2 = client.decide(MESSAGES, settings.effective(None, provider="opencode-console"), provider="opencode-console")
    assert dec2 is None
    assert r2 == REASON_MALFORMED

    # 3: bad effort -> unknown choice
    dec3, r3 = client.decide(MESSAGES, settings.effective(None, provider="opencode-console"), provider="opencode-console")
    assert dec3 is None
    assert r3 == REASON_UNKNOWN_CHOICE

