"""The loader accepts the committed files and each invalid vector fails with its reason."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
ARTIFACT = ROOT / "contracts/artifact"
COMMITTED = [ARTIFACT / "failures.yaml", ARTIFACT / "staging.yaml", ARTIFACT / "retention.yaml"]
VECTORS = ARTIFACT / "vectors"
CONTRACTS = ("failures", "staging", "retention", "index")
VALID = sorted(p for c in CONTRACTS for p in (VECTORS / c).glob("valid/*.*"))
INVALID = sorted(p for c in CONTRACTS for p in (VECTORS / c).glob("invalid/*/*.*"))
REASONS = {
    "failures": {"schema-violation", "duplicate-code", "unknown-code"},
    "staging": {
        "schema-violation",
        "unknown-kind",
        "kind-without-cap",
        "cap-above-kind-cap",
        "unknown-task-type",
        "output-not-in-registry",
        "unknown-code",
    },
    "retention": {
        "schema-violation",
        "window-above-safety-net",
        "cache-window-below-twice-max-job-duration",
        "keep-below-two",
        "failed-shorter-than-succeeded",
        "unknown-status",
    },
    "index": {
        "schema-violation",
        "entry-without-reference",
        "reference-with-failure-status",
        "wrong-kind",
        "header-not-first",
        "out-of-order",
    },
}


def check(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "wdf_artifactclient.contract", "--check", str(path)],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("path", COMMITTED + VALID, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_a_valid_file_passes(path):
    result = check(path)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("path", INVALID, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_an_invalid_vector_fails_with_the_reason_of_its_directory(path):
    result = check(path)
    assert result.returncode == 1
    assert result.stderr.startswith(f"reason: {path.parent.name}:")


@pytest.mark.parametrize("contract", sorted(REASONS))
def test_each_reason_has_a_vector(contract):
    found = {p.parent.name for p in INVALID if p.parent.parent.parent.name == contract}
    assert found == REASONS[contract]


def test_a_missing_file_or_a_missing_flag_is_a_usage_error():
    assert check(ARTIFACT / "nothing.yaml").returncode == 2
    bare = subprocess.run(
        [sys.executable, "-m", "wdf_artifactclient.contract", str(COMMITTED[0])],
        capture_output=True,
        text=True,
    )
    assert bare.returncode == 2


def test_a_file_of_neither_kind_is_a_schema_violation(tmp_path):
    other = tmp_path / "other.yaml"
    other.write_text("unrelated: true\n")
    result = check(other)
    assert result.returncode == 1
    assert result.stderr.startswith("reason: schema-violation:")


def test_the_taxonomy_holds_the_codes_of_the_spec():
    codes = {c["code"] for c in yaml.safe_load(COMMITTED[0].read_text())["codes"]}
    assert codes == {
        "STORE_UNAVAILABLE",
        "STORE_FORBIDDEN",
        "ARTIFACT_NOT_FOUND",
        "ARTIFACT_CORRUPT",
        "ARTIFACT_LOST",
        "OUTPUT_UPLOAD_FAILED",
        "OUTPUT_MISSING",
        "CHECKPOINT_CORRUPT",
    }


def test_a_store_fault_is_retryable_and_a_checkpoint_mismatch_is_not():
    by_code = {c["code"]: c for c in yaml.safe_load(COMMITTED[0].read_text())["codes"]}
    assert by_code["STORE_UNAVAILABLE"]["attempt_class"] == "retryable"
    assert by_code["CHECKPOINT_CORRUPT"]["attempt_class"] == "non-retryable"
    assert "store fault" in by_code["CHECKPOINT_CORRUPT"]["cause"]


def test_the_object_caps_are_the_pinned_figures():
    caps = yaml.safe_load(COMMITTED[1].read_text())["kind_caps"]
    mib = 1024 * 1024
    assert caps == {
        "manifest": 1 * mib,
        "source": 64 * mib,
        "index": 128 * mib,
        "component": 64 * mib,
        "build-info": 1 * mib,
        "result": 64 * mib,
        "output": 256 * mib,
        "summary": 16 * mib,
        "log-tail": 64 * 1024,
        "checkpoint": 64 * mib,
    }


def test_the_windows_are_those_of_the_decision():
    retention = yaml.safe_load(COMMITTED[2].read_text())
    days = {s: (w["results_days"], w["checkpoints_days"]) for s, w in retention["statuses"].items()}
    assert days == {
        "succeeded": (7, 1),
        "partial": (14, 3),
        "failed": (14, 3),
        "cancelled": (3, 1),
        "terminated": (3, 1),
    }
    assert retention["checkpoint_keep"] == 2
    assert retention["cache_unused_days"] == 14
    assert retention["content"] == {
        "unreferenced_grace_days": 2,
        "tombstone_interval_hours": 1,
        "orphan_grace_days": 2,
    }


def test_the_three_entry_index_holds_each_status_in_task_order():
    lines = (VECTORS / "index/valid/three-entries.jsonl").read_text().splitlines()
    entries = [json.loads(line) for line in lines[1:]]
    assert [e["status"] for e in entries] == ["succeeded", "failed", "missing"]
    assert [e["task"] for e in entries] == sorted(e["task"] for e in entries)
    assert [("reference" in e) for e in entries] == [True, False, False]


def test_the_same_entries_give_the_same_bytes_whatever_the_key_order(tmp_path):
    from wdf_artifactclient.contract import canonical

    path = VECTORS / "index/valid/three-entries.jsonl"
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    shuffled = "".join(json.dumps(dict(reversed(v.items()))) + "\n" for v in lines)
    assert "".join(canonical(v) + "\n" for v in lines) == path.read_text()
    other = tmp_path / "shuffled.jsonl"
    other.write_text(shuffled)
    result = check(other)
    assert result.returncode == 1
    assert result.stderr.startswith("reason: schema-violation:")
