"""The limits file and its profiles."""

import json

import pytest
import yaml
from jsonschema import ValidationError, validate
from wdf_compat.window import MAX_JOB_DURATION_DAYS
from wdf_jobdoc.profile import PROFILES, Limits, LimitsFile
from wdf_jobdoc.profile_files import load_profile


@pytest.fixture
def fixture_dir(contract):
    return contract / "vectors/slots-fixture"


@pytest.fixture
def numbers(contract):
    return yaml.safe_load((contract / "limits.yaml").read_text())


def profile(name, root, fixture_dir):
    return load_profile(name, root, slots_dir=fixture_dir)


def test_limits_yaml_validates_against_its_schema(contract, numbers):
    validate(numbers, json.loads((contract / "limits.schema.json").read_text()))


def test_the_profiles_are_exactly_the_four(numbers):
    assert set(numbers["profiles"]) == set(PROFILES) == {"ci", "dev", "soak", "permissive"}


def test_a_missing_profile_is_refused(numbers):
    del numbers["profiles"]["soak"]
    with pytest.raises(ValueError, match="exactly"):
        LimitsFile.model_validate(numbers)


def test_an_unknown_number_is_refused(contract, numbers):
    numbers["profiles"]["ci"]["max_taks"] = 1
    with pytest.raises(ValidationError):
        validate(numbers, json.loads((contract / "limits.schema.json").read_text()))
    with pytest.raises(ValueError, match="max_taks"):
        LimitsFile.model_validate(numbers)


def test_the_ci_numbers_are_the_proposed_ones(root, fixture_dir):
    ci = profile("ci", root, fixture_dir)
    assert (ci.max_tasks, ci.max_parallel, ci.default_max_parallel) == (512, 32, 8)
    assert (ci.max_document_bytes, ci.max_plan_bytes) == (1024 * 1024, 256 * 1024)
    assert (ci.max_retries, ci.max_run_timeout_s) == (3, 172800)
    assert ci.max_history_events == 10000
    assert (ci.compile_run_timeout_s, ci.compile_retries) == (600, 1)
    assert (ci.class_threshold_s, ci.classes) == (3600, ["short", "long"])
    assert ci.ceiling_cpu_milli is None and ci.ceiling_memory_mib is None


def test_the_history_estimate_of_the_largest_ci_job_reaches_the_limit(root, fixture_dir):
    ci = profile("ci", root, fixture_dir)
    tasks = ci.max_tasks
    events = (
        ci.history_events_per_job
        + tasks * ci.history_events_per_task
        + tasks * ci.max_retries * ci.history_events_per_retry
    )
    assert events == 13322
    assert events > ci.max_history_events


def test_max_job_duration_matches_compat(root, fixture_dir):
    assert profile("ci", root, fixture_dir).max_job_duration_days == MAX_JOB_DURATION_DAYS


def test_the_critical_path_limit_is_in_seconds(root, fixture_dir):
    assert profile("ci", root, fixture_dir).critical_path_limit_s == MAX_JOB_DURATION_DAYS * 86400


@pytest.mark.parametrize("name", ["dev", "soak"])
def test_dev_and_soak_use_the_ci_numbers(root, name):
    ci = load_profile("ci", root).model_dump(exclude={"name", "pools"})
    assert load_profile(name, root).model_dump(exclude={"name", "pools"}) == ci


def test_each_profile_reads_its_own_slots_file(root, tmp_path, fixture_dir):
    text = (fixture_dir / "ci.yaml").read_text()
    (tmp_path / "dev.yaml").write_text(text.replace("250m", "300m"))
    assert load_profile("dev", root, slots_dir=tmp_path).pools.simulate.cpu_milli == 300
    assert profile("ci", root, fixture_dir).pools.simulate.cpu_milli == 250


def test_permissive_lifts_every_number_about_a_hundredfold(root, fixture_dir):
    ci = profile("ci", root, fixture_dir)
    lifted = profile("permissive", root, fixture_dir)
    for field in (
        "max_tasks",
        "max_parallel",
        "max_document_bytes",
        "max_plan_bytes",
        "max_retries",
        "max_run_timeout_s",
        "max_job_duration_days",
        "max_history_events",
    ):
        ratio = getattr(lifted, field) / getattr(ci, field)
        assert 90 <= ratio <= 110, field
    assert lifted.max_tasks == 51200


def test_permissive_sets_the_ceilings_and_keeps_the_compile_defaults(root, fixture_dir):
    ci = profile("ci", root, fixture_dir)
    lifted = profile("permissive", root, fixture_dir)
    assert (lifted.ceiling_cpu_milli, lifted.ceiling_memory_mib) == (1_000_000, 1024 * 1024)
    assert lifted.pools == ci.pools
    assert (lifted.compile_run_timeout_s, lifted.compile_retries) == (600, 1)


def test_an_unknown_profile_is_refused(root, fixture_dir):
    with pytest.raises(ValueError, match="unknown profile"):
        profile("prod", root, fixture_dir)


def test_default_parallelism_may_not_exceed_the_maximum(numbers):
    numbers["profiles"]["ci"]["default_max_parallel"] = 33
    with pytest.raises(ValueError, match="default_max_parallel"):
        LimitsFile.model_validate(numbers)


def test_classes_are_short_and_long(numbers):
    numbers["profiles"]["ci"]["classes"] = ["short", "huge"]
    with pytest.raises(ValueError):
        LimitsFile.model_validate(numbers)


def test_limits_are_frozen(root, fixture_dir):
    with pytest.raises(ValueError):
        profile("ci", root, fixture_dir).max_tasks = 1


def test_the_limits_model_is_what_the_file_holds(numbers):
    for body in numbers["profiles"].values():
        assert set(body) - set(Limits.model_fields) == set()
