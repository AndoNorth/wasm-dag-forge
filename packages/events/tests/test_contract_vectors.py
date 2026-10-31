"""The status and artifact contracts: their vectors through the loaders, and the pinned tables."""

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from jsonschema import validate

ROOT = Path(__file__).resolve().parents[3]
VECTORS = ROOT / "scripts/verify/cluster/lib/vectors.sh"

# The buckets and key templates of the storage epic, which layout.yaml must equal.
STORAGE_TEMPLATES = {
    "wdf-sources": {"sha256/<hash2>/<hash>"},
    "wdf-components": {"sha256/<hash2>/<hash>"},
    "wdf-results": {
        "<tenant>/<batch>/<job>/<seed>/<task>/<attempt>/<name>",
        "<tenant>/<batch>/aggregate/<job>/<task>/<attempt>/<name>",
    },
    "wdf-checkpoints": {"<tenant>/<batch>/<job>/<seed>/<task>/<attempt>/<segment>/<name>"},
}
JOB_TYPES = {
    "job.accepted",
    "job.published",
    "job.admitted",
    "job.started",
    "job.paused",
    "job.pause.requested",
    "job.resume.requested",
    "job.resumed",
    "job.cancel.requested",
    "job.terminate.requested",
    "job.succeeded",
    "job.partial",
    "job.failed",
    "job.cancelled",
    "job.terminated",
    "job.dead.lettered",
}


def load(path: str):
    return yaml.safe_load((ROOT / path).read_text())


@pytest.mark.parametrize(
    ("contract", "real"),
    [("contracts/status", ["contracts/status/status.yaml"]), ("contracts/artifact", [])],
)
def test_vectors_keep_their_result(contract, real):
    run = subprocess.run([VECTORS, contract, *real], cwd=ROOT, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr


def test_status_table_has_the_ten_statuses_and_the_terminal_set():
    table = load("contracts/status/status.yaml")
    assert table["statuses"] == [
        "accepted",
        "admitted",
        "running",
        "paused",
        "resume-pending",
        "succeeded",
        "partial",
        "failed",
        "cancelled",
        "terminated",
    ]
    assert set(table["terminal"]) == {"succeeded", "partial", "failed", "cancelled", "terminated"}
    assert set(table["views"]) == {"pausing", "cancelling"}
    assert not set(table["views"]) & set(table["statuses"])


def test_projection_covers_the_16_job_types():
    projection = load("contracts/status/status.yaml")["projection"]
    assert set(projection) == JOB_TYPES
    sets = {t: [e["sets"] for e in entries] for t, entries in projection.items()}
    assert sets["job.resume.requested"] == ["resume-pending"]
    for none in (
        "job.published",
        "job.pause.requested",
        "job.cancel.requested",
        "job.terminate.requested",
        "job.dead.lettered",
    ):
        assert sets[none] == [None]


def test_resumed_depends_on_who_emits_it():
    resumed = load("contracts/status/status.yaml")["projection"]["job.resumed"]
    assert {e["when"]["source"]: e["sets"] for e in resumed} == {
        "/wdf/workflow": "running",
        "/wdf/control": "accepted",
    }


def test_row_only_moves_include_the_pairs_of_a_job_that_never_started():
    moves = load("contracts/status/status.yaml")["transitions"]
    row_only = {(m["from"], m["to"]) for m in moves if m.get("row_only")}
    assert {("accepted", "paused"), ("resume-pending", "accepted")} <= row_only
    assert {("accepted", "cancelled"), ("accepted", "terminated")} <= row_only


def test_layout_matches_the_storage_epic_and_the_reference_schema():
    layout = load("contracts/artifact/layout.yaml")
    validate(layout, json.loads((ROOT / "contracts/artifact/layout.schema.json").read_text()))
    assert set(layout["buckets"]) == set(STORAGE_TEMPLATES)
    templates = {b: set() for b in layout["buckets"]}
    for kind in layout["kinds"].values():
        templates[kind["bucket"]].add(kind["key"])
    assert templates == STORAGE_TEMPLATES

    reference = json.loads((ROOT / "contracts/artifact/reference.schema.json").read_text())
    assert set(layout["kinds"]) == set(reference["properties"]["kind"]["enum"])
    scoped = {k for k, v in layout["kinds"].items() if v["class"] == "attempt-scoped"}
    assert scoped == set(reference["if"]["properties"]["kind"]["enum"])
    for bucket in layout["buckets"]:
        classes = {v["class"] for v in layout["kinds"].values() if v["bucket"] == bucket}
        assert len(classes) == 1, bucket
