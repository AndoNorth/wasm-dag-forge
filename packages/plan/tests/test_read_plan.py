import json

import pytest
from pydantic import ValidationError
from wdf_plan import Plan, UnsupportedPlanVersion, canonical_bytes, read_plan

GOLDEN = sorted(
    (__import__("pathlib").Path(__file__).resolve().parents[3]).glob(
        "contracts/job/vectors/plans/1/*.json"
    )
)


def test_there_are_golden_plans_to_read():
    assert len(GOLDEN) == 14


@pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.name)
def test_read_plan_reads_every_golden_plan(path):
    plan = read_plan(path.read_bytes())
    assert isinstance(plan, Plan)
    assert canonical_bytes(plan) == path.read_bytes()


def test_read_plan_takes_text_and_a_parsed_mapping():
    data = GOLDEN[0].read_bytes()
    assert read_plan(data.decode()) == read_plan(json.loads(data)) == read_plan(data)


@pytest.mark.parametrize("version", [0, 2, 99, "1", 1.0, True, None])
def test_read_plan_rejects_other_versions(version):
    data = json.loads(GOLDEN[0].read_bytes()) | {"plan_version": version}
    with pytest.raises(UnsupportedPlanVersion) as raised:
        read_plan(data)
    assert raised.value.version == version
    assert isinstance(raised.value, ValueError)


@pytest.mark.parametrize("data", [{}, [], "[1]", b"null"])
def test_read_plan_rejects_input_without_a_version(data):
    with pytest.raises(UnsupportedPlanVersion):
        read_plan(data)


def test_read_plan_refuses_a_version_one_plan_that_does_not_fit():
    data = json.loads(GOLDEN[0].read_bytes()) | {"surprise": 1}
    with pytest.raises(ValidationError):
        read_plan(data)
