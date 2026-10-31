"""Every type of the catalogue has a schema, a model and a valid and an invalid vector."""

import re

import pytest
import yaml
from wdf_events import SchemaInvalid, from_bytes
from wdf_events.models import DATA_MODELS, INTRODUCED, JobParams
from wdf_events.profile import CONTRACT_DIR
from wdf_events.schemagen import main as schemagen

JOB_TYPES = {
    "accepted", "published", "admitted", "started", "paused", "pause.requested",
    "resume.requested", "resumed", "cancel.requested", "terminate.requested", "succeeded",
    "partial", "failed", "cancelled", "terminated", "dead.lettered",
}  # fmt: skip
TASK_TYPES = {
    "placed", "started", "checkpoint.committed", "attempt.failed", "retries.exhausted",
    "succeeded", "failed", "skipped", "requested", "result.ready",
}  # fmt: skip
NAMES = {f"job.{n}" for n in JOB_TYPES} | {f"task.{n}" for n in TASK_TYPES}
# The accepted event keeps its vectors under vectors/accepted/ only.
TYPED = sorted(NAMES - {"job.accepted"})


def test_the_models_are_exactly_the_job_and_task_types():
    assert {name for name, _ in DATA_MODELS} == NAMES


@pytest.mark.parametrize("name", sorted(NAMES))
def test_a_type_has_a_schema_directory_a_model_and_an_introduction_date(name):
    assert (CONTRACT_DIR / "schemas" / name / "1.0.json").is_file()
    assert (name, "1.0") in DATA_MODELS
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", INTRODUCED[(name, 1)])


@pytest.mark.parametrize("name", TYPED)
def test_a_type_has_one_valid_and_one_invalid_vector(name):
    directory = CONTRACT_DIR / "vectors/types" / name
    assert sorted(p.name for p in directory.iterdir()) == ["invalid.json", "valid.json"]
    event = from_bytes((directory / "valid.json").read_bytes())
    assert event.type == f"dev.wdf.{name}.v1"
    with pytest.raises(SchemaInvalid) as raised:
        from_bytes((directory / "invalid.json").read_bytes())
    assert raised.value.reason == "bad-data"


def test_the_schemas_match_the_models():
    assert schemagen(["--check"]) == 0


def test_the_status_projection_names_exactly_the_job_types():
    projection = yaml.safe_load(
        (CONTRACT_DIR.parents[1] / "contracts/status/status.yaml").read_text()
    )["projection"]
    assert set(projection) == {n for n in NAMES if n.startswith("job.")}


def test_the_checkpoint_event_is_the_only_checkpoint_type():
    assert [n for n in NAMES if "checkpoint" in n] == ["task.checkpoint.committed"]


def test_the_params_block_is_a_model_with_no_schema_file_and_no_type():
    JobParams(seed=7, parameters={"gain": 1.5}, class_override=None)
    assert "job.params" not in {name for name, _ in DATA_MODELS}
    assert not (CONTRACT_DIR / "schemas/job.params").exists()
    with pytest.raises(ValueError):
        JobParams(seed=-1, parameters={})
