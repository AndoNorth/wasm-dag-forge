"""A stand-in for the job workflow: events in, one task activity, an event out.

It exists to exercise the event contract through Temporal, and the real workflow replaces it.
"""

import hashlib
from datetime import UTC, datetime, timedelta

from temporalio import activity, workflow
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

with workflow.unsafe.imports_passed_through():
    from wdf_events import Event, build_event, check_task_request, to_bytes
    from wdf_events.models import JobParams
    from wdf_keys import attempt_identity

TASK_ID = "sim"
WORKFLOW_SOURCE = "/wdf/workflow"
WORKER_SOURCE = "/wdf/worker"
TASK_QUEUE = "wdf-standin"

# The platform packages and what they import must not be re-imported inside the workflow sandbox,
# where pydantic models would be rebuilt per workflow run.
RUNNER = SandboxedWorkflowRunner(
    restrictions=SandboxRestrictions.default.with_passthrough_modules(
        "wdf_events",
        "wdf_keys",
        "wdf_temporal",
        "pydantic",
        "pydantic_core",
        "jsonschema",
        "jsonschema_specifications",
        "referencing",
        "rpds",
        "attrs",
        "yaml",
        "psycopg",
    )
)


@activity.defn
async def run_task(request: Event) -> Event:
    """The worker side: checks the request and answers with a result ready event."""
    task = check_task_request(to_bytes(request))
    attempt = activity.info().attempt
    digest = hashlib.sha256(task.id.encode()).hexdigest()
    return build_event(
        "task.result.ready",
        source=WORKER_SOURCE,
        tenant=task.tenant,
        jobid=task.jobid,
        priorityclass=task.priorityclass,
        jobclass=task.jobclass,
        traceparent=task.traceparent,
        time=datetime.now(UTC),
        taskid=task.taskid,
        attempt=attempt,
        data={
            "result": {
                "uri": f"s3://wdf-results/{task.tenant}/{task.jobid}/{task.taskid}/{attempt}/result.bin",
                "sha256": digest,
                "size": 64,
                "name": "result.bin",
                "kind": "result",
                "producer": {
                    "job": task.jobid,
                    "task": task.taskid,
                    "attempt": attempt_identity(attempt, 0),
                },
            }
        },
    )


@workflow.defn
class StandInJob:
    @workflow.run
    async def run(self, accepted: Event, params: JobParams) -> Event:
        now = workflow.now()
        request = build_event(
            "task.requested",
            source=WORKFLOW_SOURCE,
            tenant=accepted.tenant,
            jobid=accepted.jobid,
            priorityclass=accepted.priorityclass,
            jobclass=params.class_override or accepted.jobclass,
            traceparent=accepted.traceparent,
            time=now,
            taskid=TASK_ID,
            data={
                "task_type": "simulate",
                "cpu_cores": 1,
                "memory_mib": 256,
                "inputs": [accepted.data["manifest"]],
                "deadline": (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            },
        )
        return await workflow.execute_activity(
            run_task, request, start_to_close_timeout=timedelta(seconds=30)
        )
