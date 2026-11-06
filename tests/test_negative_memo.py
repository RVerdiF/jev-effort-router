"""The negative memo: a failed turn's decision is not re-asked inside its own tool loop.

Measured on the live audit file, a turn whose answer fell below ``confidence_threshold`` called
the decider once per provider request (~9 calls in a failing turn): same state, same question,
same unusable answer. The marker recorded here says "this turn already asked and failed" and the
replay path skips without a decider call, recording ``replayed: true``.
"""

from __future__ import annotations

import json

from stubs import ReadTimeout, StubResponse, StubTransport, decision_payload

from test_router import build, route

#: The live decider is ``jev_console``; it is also the one that fails open (``None``) on a
#: low-confidence answer, which is the failure this memo exists for. A six-model grid keeps the
#: model question in play; the Console fallback model is disabled so one failure is one call.
CONSOLE_CONFIG = {
    "decider": "jev_console",
    "routable_providers": ["ollama-cloud"],
    "console_jev_fallback_model": "",
}


def _build(tmp_path, monkeypatch, transport, config=None):
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key")
    router, settings = build(tmp_path, transport, config=dict(config or CONSOLE_CONFIG))
    monkeypatch.setattr(router, "_data_dir", lambda: str(tmp_path / "audit"))
    return router, settings


def _skips(router, settings):
    return [record for record in router.tail(settings, limit=20) if record["event"] == "skip"]


def _low_confidence_transport(calls: int = 1) -> StubTransport:
    return StubTransport(
        [
            StubResponse(decision_payload(model_choice="2", model_confidence=0.2, grid_size=6))
            for _ in range(calls)
        ]
    )


def test_a_low_confidence_turn_calls_the_decider_once(tmp_path, monkeypatch):
    transport = _low_confidence_transport()
    router, settings = _build(tmp_path, monkeypatch, transport)

    results = [route(router, turn_id="turn-1", api_call_count=index) for index in range(5)]

    assert results == [None] * 5
    assert transport.call_count == 1
    skips = _skips(router, settings)
    assert len(skips) == 5
    assert skips[0]["reason"] == "low_confidence"
    assert "replayed" not in skips[0]
    assert all(skip["reason"] == "low_confidence" and skip["replayed"] is True for skip in skips[1:])
    # The replay names the same turn it replayed, so a reader can group the calls.
    assert {skip["turn_id"] for skip in skips} == {"turn-1"}


def test_a_timed_out_turn_calls_the_decider_once(tmp_path, monkeypatch):
    transport = StubTransport([ReadTimeout("slow decider")])
    router, settings = _build(tmp_path, monkeypatch, transport)

    results = [route(router, turn_id="turn-timeout", api_call_count=index) for index in range(4)]

    assert results == [None] * 4
    assert transport.call_count == 1
    skips = _skips(router, settings)
    # The remembered reason survives the replay: a timeout is not relabelled low_confidence.
    assert [skip["reason"] for skip in skips] == ["timeout"] * 4
    assert skips[0].get("replayed") is None
    assert all(skip["replayed"] is True for skip in skips[1:])


def test_each_turn_asks_again(tmp_path, monkeypatch):
    """The marker is per turn: a new user turn is a new question and must be routed."""
    transport = _low_confidence_transport(calls=2)
    router, _ = _build(tmp_path, monkeypatch, transport)

    assert route(router, turn_id="turn-1") is None
    assert route(router, turn_id="turn-1", api_call_count=1) is None
    assert transport.call_count == 1

    assert route(router, turn_id="turn-2") is None
    assert transport.call_count == 2


def test_a_failure_outside_the_memoised_set_is_retried(tmp_path, monkeypatch):
    """A 500 is worth retrying; only ``low_confidence`` and ``timeout`` become markers."""
    transport = StubTransport(
        [StubResponse({"error": "boom"}, status_code=500) for _ in range(3)]
    )
    router, settings = _build(tmp_path, monkeypatch, transport)

    assert [route(router, turn_id="turn-5xx", api_call_count=index) for index in range(3)] == [None] * 3

    assert transport.call_count == 3
    assert [skip["reason"] for skip in _skips(router, settings)] == ["upstream_error"] * 3


def test_the_marker_does_not_suppress_a_session_when_routing_per_session(tmp_path, monkeypatch):
    """``route_per_turn: false`` memoises per session; a failed request must not poison 24h."""
    transport = _low_confidence_transport(calls=2)
    router, _ = _build(
        tmp_path,
        monkeypatch,
        transport,
        config={**CONSOLE_CONFIG, "route_per_turn": False},
    )

    assert route(router, turn_id="turn-1", session_id="session-a") is None
    assert route(router, turn_id="turn-1", session_id="session-a", api_call_count=1) is None

    assert transport.call_count == 2


def test_a_replayed_marker_is_a_skip_record_without_a_decision(tmp_path, monkeypatch):
    """The record proves the difference between the first failure and its replay."""
    transport = _low_confidence_transport()
    router, settings = _build(tmp_path, monkeypatch, transport)

    route(router, turn_id="turn-audit")
    route(router, turn_id="turn-audit", api_call_count=1)

    records = router.tail(settings, limit=20)
    assert [record["event"] for record in records] == ["skip", "skip"]
    assert records[0]["reason"] == "low_confidence"
    assert records[1]["replayed"] is True
    assert records[1]["turn_id"] == "turn-audit"
    # A skip never claims a routed model, and the audit text carries no user content.
    assert "model" not in records[1]
    assert "user_message" not in json.dumps(records[1])
