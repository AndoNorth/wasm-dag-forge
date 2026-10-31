"""The event bytes in a real Temporal history: input payloads, their metadata and a replay."""

import asyncio
import json
import re
import subprocess
import uuid
from pathlib import Path

import pytest
from temporalio.client import Client
from temporalio.worker import Replayer, Worker
from wdf_events import Event, from_bytes, to_bytes
from wdf_events.models import JobParams
from wdf_itest.session import RUN_LABEL
from wdf_temporal import ENCODING, event_data_converter
from wdf_temporal.standin import RUNNER, TASK_QUEUE, StandInJob, run_task

pytestmark = pytest.mark.integration

PACKAGE = Path(__file__).resolve().parents[1]
CONTRACT = PACKAGE.parents[1] / "contracts/events/vectors/accepted/valid"
PARAMS = JobParams(seed=7, parameters={"label": "naïve", "steps": 3})


def declared_form() -> str:
    text = (PACKAGE / "README.md").read_text()
    return re.search(r"^Payload form: `(converter|bytes)`", text, re.MULTILINE).group(1)


def labelled(run_id: str) -> list[str]:
    return subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label={RUN_LABEL}={run_id}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()


async def connect(port: int) -> Client:
    last = None
    for _ in range(120):
        try:
            return await Client.connect(f"127.0.0.1:{port}", data_converter=event_data_converter)
        except RuntimeError as exc:
            last = exc
            await asyncio.sleep(0.5)
    raise last


async def run_once(port: int, accepted: Event):
    client = await connect(port)
    async with Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[StandInJob],
        activities=[run_task],
        workflow_runner=RUNNER,
    ):
        handle = await client.start_workflow(
            StandInJob.run,
            args=[accepted, PARAMS],
            id=f"standin-{uuid.uuid4()}",
            task_queue=TASK_QUEUE,
        )
        result = await handle.result()
        return result, await handle.fetch_history()


def payloads(history, kind: str) -> list:
    for event in history.events:
        if kind == "workflow":
            if event.HasField("workflow_execution_started_event_attributes"):
                return list(event.workflow_execution_started_event_attributes.input.payloads)
        elif event.HasField("activity_task_scheduled_event_attributes"):
            return list(event.activity_task_scheduled_event_attributes.input.payloads)
    raise AssertionError(f"no {kind} input in the history")


@pytest.mark.parametrize("vector", ["accepted.json", "tracestate-and-unicode.json"])
def test_the_history_holds_the_canonical_event_bytes_and_replays(t1, vector):
    accepted = from_bytes((CONTRACT / vector).read_bytes())
    form = declared_form()
    assert form == "converter"
    with t1.container(
        "temporalio/temporal",
        ports={0: 7233},
        command=("server", "start-dev", "--ip", "0.0.0.0", "--headless"),
    ) as container:
        container.wait_ready(0)
        result, history = asyncio.run(run_once(container.host_ports[0], accepted))
        assert labelled(t1.run_id) == [container.id[:12]]

    workflow_input, activity_input = payloads(history, "workflow"), payloads(history, "activity")
    assert workflow_input[0].data == to_bytes(accepted)
    assert workflow_input[0].metadata["encoding"] == ENCODING.encode()
    assert json.loads(workflow_input[0].data)["id"] == accepted.id
    assert workflow_input[1].metadata["encoding"] == ENCODING.encode()
    assert JobParams.model_validate_json(workflow_input[1].data) == PARAMS

    request = from_bytes(activity_input[0].data)
    assert request.type == "dev.wdf.task.requested.v1"
    assert activity_input[0].data == to_bytes(request)
    assert activity_input[0].metadata["encoding"] == ENCODING.encode()

    assert result.type == "dev.wdf.task.result.ready.v1"
    assert (result.jobid, result.traceparent) == (accepted.jobid, accepted.traceparent)

    asyncio.run(
        Replayer(
            workflows=[StandInJob], workflow_runner=RUNNER, data_converter=event_data_converter
        ).replay_workflow(history)
    )
    assert labelled(t1.run_id) == []
