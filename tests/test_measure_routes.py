"""Tests for ``scripts/measure_routes.py`` (stdout-only measurement of the decider).

The script is standalone (stdlib, no package imports), so it is loaded by path. Plain
``unittest`` keeps the tests runnable with ``python3 -m unittest`` as well as pytest.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "measure_routes.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("measure_routes_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


measure = _load_module()

SINCE = datetime(2026, 11, 1, tzinfo=timezone.utc)
UNTIL = datetime(2026, 11, 8, tzinfo=timezone.utc)


def _skip(ts, provider, reason, turn="t1", session="s1", platform="desktop"):
    return {
        "ts": ts,
        "event": "skip",
        "reason": reason,
        "provider": provider,
        "configured_model": "m",
        "turn_id": turn,
        "session_id": session,
        "api_request_id": f"{turn}:api:1",
        "platform": platform,
    }


def _route(ts, provider, effort, *, replayed=False, confidence=0.8, turn="t1",
           session="s1", platform="desktop", latency=100):
    return {
        "ts": ts,
        "event": "route",
        "model": "m",
        "wire_model": "m",
        "effort": effort,
        "effort_requested": effort,
        "model_choice": "1",
        "provider": provider,
        "effort_choice": None if replayed else effort,
        "model_confidence": 1.0,
        "effort_confidence": confidence,
        "effort_probabilities": None if replayed else {"low": 0.1, "medium": 0.1, "high": 0.8},
        "latency_ms": 0 if replayed else latency,
        "fallback_reasons": [],
        "decider": "jev_console",
        "replayed": replayed,
        "turn_id": turn,
        "session_id": session,
        "api_request_id": f"{turn}:api:1",
        "platform": platform,
    }


class AggregateTest(unittest.TestCase):
    def aggregate(self, records, routable=("opencode-go",), host_effort="high"):
        return measure.aggregate(
            records,
            since=SINCE,
            until=UNTIL,
            routable=routable,
            host_effort=host_effort,
            confidence_threshold=0.5,
        )

    def test_decider_calls_count_skips_and_fresh_routes_but_not_replays(self):
        records = [
            _skip("2026-11-02T10:00:00+00:00", "opencode-go", "low_confidence", turn="t1"),
            _skip("2026-11-02T10:00:01+00:00", "opencode-go", "low_confidence", turn="t1"),
            _route("2026-11-02T10:00:02+00:00", "opencode-go", "high", turn="t1"),
            _route("2026-11-02T10:00:03+00:00", "opencode-go", "high", replayed=True, turn="t1"),
        ]
        metrics = self.aggregate(records)
        self.assertEqual(metrics["per_turn"]["decider_calls"], 3)
        self.assertEqual(metrics["per_turn"]["decider_calls_per_turn"], 3.0)
        self.assertEqual(metrics["per_call"]["memo_replays"], 1)
        self.assertEqual(metrics["per_call"]["routable_calls"], 4)
        self.assertEqual(metrics["denominators"]["routable_turns"], 1)

    def test_predecision_skips_never_reach_the_decider_and_are_excluded(self):
        records = [
            _skip("2026-11-02T10:00:00+00:00", "anthropic", "provider_not_routed", turn="a1"),
            _route("2026-11-02T10:00:01+00:00", "opencode-go", "medium", turn="t1"),
        ]
        metrics = self.aggregate(records)
        self.assertEqual(metrics["denominators"]["routable_turns"], 1)
        self.assertEqual(metrics["per_turn"]["decider_calls"], 1)
        self.assertEqual(metrics["per_call"]["routable_calls"], 1)

    def test_skip_takes_the_host_effort_and_a_routed_turn_takes_its_own(self):
        records = [
            _skip("2026-11-02T10:00:00+00:00", "opencode-go", "low_confidence", turn="skipped"),
            _route("2026-11-02T10:00:05+00:00", "opencode-go", "low", replayed=True, turn="routed"),
            _route("2026-11-02T10:00:04+00:00", "opencode-go", "low", turn="routed"),
        ]
        metrics = self.aggregate(records)
        self.assertEqual(metrics["effort"]["turns"], {"high": 1, "low": 1})
        self.assertEqual(metrics["effort"]["calls"], {"high": 1, "low": 2})
        self.assertEqual(metrics["effort"]["high_share_of_calls"], 1 / 3)

    def test_turn_rates_use_turns_not_calls(self):
        records = [
            _skip("2026-11-02T10:00:00+00:00", "opencode-go", "low_confidence", turn="t1"),
            _skip("2026-11-02T10:00:01+00:00", "opencode-go", "low_confidence", turn="t1"),
            _route("2026-11-02T10:00:02+00:00", "opencode-go", "medium", turn="t2"),
            _route("2026-11-02T10:00:03+00:00", "opencode-go", "medium", turn="t3"),
        ]
        metrics = self.aggregate(records)
        self.assertAlmostEqual(metrics["per_turn"]["low_confidence_turn_rate"], 1 / 3)
        self.assertAlmostEqual(metrics["per_call"]["low_confidence_call_rate"], 2 / 4)

    def test_timeouts_and_upstream_errors_are_decider_calls(self):
        records = [
            _skip("2026-11-02T10:00:00+00:00", "opencode-go", "timeout", turn="t1"),
            _skip("2026-11-02T10:00:01+00:00", "opencode-go", "upstream_error", turn="t1"),
        ]
        metrics = self.aggregate(records)
        self.assertEqual(metrics["per_turn"]["decider_calls"], 2)
        self.assertEqual(metrics["per_turn"]["timeouts"], 1)
        self.assertEqual(metrics["per_turn"]["upstream_error_skips"], 1)
        self.assertEqual(metrics["per_turn"]["timeouts_per_turn"], 1.0)

    def test_window_is_half_open_on_ts(self):
        records = [
            _route("2026-10-31T23:59:59+00:00", "opencode-go", "high", turn="before"),
            _route("2026-11-02T00:00:00+00:00", "opencode-go", "high", turn="inside"),
            _route("2026-11-09T00:00:00+00:00", "opencode-go", "high", turn="after"),
        ]
        metrics = self.aggregate(records)
        self.assertEqual(metrics["denominators"]["routable_turns"], 1)
        self.assertEqual(metrics["scope"]["records_outside_window_or_bad_ts"], 2)

    def test_confidence_stats_cover_only_accepted_decisions(self):
        records = [
            _skip("2026-11-02T10:00:00+00:00", "opencode-go", "low_confidence", turn="t1"),
            _route("2026-11-02T10:00:01+00:00", "opencode-go", "high", confidence=0.52, turn="t2"),
            _route("2026-11-02T10:00:02+00:00", "opencode-go", "high", confidence=0.91, turn="t3"),
        ]
        metrics = self.aggregate(records)
        self.assertEqual(metrics["confidence"]["n"], 2)
        self.assertEqual(metrics["confidence"]["min"], 0.52)
        self.assertEqual(metrics["confidence"]["max"], 0.91)

    def test_surface_breakdown_splits_turns(self):
        records = [
            _route("2026-11-02T10:00:00+00:00", "opencode-go", "high", turn="k1", platform="kanban"),
            _skip("2026-11-02T10:00:01+00:00", "opencode-go", "low_confidence", turn="d1", platform="desktop"),
        ]
        metrics = self.aggregate(records)
        surfaces = {row["surface"]: row for row in metrics["surfaces"]}
        self.assertEqual(surfaces["kanban"]["high_share"], 1.0)
        self.assertEqual(surfaces["desktop"]["low_conf_turn_rate"], 1.0)


class ConfigTest(unittest.TestCase):
    def test_reads_the_routable_providers_block(self):
        text = """
plugins:
  entries:
    jev-effort-router:
      settings:
        enabled: true
        routable_providers:
          - opencode-go
          - 'commandcode'
        grids:
          opencode-go:
            - 'x: y'
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(text, encoding="utf-8")
            self.assertEqual(measure.read_routable_providers(path), ["opencode-go", "commandcode"])

    def test_missing_block_falls_back_to_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text("plugins:\n  entries: {}\n", encoding="utf-8")
            self.assertEqual(measure.read_routable_providers(path), [])

    def test_inline_list_is_understood(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text("        routable_providers: [opencode-go, commandcode]\n", encoding="utf-8")
            self.assertEqual(measure.read_routable_providers(path), ["opencode-go", "commandcode"])


class WindowTest(unittest.TestCase):
    def test_date_only_since_is_midnight_utc(self):
        self.assertEqual(
            measure._parse_instant("2026-11-03").isoformat(), "2026-11-03T00:00:00+00:00"
        )

    def test_date_only_until_is_end_of_day(self):
        parsed = measure._parse_instant("2026-11-03", end_of_day=True)
        self.assertEqual(parsed.date().isoformat(), "2026-11-03")
        self.assertGreater(parsed.hour, 23 - 1)

    def test_bad_date_raises_usage_error(self):
        with self.assertRaises(measure.UsageError):
            measure._parse_instant("05/11/2026")


class SessionsTest(unittest.TestCase):
    def _build_db(self, path: Path):
        con = sqlite3.connect(path)
        con.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, source TEXT, model TEXT, billing_provider TEXT,
                started_at REAL, ended_at REAL, message_count INTEGER, api_call_count INTEGER,
                output_tokens INTEGER, reasoning_tokens INTEGER, input_tokens INTEGER,
                estimated_cost_usd REAL
            );
            CREATE TABLE session_model_usage (
                session_id TEXT, model TEXT, billing_provider TEXT, task TEXT,
                api_call_count INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER,
                first_seen REAL
            );
            """
        )
        started = datetime(2026, 11, 2, 10, tzinfo=timezone.utc).timestamp()
        con.execute(
            "INSERT INTO sessions VALUES ('s1','desktop','m','opencode-go',?,NULL,4,10,1000,400,0,0)",
            (started,),
        )
        con.execute(
            "INSERT INTO sessions VALUES ('s2','cron','m','opencode-go',?,NULL,1,2,500,100,0,0)",
            (started,),
        )
        con.execute(
            "INSERT INTO session_model_usage VALUES ('s1','m','opencode-go','',10,1000,400,?)",
            (started,),
        )
        con.commit()
        con.close()

    def test_tokens_per_turn_use_routable_turn_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            self._build_db(db)
            result = measure.session_metrics(
                db,
                since=SINCE,
                until=UNTIL,
                routable=("opencode-go",),
                turns_by_session={"s1": 2},
            )
        self.assertTrue(result["available"])
        self.assertEqual(result["sessions_with_routable_turns"], 1)
        self.assertEqual(result["reasoning_tokens"], 400)
        self.assertEqual(result["reasoning_per_turn"], 200.0)
        self.assertEqual(result["reasoning_share_of_output"], 0.4)
        self.assertEqual(result["by_source"][0]["source"], "desktop")

    def test_missing_db_is_reported_not_raised(self):
        result = measure.session_metrics(
            Path("/nonexistent/state.db"),
            since=SINCE,
            until=UNTIL,
            routable=("opencode-go",),
            turns_by_session={},
        )
        self.assertFalse(result["available"])
        self.assertIn("not found", result["reason"])


class CliTest(unittest.TestCase):
    def _write_routes(self, path: Path):
        records = [
            _skip("2026-11-02T10:00:00+00:00", "opencode-go", "low_confidence", turn="t1"),
            _route("2026-11-02T10:00:01+00:00", "opencode-go", "high", turn="t2"),
        ]
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    def test_markdown_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            routes = Path(tmp) / "routes.jsonl"
            self._write_routes(routes)
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = measure.main(
                    [
                        "--routes", str(routes),
                        "--since", "2026-11-01",
                        "--until", "2026-11-07",
                        "--no-sessions",
                        "--routable-provider", "opencode-go",
                    ]
                )
            output = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("| chamadas ao decider por turno | 1,00 |", output)
        self.assertIn("| turnos em skip low_confidence | 50,0% |", output)
        self.assertIn("| high | 2 | 100,0% | 2 | 100,0% |", output)

    def test_json_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            routes = Path(tmp) / "routes.jsonl"
            self._write_routes(routes)
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = measure.main(
                    [
                        "--routes", str(routes),
                        "--since", "2026-11-01",
                        "--until", "2026-11-07",
                        "--no-sessions",
                        "--routable-provider", "opencode-go",
                        "--format", "json",
                    ]
                )
            payload = json.loads(buffer.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["denominators"]["routable_turns"], 2)
        self.assertEqual(payload["per_turn"]["decider_calls"], 2)

    def test_missing_routes_exits_with_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = measure.main(
                    ["--routes", str(Path(tmp) / "nope.jsonl"), "--since", "2026-11-01", "--no-sessions"]
                )
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
