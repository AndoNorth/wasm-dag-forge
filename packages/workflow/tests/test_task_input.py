"""A task input keeps the bytes of its request through the payload converter of the workflow."""

import pytest
from wdf_events import to_bytes
from wdf_temporal import event_data_converter
from wdf_workflow.contract import canonical
from wdf_workflow.models import TaskInput

CONVERTER = event_data_converter.payload_converter


@pytest.mark.parametrize("name", ["compile", "simulate", "aggregate", "simulate-resumed"])
def test_task_input_round_trip(task_input, name):
    given = TaskInput.model_validate(task_input(name))
    request = to_bytes(given.request)
    [payload] = CONVERTER.to_payloads([given])
    assert b'"request":' + request + b"," in payload.data
    assert payload.data == canonical(given)
    [back] = CONVERTER.from_payloads([payload], [TaskInput])
    assert to_bytes(back.request) == request
    assert back == given


def test_task_input_params_keep_their_shape(task_input):
    shapes = {
        name: type(TaskInput.model_validate(task_input(name)).params).__name__
        for name in ("compile", "simulate", "aggregate")
    }
    assert shapes == {
        "compile": "CompileParams",
        "simulate": "Input",
        "aggregate": "BoundSummaryStatsParams",
    }
