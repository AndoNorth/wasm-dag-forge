"""The job workflow: one admitted job, one workflow that loads the plan, places each ready task on
a pool queue and ends the job with a terminal result.

`JobWorkflow.run(accepted, params)` takes the accepted event and the job's params block, writes
every job and task event through the one `emit` point, and answers the query `status`.
"""

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from temporalio import workflow
from temporalio.common import VersioningBehavior
from temporalio.exceptions import ApplicationError

from . import taskpath

with workflow.unsafe.imports_passed_through():
    from wdf_events import Event, build_event
    from wdf_events.models import JobParams, ManifestReference
    from wdf_jobdoc.plan_models import Plan

    from .contract import naming
    from .models import StatusQuery, TaskState, TaskStatus
    from .ready import initial, settle, to_place
    from .result import Outcome, outcome
    from .testing.control import Emit, Emitted, LoadedPlan, LoadPlan, Transition

SOURCE = "/wdf/workflow"
# The infrastructure budget of a task in each run.
INFRA_ATTEMPTS = 10
# The time format of wdf_events.models.TIME_PATTERN, in which a worker reports its start.
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


@dataclass(frozen=True)
class Identity:
    """The context attributes every event of a job carries, read from its accepted event."""

    tenant: str
    jobid: str
    priorityclass: str
    jobclass: str
    traceparent: str

    @classmethod
    def of(cls, accepted: Any, class_override: str | None = None) -> "Identity":
        return cls(
            tenant=accepted.tenant,
            jobid=accepted.jobid,
            priorityclass=accepted.priorityclass,
            jobclass=class_override or accepted.jobclass,
            traceparent=accepted.traceparent,
        )

    def event(self, name: str, time: datetime, data: dict, **keys: Any) -> Event:
        """An event of the workflow stage, whose id derives from the job, task, attempt, type and
        sequence, so a rebuild of the same fact has the same id."""
        return build_event(
            name,
            source=SOURCE,
            time=time,
            data=data,
            tenant=self.tenant,
            jobid=self.jobid,
            priorityclass=self.priorityclass,
            jobclass=self.jobclass,
            traceparent=self.traceparent,
            **keys,
        )


def start_facts(accepted: Any) -> tuple[ManifestReference, str]:
    """What the workflow reads of the data of its accepted event: the manifest and the batch."""
    data = accepted.data
    return ManifestReference.model_validate(data["manifest"]), data["batch_id"]


@workflow.defn(name=naming().workflow.type, versioning_behavior=VersioningBehavior.PINNED)
class JobWorkflow:
    def __init__(self):
        self.job_status = "admitted"
        self.pause_reason: str | None = None
        self.resume_counter = 0
        self.control_seq = 0
        self.plan: Plan | None = None
        self.states: dict[str, TaskState] = {}
        self.attempts: Counter[str] = Counter()
        self.last_class: dict[str, str] = {}
        # The output references of each done task, by kind.
        self.outputs: dict[str, dict[str, Any]] = {}
        self.cache_keys: dict[str, str] = {}
        meter = workflow.metric_meter()
        self.history_events = meter.create_histogram(
            "wdf_workflow_history_events", "History length of a job workflow at each wake."
        )
        self.lost_transitions = meter.create_counter(
            "wdf_workflow_lost_transitions_total", "Status moves of the job row that lost."
        )

    @workflow.run
    async def run(self, accepted: Event, params: JobParams) -> str:
        """Runs the job to its end and returns its status."""
        self.identity = Identity.of(accepted, params.class_override)
        self.job_id = self.identity.jobid
        self.params = params
        manifest, self.batch_id = start_facts(accepted)
        try:
            loaded = await taskpath.control(
                "load_plan", LoadPlan(manifest=manifest, params=params), LoadedPlan
            )
        except taskpath.TaskFailed as failure:
            await self.end(Outcome("failed", None, failure.reason, (), {}))
            return self.job_status
        self.plan = loaded.plan
        self.states = initial(self.plan)
        tasks = {task.id: task for task in self.plan.tasks}
        await self.emit(self.job_event("job.started", {}), move="running")
        running: dict[str, asyncio.Task] = {}
        while (end := outcome(self.plan, self.states)) is None:
            for tid in to_place(self.plan, self.states):
                self.states[tid] = "running"
                running[tid] = asyncio.create_task(taskpath.path(self, tasks[tid]))
            self.history_events.record(workflow.info().get_current_history_length())
            done, _ = await workflow.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                # A defect raised on a task path fails the workflow task, which Temporal retries.
                task.result()
            running = {tid: task for tid, task in running.items() if not task.done()}
        await self.end(end)
        if self.job_status == "paused":
            # A paused job waits for its resume.
            await workflow.wait_condition(lambda: self.job_status != "paused")
        return self.job_status

    def finish(self, task_id: str, state: TaskState) -> None:
        """Records the state a task ended in and settles the tasks that wait on it."""
        self.states = settle(self.plan, {**self.states, task_id: state})

    async def end(self, result: Outcome) -> None:
        """Writes the job's end event and moves the row to its status."""
        data: dict[str, Any] = {}
        if result.status == "failed":
            data = {"reason": result.reason}
        elif result.status == "partial":
            data = {"skipped_tasks": list(result.skipped_tasks)}
        await self.emit(self.job_event(f"job.{result.status}", data), move=result.status)
        self.pause_reason = result.pause_reason
        if self.plan is not None:
            self.states = result.states

    def job_event(self, name: str, data: dict) -> Event:
        # A resume starts the job's events over under its own sequence.
        return self.identity.event(name, workflow.now(), data, sequence=self.resume_counter)

    def task_event(
        self,
        name: str,
        task_id: str,
        data: dict,
        *,
        attempt: int | None = None,
        sequence: int = 0,
        time: str | None = None,
    ) -> Event:
        """A task event at `time`, a time a worker reported, or else now."""
        moment = datetime.strptime(time, TIME_FORMAT).replace(tzinfo=UTC) if time else None
        return self.identity.event(
            name,
            moment or workflow.now(),
            data,
            taskid=task_id,
            attempt=attempt,
            resume_counter=self.resume_counter,
            sequence=sequence,
        )

    async def emit(self, *events: Event, move: str | None = None) -> None:
        """Writes a batch of events, and the move of the job row to `move` with them.

        A typed error of `emit` is a defect of this build, and a move that lost leaves the row and
        the workflow apart, so both fail the workflow task, which Temporal retries until a fixed
        build runs it.
        """
        transition = Transition(expected=self.job_status, to=move) if move else None
        batch = Emit(job_id=self.job_id, events=list(events), transition=transition)
        emitted: Emitted = await taskpath.local("emit", [batch], Emitted)
        # A row already at the target was moved by an earlier send of this batch.
        if emitted.lost is not None and emitted.lost != move:
            self.lost_transitions.add(1)
            raise RuntimeError(f"the move {transition} lost, the row is {emitted.lost}")
        if move is not None:
            self.job_status = move

    @workflow.query(name=naming().queries[0])
    def status(self) -> StatusQuery:
        if self.plan is None:
            raise ApplicationError(f"the plan of {self.job_id} is not loaded")
        retries = {task.id: task.retries for task in self.plan.tasks}
        return StatusQuery(
            job_status=self.job_status,
            pause_reason=self.pause_reason,
            resume_counter=self.resume_counter,
            control_seq=self.control_seq,
            tasks=[
                TaskStatus(
                    id=tid,
                    state=state,
                    task_attempt=self.attempts[tid],
                    guest_left=retries[tid],
                    infra_left=INFRA_ATTEMPTS,
                    reschedules=0,
                    last_class=self.last_class.get(tid),
                    checkpoint=None,
                )
                for tid, state in self.states.items()
            ],
        )
