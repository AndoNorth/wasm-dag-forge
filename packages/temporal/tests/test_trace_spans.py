"""One trace over the submit, admission, workflow and activity stand-ins, on a real server."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from temporalio.client import Client
from temporalio.worker import Worker
from wdf_events import build_event, from_bytes, to_bytes
from wdf_events.models import JobParams
from wdf_temporal import (
    current_traceparent,
    event_data_converter,
    new_traceparent,
    stage_span,
    trace_id_of,
    tracing_interceptor,
)
from wdf_temporal.standin import RUNNER, TASK_QUEUE, StandInJob, run_task

pytestmark = pytest.mark.integration

JOB = "3a9617fa-df7c-5760-a644-42d559e0f433"
MANIFEST = {
    "uri": "s3://wdf-sources/sha256/9f/9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "size": 2048,
    "name": "manifest.json",
    "kind": "manifest",
}
# The workflow stage is the client span of the start, because the interceptor parents every
# workflow-side span (the run, the activity start, the completion) on it.
STAGES = ["stage.submit", "stage.admission", "StartWorkflow:StandInJob", "RunActivity:run_task"]


async def connect(port: int, interceptor) -> Client:
    for _ in range(120):
        try:
            return await Client.connect(
                f"127.0.0.1:{port}",
                data_converter=event_data_converter,
                interceptors=[interceptor],
            )
        except RuntimeError:
            await asyncio.sleep(0.5)
    raise AssertionError("the Temporal server did not answer")


async def run_stages(port: int, tracer, interceptor):
    """Submit and admission run as spans around the real start of the stand-in workflow."""
    submitted = new_traceparent()
    with stage_span(tracer, "stage.submit", submitted):
        accepted = build_event(
            "job.accepted",
            source="/wdf/dispatch",
            tenant="acme",
            jobid=JOB,
            priorityclass="standard",
            jobclass="simulate",
            traceparent=current_traceparent(),
            time=datetime.now(UTC),
            data={"manifest": MANIFEST, "batch_id": JOB, "submission_key": "s-1"},
        )
    client = await connect(port, interceptor)
    async with Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[StandInJob],
        activities=[run_task],
        workflow_runner=RUNNER,
    ):
        with stage_span(tracer, "stage.admission", accepted.traceparent):
            handle = await client.start_workflow(
                StandInJob.run,
                args=[accepted, JobParams(seed=1, parameters={})],
                id=f"trace-{uuid.uuid4()}",
                task_queue=TASK_QUEUE,
            )
        result = await handle.result()
    return submitted, accepted, result


def test_every_stage_span_is_on_the_submit_trace_and_follows_the_stage_before(t1):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("wdf")
    with t1.container(
        "temporalio/temporal",
        ports={0: 7233},
        command=("server", "start-dev", "--ip", "0.0.0.0", "--headless"),
    ) as container:
        container.wait_ready(0)
        submitted, accepted, result = asyncio.run(
            run_stages(container.host_ports[0], tracer, tracing_interceptor(tracer))
        )

    spans = {s.context.span_id: s for s in exporter.get_finished_spans()}
    by_name = {}
    for span in spans.values():
        by_name.setdefault(span.name, span)
    trace_id = int(trace_id_of(submitted), 16)
    assert all(name in by_name for name in STAGES), sorted(by_name)
    for name in STAGES:
        assert by_name[name].context.trace_id == trace_id, name

    def ancestors(span):
        # The chain ends at the remote parent that the submit traceparent names.
        while span.parent is not None and span.parent.span_id in spans:
            span = spans[span.parent.span_id]
            yield span

    assert by_name["stage.submit"].parent.span_id == int(submitted.split("-")[2], 16)
    for before, after in zip(STAGES, STAGES[1:], strict=False):
        assert by_name[before] in list(ancestors(by_name[after])), f"{after} is not under {before}"

    assert by_name["StartWorkflow:StandInJob"] in list(ancestors(by_name["RunWorkflow:StandInJob"]))

    for event in (accepted, result, from_bytes(to_bytes(result))):
        assert trace_id_of(event.traceparent) == trace_id_of(submitted)
