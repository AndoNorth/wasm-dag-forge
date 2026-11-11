"""The loader accepts the pinned files and the valid vectors, and each invalid vector fails with
the reason of its directory."""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from wdf_workflow.contract import (
    failure_classes,
    uncovered_artifact_codes,
    uncovered_exit_classes,
)
from wdf_workflow.models import FILES, FailureClasses

ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = {name: ROOT / "contracts" / name for name in ("activity", "temporal")}
PINNED = [
    CONTRACTS["activity"] / "failure-classes.yaml",
    CONTRACTS["temporal"] / "naming.yaml",
    CONTRACTS["temporal"] / "pools.yaml",
]
VALID = sorted(p for c in CONTRACTS.values() for p in c.glob("vectors/*/valid/*"))
INVALID = sorted(p for c in CONTRACTS.values() for p in c.glob("vectors/*/invalid/*/*"))
NOT_COVERED = CONTRACTS["activity"] / "vectors/failure-classes/invalid/class-not-covered"
# The reasons that apply to each schema, in check order.
REASONS = {
    "task-input": [
        "schema-violation",
        "too-large",
        "bad-request",
        "params-type-mismatch",
        "cache-key-mismatch",
        "inputs-mismatch",
    ],
    "task-result": ["schema-violation", "too-large", "outputs-not-declared"],
    "failure-details": [
        "schema-violation",
        "too-large",
        "unknown-class",
        "class-not-reportable",
        "deterministic-mismatch",
        "reason-prefix",
    ],
    "heartbeat": ["schema-violation", "too-large"],
    "failure-classes": [
        "schema-violation",
        "duplicate-class",
        "unknown-class",
        "source-mismatch",
        "deterministic-mismatch",
        "deterministic-retried",
        "transient-never-retried",
        "bad-action",
        "class-not-covered",
    ],
    "naming": ["schema-violation", "duplicate-name", "pool-mismatch"],
    "pools": ["schema-violation", "pool-mismatch"],
    "control-signal": ["schema-violation"],
    "resume-signal": ["schema-violation"],
    "status-query": [
        "schema-violation",
        "too-large",
        "unknown-status",
        "unknown-class",
        "pause-reason-mismatch",
        "out-of-order",
    ],
}


def check(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "wdf_workflow.contract", "--check", *map(str, args)],
        capture_output=True,
        text=True,
    )


def vector_id(path: Path) -> str:
    return str(path.relative_to(ROOT / "contracts"))


@pytest.mark.parametrize("path", PINNED + VALID, ids=vector_id)
def test_loader_accepts_a_valid_file(path):
    result = check(path)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("path", INVALID, ids=vector_id)
def test_loader_refuses_an_invalid_vector_with_the_reason_of_its_directory(path):
    result = check(path)
    assert result.returncode == 1
    assert result.stderr.startswith(f"reason: {path.parent.name}:"), result.stderr


def test_loader_has_a_vector_for_every_reason_of_every_schema():
    found: dict[str, set[str]] = {}
    for path in INVALID:
        found.setdefault(path.parent.parent.parent.name, set()).add(path.parent.name)
    assert {name: sorted(reasons) for name, reasons in found.items()} == {
        name: sorted(reasons) for name, reasons in REASONS.items()
    }
    assert set(REASONS) == set(FILES)


def test_loader_has_two_valid_payloads_or_a_pinned_file_per_schema():
    valid = {p.parent.parent.name for p in VALID}
    pinned = {p.name.split(".")[0] for p in PINNED}
    assert valid | pinned == set(FILES)
    assert all(len([p for p in VALID if p.parent.parent.name == n]) >= 2 for n in valid)


@pytest.mark.parametrize("contract", CONTRACTS.values(), ids=lambda c: c.name)
def test_loader_check_sh_runs_the_loader(contract):
    script = contract / "check.sh"
    pinned = [p for p in PINNED if p.parent == contract][0]
    assert subprocess.run([script, pinned], capture_output=True).returncode == 0
    bad = sorted(contract.glob("vectors/*/invalid/schema-violation/*"))[0]
    result = subprocess.run([script, bad], capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stderr.startswith("reason: schema-violation:")
    assert subprocess.run([script], capture_output=True).returncode == 2


def test_loader_takes_the_name_from_as(tmp_path):
    payload = tmp_path / "signal.json"
    payload.write_text('{"control_seq": 1, "reason": null}\n')
    assert check(payload, "--as", "control-signal").returncode == 0
    result = check(payload, "--as", "resume-signal")
    assert result.returncode == 1
    assert result.stderr.startswith("reason: schema-violation:")


def test_loader_usage_errors(tmp_path):
    unnamed = tmp_path / "unnamed.json"
    unnamed.write_text("{}\n")
    assert check(unnamed).returncode == 2
    assert check(tmp_path / "missing.json", "--as", "heartbeat").returncode == 2
    assert check(unnamed, "--as", "no-such-schema").returncode == 2
    bare = subprocess.run(
        [sys.executable, "-m", "wdf_workflow.contract", str(PINNED[0])], capture_output=True
    )
    assert bare.returncode == 2


def test_loader_refuses_a_file_that_is_not_utf8(tmp_path):
    latin = tmp_path / "beat.json"
    latin.write_bytes(b'{"started_at": "\xe9"}\n')
    result = check(latin, "--as", "heartbeat")
    assert result.returncode == 1
    assert result.stderr.startswith("reason: schema-violation:")


def table(path: Path) -> FailureClasses:
    return FailureClasses.model_validate(yaml.safe_load(path.read_text()))


def test_failure_table_covers_exit_classes():
    assert uncovered_exit_classes(table(PINNED[0])) == []
    assert uncovered_exit_classes(table(NOT_COVERED / "missing-exit-class.yaml")) == ["timeout"]


def test_failure_table_covers_artifact_codes():
    assert uncovered_artifact_codes(table(PINNED[0])) == []
    missing = table(NOT_COVERED / "missing-artifact-code.yaml")
    assert uncovered_artifact_codes(missing) == ["OUTPUT_MISSING"]


# Every row of the pinned table: source, pool, deterministic, budget and action.
ROWS = {
    "timeout": ("workload", "simulate", False, "guest", None),
    "guest_memory_cap": ("workload", "simulate", True, "none", None),
    "guest_failed": ("workload", "simulate", "from-error-file", "guest", None),
    "host_error": ("workload", "simulate", False, "infra", None),
    "oom_killed": ("workload", "simulate", False, "infra", None),
    "compile_failed": ("workload", "compile", True, "none", None),
    "compile_dependency_unavailable": ("workload", "compile", False, "infra", None),
    "compile_timeout": ("workload", "compile", False, "guest", None),
    "compile_oom": ("workload", "compile", False, "guest", "escalate-once"),
    "aggregate_failed": ("workload", "aggregate", False, "guest", None),
    "aggregate_input_unavailable": ("workload", "aggregate", False, "infra", None),
    "aggregate_input_corrupt": ("workload", "aggregate", True, "none", None),
    "aggregate_input_refused": ("workload", "aggregate", True, "none", None),
    "aggregate_parse_failed": ("workload", "aggregate", True, "none", None),
    "aggregate_oom": ("workload", "aggregate", False, "guest", "escalate-once"),
    "aggregate_timeout": ("workload", "aggregate", False, "guest", None),
    "STORE_UNAVAILABLE": ("artifact", None, False, "infra", None),
    "STORE_FORBIDDEN": ("artifact", None, True, "none", None),
    "ARTIFACT_NOT_FOUND": ("artifact", None, True, "none", None),
    "ARTIFACT_CORRUPT": ("artifact", None, True, "none", None),
    "ARTIFACT_LOST": ("artifact", None, True, "none", "invalidate-cache-and-rebuild"),
    "OUTPUT_UPLOAD_FAILED": ("artifact", None, False, "infra", None),
    "OUTPUT_MISSING": ("artifact", None, True, "none", None),
    "CHECKPOINT_CORRUPT": ("artifact", None, False, "infra", "previous-checkpoint"),
    "operator_interrupt": ("runner", None, False, "free", None),
    "worker_shutdown": ("runner", None, False, "infra", None),
    "heartbeat_timeout": ("temporal", None, False, "infra", None),
    "start_to_close_timeout": ("temporal", None, False, "infra", None),
    "schedule_to_start_timeout": ("temporal", None, False, "free", "re-place"),
    "insufficient_memory": ("placement", None, True, "none", None),
    "insufficient_cpu": ("placement", None, True, "none", None),
    "no_matching_pool": ("placement", None, True, "none", None),
}


def test_failure_table_rows_are_those_of_the_spec():
    rows = {
        name: (row.source, row.pool, row.deterministic, row.budget, row.action)
        for name, row in failure_classes().items()
    }
    assert rows == ROWS
