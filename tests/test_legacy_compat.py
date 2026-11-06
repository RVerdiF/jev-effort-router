"""Compatibility gate: legacy behaviour against this stack's base commit.

The expectation file is **generated from the reference commit**, never from the working tree (see
``tests/legacy_fixture_scenarios.py``). A failure here means a change moved something an operator
already depends on — installed payload bytes, call order, reason strings, audit record shape — and
the fix is to restore the behaviour, not to regenerate the fixture.

The reference commit is this stack's base (``12aaeec``), not 0.4.0: the one-model grid rule of
``t_8cac6c92`` and the four-step rubric of ``t_0998d382`` are reviewed behaviour changes, so the
0.4.0 bytes are not the target any more. The rubric's wording is masked in the projection; its
shape and option keys are still pinned.

The opt-in CommandCode transport is out of scope by construction: every case in the fixture runs
with an empty trio, so these expectations are the *no-configuration* contract.
"""

from __future__ import annotations

import json

from legacy_fixture_scenarios import FIXTURE_PATH, collect_decider_cases, collect_router_cases

BASE_COMMIT = "12aaeec55713b55d08a791c856cb0ebbf22dec4a"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_the_fixture_was_generated_from_the_base_commit():
    fixture = _fixture()
    assert fixture["base_commit"] == BASE_COMMIT
    assert fixture["generated_by"] == "tests/legacy_fixture_scenarios.py"
    # Coverage is part of the contract: a silently shrunken fixture would weaken every test below.
    assert len(fixture["decider"]) >= 10
    assert len(fixture["router"]) >= 5


def test_decider_wire_and_decisions_are_byte_identical_to_the_base_commit():
    assert collect_decider_cases() == _fixture()["decider"]


def test_router_routing_audit_and_fail_open_are_identical_to_the_base_commit():
    assert collect_router_cases() == _fixture()["router"]


def test_an_absent_legacy_config_is_indistinguishable_from_an_explicitly_empty_one():
    cases = collect_decider_cases()
    assert cases["jev_success"] == cases["jev_success_empty_string_config"]


def test_legacy_route_records_keep_the_0_4_0_shape():
    """The two provenance columns belong to the opt-in transport and to nothing else."""
    events = set()
    for case in _fixture()["router"].values():
        for record in case["records"]:
            events.add(record["event"])
            assert "decider_model" not in record
            assert "decider_endpoint" not in record
    # Both halves of the audit contract are exercised: a route and a skip.
    assert events == {"route", "skip"}
