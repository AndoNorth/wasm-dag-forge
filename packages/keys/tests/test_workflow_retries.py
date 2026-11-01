"""Two workflow-level retries of one task never share an event id or an attempt-scoped key."""

import json
from pathlib import Path

import pytest
import wdf_keys
import yaml

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
SCENARIOS = json.loads((CONTRACTS / "events/vectors/keys.json").read_text())["workflow_retries"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["note"][:30])
def test_vectors_match_the_functions(scenario):
    for run in scenario["runs"]:
        identity = wdf_keys.attempt_identity(run["task_attempt"], scenario["resume_counter"])
        assert identity == run["identity"]
        event_id = wdf_keys.cloudevent_id(
            scenario["job_id"],
            scenario["task_key"],
            identity,
            scenario["kind"],
            scenario["sequence"],
        )
        assert event_id == run["event_id"]


def test_retries_get_distinct_identities_and_event_ids():
    retries = SCENARIOS[0]["runs"]
    assert len(retries) >= 2
    assert len({run["identity"] for run in retries}) == len(retries)
    assert len({run["event_id"] for run in retries}) == len(retries)


def test_an_activity_attempt_that_is_always_1_would_collide():
    scenario = SCENARIOS[0]
    ids = {
        wdf_keys.cloudevent_id(
            scenario["job_id"],
            scenario["task_key"],
            wdf_keys.attempt_identity(1, scenario["resume_counter"]),
            scenario["kind"],
            scenario["sequence"],
        )
        for _ in scenario["runs"]
    }
    assert len(ids) == 1


def test_retries_write_distinct_attempt_scoped_keys():
    names = SCENARIOS[0]["artifact_vectors"]
    refs = [yaml.safe_load((CONTRACTS / "artifact/vectors/valid" / n).read_text()) for n in names]
    assert [r["producer"]["attempt"] for r in refs] == [r["identity"] for r in SCENARIOS[0]["runs"]]
    assert len({r["uri"] for r in refs}) == len(refs)
    assert len({(r["producer"]["job"], r["producer"]["task"], r["name"]) for r in refs}) == 1
