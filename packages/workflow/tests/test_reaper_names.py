"""The reaper Schedule of the naming contract is the one wdf_artifactreaper.schedule delivers."""

import pytest
from wdf_artifactreaper import schedule
from wdf_workflow.contract import naming

CONSTANTS = {
    "id": "SCHEDULE_ID",
    "queue": "TASK_QUEUE",
    "workflow": "WORKFLOW",
    "workflow_id": "WORKFLOW_ID",
    "activity": "ACTIVITY",
}


def mismatches(names) -> list[str]:
    return [
        key
        for key, constant in CONSTANTS.items()
        if getattr(names, key) != getattr(schedule, constant)
    ]


def test_reaper_names_equal_the_schedule_module():
    assert mismatches(naming().schedules["artifact_reaper"]) == []


@pytest.mark.parametrize("key", CONSTANTS)
def test_reaper_names_mismatch_is_caught(key):
    names = naming().schedules["artifact_reaper"]
    changed = names.model_copy(update={key: f"{getattr(names, key)}-x"})
    assert mismatches(changed) == [key]
