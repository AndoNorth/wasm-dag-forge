import json
import subprocess
from pathlib import Path

import pytest
from wdf_jobcheck import validate
from wdf_jobexamples import examples, load
from wdf_plan import bind, check_job_params, compile_plan, plan_hash

CONTRACT = Path(__file__).resolve().parents[3] / "contracts/job"
VECTORS = CONTRACT / "vectors"

NAMES = [p.stem for p in sorted((VECTORS / "valid").glob("*.json"))]


def test_examples_are_valid_vectors():
    assert examples() == NAMES
    assert len(NAMES) == 7
    for name in NAMES:
        done = subprocess.run(
            [CONTRACT / "check.sh", VECTORS / "valid" / f"{name}.json"],
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, f"{name}: {done.stderr}"


def test_load_returns_the_file_bytes_and_rejects_an_unknown_name():
    assert load("level0-echo") == (VECTORS / "valid/level0-echo.json").read_bytes()
    with pytest.raises(KeyError, match="no-such"):
        load("no-such")


def _types(name):
    return sorted(t["type"] for t in json.loads(load(name))["tasks"].values())


def test_level0_is_the_smoke_job():
    assert _types("level0-echo") == ["compile", "simulate"]


def test_ladder_levels():
    assert _types("level1-simulate") == ["compile", "simulate"]
    assert _types("fan-out-8").count("simulate") == 8
    assert "aggregate" in _types("level2-aggregate")
    assert _types("aggregate-only") == ["aggregate"]


def test_aggregate_only_names_an_index_and_partial_opts_in():
    only = json.loads(load("aggregate-only"))["tasks"]["agg"]
    assert only["params"]["index"]["kind"] == "index"
    partial = json.loads(load("aggregate-partial"))["tasks"]["agg"]
    assert partial["allow_partial"] is True


def test_seed_placeholders_only_where_the_issue_says():
    for name in NAMES:
        text = load(name).decode()
        seeded = '"param": "seed"' in text
        assert seeded == (name in {"level2-aggregate", "batch-template"}), name
    assert json.loads(load("batch-template"))["parameters"] == {"seed": "seed", "steps": "integer"}


@pytest.mark.parametrize("name", NAMES)
def test_seed_7_block_binds_and_validates(name, profile_of, resolver):
    profile = profile_of()
    report = validate(load(name), profile, resolver)
    assert report.ok, report.issues
    plan = compile_plan(report.document, profile)
    block = json.loads((VECTORS / f"job-params/valid/{name}.seed-7.json").read_text())
    assert block["seed"] == 7
    assert check_job_params(plan, block, profile) == ()
    assert len(plan_hash(bind(plan, block, profile))) == 64
