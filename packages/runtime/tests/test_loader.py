"""The loader accepts the committed files, and each invalid vector fails with its reason."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from wdf_runtime.contract import Refused, heartbeat, wit_version
from wdf_runtime.models import CONTRACT, DigestListSummary, SummaryStatsSummary

ROOT = Path(__file__).resolve().parents[3]
WORKLOAD = ROOT / "contracts/workload"
VECTORS = WORKLOAD / "vectors"
COMMITTED = [WORKLOAD / "exit-classes.yaml", WORKLOAD / "results.yaml"]
LEVEL0 = sorted((VECTORS / "level0").glob("*/*.json"))
VALID = sorted(VECTORS.glob("*/valid/*"))
INVALID = sorted(VECTORS.glob("*/invalid/*/*"))
REASONS = {
    "schema-violation",
    "duplicate-class",
    "unknown-pool",
    "exit-code-taken",
    "unknown-kind",
    "unknown-column-type",
    "bad-heartbeat",
    "doctype-present",
    "row-outside-block",
    "duplicate-block-id",
    "bad-number-format",
    "wit-version-mismatch",
}


def check(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "wdf_runtime.contract", "--check", str(path)],
        capture_output=True,
        text=True,
    )


def vector_id(path: Path) -> str:
    return str(path.relative_to(WORKLOAD))


@pytest.mark.parametrize("path", COMMITTED + VALID + LEVEL0, ids=vector_id)
def test_a_valid_file_passes(path):
    result = check(path)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("path", INVALID, ids=vector_id)
def test_an_invalid_vector_fails_with_the_reason_of_its_directory(path):
    result = check(path)
    assert result.returncode == 1
    assert result.stderr.startswith(f"reason: {path.parent.name}:"), result.stderr


def test_each_reason_has_a_vector():
    assert {p.parent.name for p in INVALID} == REASONS


def test_every_file_has_valid_and_invalid_vectors():
    files = {p.parent.parent.parent.name for p in INVALID}
    assert files == {p.parent.parent.name for p in VALID}
    assert files == {
        "input",
        "error",
        "heartbeat",
        "results",
        "exit-classes",
        "summary",
        "build-info",
        "expected",
    }


def test_a_missing_file_or_a_missing_flag_is_a_usage_error():
    assert check(WORKLOAD / "nothing.yaml").returncode == 2
    bare = subprocess.run(
        [sys.executable, "-m", "wdf_runtime.contract", str(COMMITTED[0])],
        capture_output=True,
        text=True,
    )
    assert bare.returncode == 2


@pytest.mark.parametrize("name,text", [("other.yaml", "unrelated: true\n"), ("other.json", "[]\n")])
def test_a_file_of_no_known_shape_is_a_schema_violation(tmp_path, name, text):
    other = tmp_path / name
    other.write_text(text)
    result = check(other)
    assert result.returncode == 1
    assert result.stderr.startswith("reason: schema-violation:")


def test_check_sh_runs_the_loader():
    script = WORKLOAD / "check.sh"
    assert subprocess.run([script, str(COMMITTED[0])], capture_output=True).returncode == 0
    bad = VECTORS / "exit-classes/invalid/unknown-pool/gpu.yaml"
    result = subprocess.run([script, str(bad)], capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stderr.startswith("reason: unknown-pool:")
    assert subprocess.run([script], capture_output=True).returncode == 2


def test_wit_parses():
    result = subprocess.run(
        ["wasm-tools", "component", "wit", str(WORKLOAD / "wit")], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "package wdf:task@1.0.0;" in result.stdout
    assert "world task {" in result.stdout
    assert "export wasi:cli/run@0.2.9;" in result.stdout


def test_the_wit_version_is_the_package_line_and_ships_as_package_data():
    assert wit_version() == "1.0.0"
    shipped = (CONTRACT / "wit" / "task.wit").read_text()
    assert shipped == (WORKLOAD / "wit/task.wit").read_text()
    assert "include wasi:cli/command@0.2.9;" in shipped


def test_level0_vectors_pair_an_input_with_its_expected_values():
    for directory in (VECTORS / "level0").iterdir():
        given = json.loads((directory / "input.json").read_text())
        expected = json.loads((directory / "expected.json").read_text())
        assert given["workload"] == directory.name
        assert expected["seed"] == given["seed"]
    assert {d.name for d in (VECTORS / "level0").iterdir()} == {"echo", "sum_squares", "hash_chain"}


def level0(workload: str) -> tuple[dict, list]:
    directory = VECTORS / "level0" / workload
    given = json.loads((directory / "input.json").read_text())
    return given, json.loads((directory / "expected.json").read_text())["blocks"]


def test_echo_returns_its_args():
    given, blocks = level0("echo")
    rows = {row["key"]: json.loads(row["value"]) for row in blocks[0]["rows"]}
    assert rows == given["args"]
    assert blocks[0]["kind"] == "echo"


def test_sum_squares_is_the_closed_form():
    given, blocks = level0("sum_squares")
    n = given["args"]["n"]
    assert blocks[0]["rows"] == [{"name": "sum_squares", "value": n * (n + 1) * (2 * n + 1) // 6}]
    assert blocks[0]["rows"][0]["value"] == sum(i * i for i in range(1, n + 1))


def test_hash_chain_is_rounds_of_sha256_from_the_seed():
    given, blocks = level0("hash_chain")
    digest = given["seed"].to_bytes(8, "big")
    for _ in range(given["args"]["rounds"]):
        digest = hashlib.sha256(digest).digest()
    assert blocks[0]["rows"] == [{"name": "hash_chain", "value": digest.hex()}]


def test_the_file_names_are_those_of_the_staging_contract():
    staging = yaml.safe_load((CONTRACT / "staging.yaml").read_text())
    outputs = {
        name: [out["file"] for out in task["outputs"]]
        for name, task in staging["task_types"].items()
    }
    assert outputs == {
        "compile": ["component.wasm", "build-info.json"],
        "simulate": ["results.xml"],
        "aggregate": ["summary.json"],
    }
    assert staging["input_file"] == "input.json"
    assert staging["undeclared"] == "skip-and-list"
    assert "error.json" not in {f for files in outputs.values() for f in files}


def test_the_pools_and_aggregations_are_those_of_the_registry():
    registry = json.loads((CONTRACT / "registry.json").read_text())
    pools = yaml.safe_load(COMMITTED[0].read_text())["pools"]
    assert set(pools) == {entry["pool"] for entry in registry["task_types"].values()}
    literals = {
        model.model_fields["aggregation"].annotation.__args__[0]
        for model in (SummaryStatsSummary, DigestListSummary)
    }
    assert literals == set(registry["aggregations"])
    assert set(SummaryStatsSummary.model_fields["args"].annotation.model_fields) == set(
        registry["aggregations"]["summary_stats"]["args"]
    )


def test_the_exit_classes_and_native_codes_are_those_of_the_spec():
    pools = yaml.safe_load(COMMITTED[0].read_text())["pools"]
    codes = {
        pool: {c["class"]: c.get("exit_code") for c in entry["classes"]}
        for pool, entry in pools.items()
    }
    assert codes == {
        "simulate": dict.fromkeys(
            ["ok", "timeout", "guest_memory_cap", "guest_failed", "host_error", "oom_killed"]
        ),
        "compile": {
            "compile_failed": 3,
            "compile_dependency_unavailable": 4,
            "compile_timeout": 5,
            "compile_oom": None,
        },
        "aggregate": {
            "aggregate_failed": 1,
            "aggregate_input_unavailable": 3,
            "aggregate_input_corrupt": 4,
            "aggregate_input_refused": 5,
            "aggregate_parse_failed": 6,
            "aggregate_oom": None,
            "aggregate_timeout": None,
        },
    }
    by_class = {c["class"]: c for entry in pools.values() for c in entry["classes"]}
    assert by_class["guest_failed"]["deterministic"] == "from-error-file"
    assert by_class["guest_memory_cap"]["deterministic"] is True
    assert by_class["compile_failed"]["deterministic"] is True
    assert by_class["aggregate_parse_failed"]["deterministic"] is True
    assert by_class["aggregate_input_unavailable"]["deterministic"] is False


def test_the_kinds_are_those_of_the_spec():
    kinds = yaml.safe_load(COMMITTED[1].read_text())["kinds"]
    columns = {name: [(c["name"], c["type"]) for c in k["columns"]] for name, k in kinds.items()}
    assert columns == {
        "echo": [("key", "str"), ("value", "str")],
        "scalar": [("name", "str"), ("value", "int")],
        "digest": [("name", "str"), ("value", "hex")],
        "series": [("step", "int"), ("value", "float")],
        "payload": [("step", "int"), ("data", "hex")],
    }


def test_a_heartbeat_line_gives_its_progress_and_other_lines_give_none():
    assert heartbeat('HEARTBEAT {"progress":0.5,"step":2,"steps":4}') == {
        "progress": 0.5,
        "step": 2,
        "steps": 4,
    }
    assert heartbeat("progress is fine") is None
    with pytest.raises(Refused, match="bad-heartbeat"):
        heartbeat('HEARTBEAT {"progress":2,"step":2,"steps":4}')
