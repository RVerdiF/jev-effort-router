"""Decider provenance in the audit record.

The record must name the decider model that *actually answered the call*. ``settings.jev_model`` is
the legacy OpenRouter id, so reading it while another decider is active stamps the record with a
model that was never consulted — the bug this module pins down. ``decider_fallback_reason`` carries
why the backend's fallback model answered when the primary failed over.
"""

from __future__ import annotations

import json

from stubs import StubResponse, StubTransport, decision_payload, ollama_request

from test_router import build, route

CONSOLE_PRIMARY = "jev-1.13-free"
CONSOLE_FALLBACK = "jev-1.13"
LEGACY_OPENROUTER = "typesafe/jev-1.13"


def _last_record(router, settings):
    records = router.tail(settings, limit=50)
    assert records, "the audit file must hold the attempt"
    return records[-1]


def test_jev_console_audit_names_the_console_model_that_answered(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key")
    transport = StubTransport(
        [StubResponse(decision_payload(model_choice="1", model_confidence=0.95, grid_size=1))]
    )
    router, settings = build(
        tmp_path,
        transport,
        config={"decider": "jev_console", "routable_providers": ["opencode-console"]},
    )

    assert route(router, ollama_request(), provider="opencode-console") is not None

    record = _last_record(router, settings)
    assert record["decider"] == "jev_console"
    assert record["jev_model"] == CONSOLE_PRIMARY
    assert record["decider_fallback_reason"] is None
    # The wire agrees with the record: the console model above is the one that was asked.
    assert transport.calls[0]["json"]["model"] == CONSOLE_PRIMARY
    assert LEGACY_OPENROUTER not in json.dumps(record)


def test_jev_console_audit_names_the_fallback_model_and_why_it_answered(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_CONSOLE_API_KEY", "test-console-key")
    transport = StubTransport(
        [
            StubResponse({"error": "free quota exhausted"}, status_code=500),
            StubResponse(decision_payload(model_choice="1", model_confidence=0.95, grid_size=1)),
        ]
    )
    router, settings = build(
        tmp_path,
        transport,
        config={
            "decider": "jev_console",
            "routable_providers": ["opencode-console"],
            "console_jev_fallback_model": CONSOLE_FALLBACK,
        },
    )

    assert route(router, ollama_request(), provider="opencode-console") is not None

    assert [call["json"]["model"] for call in transport.calls] == [CONSOLE_PRIMARY, CONSOLE_FALLBACK]
    record = _last_record(router, settings)
    assert record["jev_model"] == CONSOLE_FALLBACK
    assert record["decider_fallback_reason"] == "upstream_error"
    assert LEGACY_OPENROUTER not in json.dumps(record)

    # A replay inside the same turn consults no decider at all, so it must re-emit the provenance
    # of the decision it replays instead of falling back to the configured primary.
    assert (
        route(
            router,
            ollama_request(),
            provider="opencode-console",
            api_call_count=1,
            api_request_id="turn-1:api:1",
        )
        is not None
    )
    replayed = _last_record(router, settings)
    assert replayed["replayed"] is True
    assert replayed["jev_model"] == CONSOLE_FALLBACK
    assert replayed["decider_fallback_reason"] == "upstream_error"
    assert transport.call_count == 2


def test_jev_decider_audit_keeps_the_jev_model(tmp_path):
    transport = StubTransport([StubResponse(decision_payload("2", 0.9, "high", 0.8))])
    router, settings = build(tmp_path, transport)

    assert route(router, ollama_request()) is not None

    record = _last_record(router, settings)
    assert record["decider"] == "jev"
    assert record["jev_model"] == LEGACY_OPENROUTER
    assert record["decider_fallback_reason"] is None


def test_openai_compatible_audit_records_its_own_decider_model(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-secret")
    transport = StubTransport(
        [
            StubResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"model": "deepseek-v4.1-flash", "effort": "medium", "confidence": 0.92}
                                )
                            }
                        }
                    ]
                }
            )
        ]
    )
    router, settings = build(
        tmp_path,
        transport,
        config={
            "decider": "openai_compatible",
            "decider_base_url": "https://opencode.ai/zen/go/v1",
            "decider_model": "deepseek-v4.1-flash",
            "decider_api_key_env": "OPENCODE_GO_API_KEY",
            "routable_providers": ["opencode-go"],
            "effort_maps": {"opencode-go": {"deepseek-v4.1-flash": ["low", "medium", "high"]}},
        },
    )

    assert route(router, ollama_request(), provider="opencode-go") is not None

    record = _last_record(router, settings)
    assert record["decider"] == "openai_compatible"
    assert record["jev_model"] == "deepseek-v4.1-flash"
    assert record["decider_fallback_reason"] is None
    assert LEGACY_OPENROUTER not in json.dumps(record)
