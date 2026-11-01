import json
from pathlib import Path

import pytest
from wdf_jobcheck import validate
from wdf_jobexamples import generate, load, seed_list
from wdf_plan import bind, check_job_params, compile_plan, plan_hash

CONTRACT = Path(__file__).resolve().parents[3] / "contracts/job"
VECTORS = CONTRACT / "vectors"


@pytest.mark.parametrize("shape", ["fan-out", "fan-in", "chains"])
def test_generated_jobs_validate_under_ci_with_no_retries(shape, profile_of):
    report = validate(generate(shape, 100), profile_of("ci"))
    assert report.ok, report.issues
    assert all(t.retries == 0 for t in report.document.tasks.values() if t.type == "simulate")


@pytest.mark.parametrize("shape", ["fan-out", "fan-in", "chains"])
def test_generation_is_deterministic(shape):
    assert generate(shape, 50) == generate(shape, 50)


def test_600_chains_pass_permissive_and_fail_ci(profile_of):
    data = generate("fan-out", 600)
    assert validate(data, profile_of("permissive")).ok
    issues = validate(data, profile_of("ci")).issues
    assert "too-many-tasks" in {i.code for i in issues}


def test_generator_rejects_over_permissive():
    with pytest.raises(ValueError, match=r"fan-out with 51201 .*51,200 tasks"):
        generate("fan-out", 51201)
    generate("fan-out", 51198)


def test_generator_rejects_unknown_shape_and_empty_job():
    with pytest.raises(ValueError, match="unknown shape"):
        generate("ring", 3)
    with pytest.raises(ValueError, match="at least 1"):
        generate("fan-in", 0)


def test_shapes_have_their_task_counts():
    for shape, count in {"fan-out": 12, "fan-in": 21, "chains": 30}.items():
        assert len(json.loads(generate(shape, 10))["tasks"]) == count


def test_seed_list_binds_to_the_batch_template(profile_of):
    profile = profile_of()
    plan = compile_plan(validate(load("batch-template"), profile).document, profile)
    path = VECTORS / "job-params/valid/batch-template.seeds.ndjson"
    lines = path.read_bytes().splitlines()
    assert lines
    hashes = set()
    for line in lines:
        block = json.loads(line)
        assert check_job_params(plan, block, profile) == ()
        hashes.add(plan_hash(bind(plan, block, profile)))
    assert len(hashes) == len(lines)
    assert plan_hash(plan) not in hashes
    assert seed_list(len(lines)) == path.read_bytes()


def test_seed_list_is_deterministic_and_offsets():
    assert seed_list(5) == seed_list(5)
    assert seed_list(2, start=10).splitlines() == seed_list(12).splitlines()[10:]
    assert seed_list(0) == b""
