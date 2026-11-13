"""The job workflow: one admitted job, one workflow that loads the plan, places each ready task on
a pool queue and ends the job with a terminal result.

`JobWorkflow.run(accepted, params)` takes the accepted event and the job's params block, writes
every job and task event through the one `emit` point, takes the control signals `cancel`,
`terminate`, `pause` and `resume`, and answers the query `status`.
"""

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from temporalio import workflow
from temporalio.common import VersioningBehavior
from temporalio.exceptions import ApplicationError
from temporalio.workflow import ActivityHandle

from . import taskpath

with workflow.unsafe.imports_passed_through():
    from wdf_events import Event, build_event
    from wdf_events.models import JobParams, ManifestReference
    from wdf_jobdoc.plan_models import Plan
    from wdf_jobpatch import patch_check
    from wdf_jobstate import status_vocabulary

    from .activities.plan import limits
    from .budgets import Budgets, fresh, resumed
    from .contract import naming
    from .models import ControlSignal, ResumeSignal, StatusQuery, TaskState, TaskStatus
    from .ready import DONE, initial, resume, settle, to_interrupt, to_place
    from .result import Outcome, Stop, outcome
    from .testing.control import Emit, Emitted, LoadedPlan, LoadPlan, Transition

SOURCE = "/wdf/workflow"
# The time format of wdf_events.models.TIME_PATTERN, in which a worker reports its start.
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
# The control requests that end a job, each overriding the ones before it.
ENDING = ("cancel", "terminate")
RANK: dict[Stop | None, int] = {None: 0, "pause": 1, "cancel": 2, "terminate": 3}
ENDED_BY_REQUEST = ("cancelled", "terminated")


class Ended(Exception):
    """The job row reached a terminal status that the workflow did not move it to."""

    def __init__(self, status: str):
        super().__init__(f"the job ended outside the workflow as {status}")
        self.status = status


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
        # The control request in force, the strongest one received.
        self.stop: Stop | None = None
        # The last resume applied, which a paused job runs again from.
        self.resume_request: ResumeSignal | None = None
        self.plan: Plan | None = None
        self.tasks: dict[str, Any] = {}
        self.states: dict[str, TaskState] = {}
        self.attempts: Counter[str] = Counter()
        self.budgets: dict[str, Budgets] = {}
        # The output references of each done task, by kind.
        self.outputs: dict[str, dict[str, Any]] = {}
        self.cache_keys: dict[str, str] = {}
        # The compile tasks a cache hit satisfied, the hits of each, and the rebuild of each lost
        # component.
        self.cache_hits: set[str] = set()
        self.skips: Counter[str] = Counter()
        # The placements of each task, which a withdrawn attempt does not take back.
        self.placements: Counter[str] = Counter()
        self.rebuilds: dict[tuple[str, str], asyncio.Future] = {}
        # The path of each task in flight, and its pool activity once scheduled.
        self.running: dict[str, asyncio.Task] = {}
        self.in_flight: dict[str, ActivityHandle] = {}
        # Set once the history reaches its budget, after which the job fails once nothing runs.
        self.fail_reason: str | None = None
        meter = workflow.metric_meter()
        self.history_events = meter.create_histogram(
            "wdf_workflow_history_events", "History length of a job workflow at each wake."
        )
        self.lost_transitions = meter.create_counter(
            "wdf_workflow_lost_transitions_total", "Status moves of the job row that lost."
        )
        self.retries = meter.create_counter(
            "wdf_workflow_retries_total", "Attempts run again after a failure, by budget charged."
        )
        self.reschedules = meter.create_counter(
            "wdf_workflow_reschedules_total", "Schedule-to-start timeouts followed by a new place."
        )

    @workflow.run
    async def run(self, accepted: Event, params: JobParams) -> str:
        """Runs the job to its end and returns its status.

        A cancel request of the workflow itself ends the job as the `cancel` signal does, and then
        ends the workflow cancelled, unless the job ended otherwise first. The job runs shielded
        from it, so no move of the row is interrupted halfway.
        """
        job = asyncio.create_task(self.job(accepted, params))
        try:
            return await asyncio.shield(job)
        except asyncio.CancelledError:
            self.request("cancel")
            if await job in ENDED_BY_REQUEST:
                raise
            return self.job_status

    async def job(self, accepted: Event, params: JobParams) -> str:
        self.identity = Identity.of(accepted, params.class_override)
        self.job_id = self.identity.jobid
        self.params = params
        manifest, self.batch_id = start_facts(accepted)
        try:
            try:
                loaded = await taskpath.control(
                    "load_plan", LoadPlan(manifest=manifest, params=params), LoadedPlan
                )
            except taskpath.TaskFailed as failure:
                await self.end(Outcome("failed", None, failure.reason, (), {}))
                return self.job_status
            self.plan = loaded.plan
            self.limits = limits().profiles[self.plan.profile]
            self.max_history_events = loaded.max_history_events
            self.states = initial(self.plan)
            self.tasks = {task.id: task for task in self.plan.tasks}
            self.budgets = {task.id: fresh(task) for task in self.plan.tasks}
            await self.emit(self.job_event("job.started", {}), move="running")
            await self.end(await self.drain())
            while self.job_status == "paused":
                # A paused job waits for a resume or its end, without a timer, so the wait adds
                # nothing to its history.
                await workflow.wait_condition(
                    lambda: self.stop in ENDING or self.resume_request is not None
                )
                if self.stop in ENDING:
                    await self.end(outcome(self.plan, self.states, stop=self.stop))
                elif await self.run_again():
                    await self.end(await self.drain())
        except Ended as ended:
            # The job ended outside the workflow: its tasks are cancelled and nothing more is
            # written.
            self.request("cancel")
            self.interrupt()
            await workflow.wait_condition(lambda: all(t.done() for t in self.running.values()))
            self.job_status = ended.status
            self.pause_reason = None
            if self.plan is not None:
                self.states = outcome(self.plan, self.states, stop="terminate").states
        return self.job_status

    async def drain(self) -> Outcome:
        """Places each ready task and waits on the tasks in flight until the job stops, acting on
        each control request once as it arrives."""
        acted: Stop | None = None
        while True:
            if self.stop != acted:
                acted = self.stop
                self.interrupt()
            spent = self.history_spent()
            if (
                end := outcome(self.plan, self.states, stop=self.stop, fail_reason=self.fail_reason)
            ) is not None:
                return end
            for tid in [] if spent or self.stop else to_place(self.plan, self.states):
                self.states[tid] = "running"
                self.running[tid] = asyncio.create_task(taskpath.path(self, self.tasks[tid]))
            await workflow.wait_condition(
                lambda acted=acted: (
                    self.stop != acted or any(t.done() for t in self.running.values())
                )
            )
            for tid, task in list(self.running.items()):
                if task.done():
                    del self.running[tid]
                    # A defect raised on a task path fails the workflow task, which Temporal
                    # retries. A path re-raises the cancel that ended it.
                    if not task.cancelled():
                        task.result()

    async def run_again(self) -> bool:
        """Resumes a paused job on the last resume applied, and returns whether it runs again.

        The row moves from `resume-pending` back to `running` with `job.resumed` under the next
        resume counter before anything else runs. Then the patched plan, if any, is put in force,
        and every task that has not succeeded runs again with fresh budgets from the head of its
        kept checkpoints, the compile tasks the patch changed among them with every task after
        them.
        A cancel or terminate that arrives while the move waits for the row to reach
        `resume-pending` wins, and the job stays paused for it to end.
        A patch that does not load, or that the patch check refuses, runs nothing, and the job
        pauses again for the reason it had, since the row has no move from `resume-pending` back
        to `paused`.
        """
        request, self.resume_request = self.resume_request, None
        reason = self.pause_reason
        if self.stop == "pause":
            # The manual pause is over, which request() never allows, since it only strengthens.
            # It ends before the move, so a pause that arrives during the move stays in force.
            self.stop = None
        sequence = self.resume_counter + 1
        resumed_event = self.identity.event("job.resumed", workflow.now(), {}, sequence=sequence)
        if not await self.emit(resumed_event, move="running", expected="resume-pending"):
            return False
        self.resume_counter = sequence
        self.pause_reason = None
        rerun: tuple[str, ...] = ()
        if request.manifest is not None:
            if (patched := await self.patch(request.manifest)) is None:
                await self.end(Outcome("paused", reason, None, (), self.states))
                return False
            rerun = patched
        self.states = resume(self.plan, self.states, rerun)
        for tid, state in self.states.items():
            if state not in DONE:
                self.budgets[tid] = resumed(self.tasks[tid], self.budgets[tid])
        return True

    async def patch(self, manifest: ManifestReference) -> tuple[str, ...] | None:
        """Loads the patched plan of a resume and puts it in force, returning the compile tasks it
        runs again, or None when it does not load or the patch check refuses it."""
        try:
            loaded = await taskpath.control(
                "load_plan", LoadPlan(manifest=manifest, params=self.params), LoadedPlan
            )
        except taskpath.TaskFailed as failure:
            workflow.logger.warning("the patch of %s does not load: %s", self.job_id, failure)
            return None
        done = {tid for tid, state in self.states.items() if state in DONE}
        report = patch_check(self.plan, loaded.plan, done)
        if not report.compatible:
            codes = [issue.code for issue in report.issues]
            workflow.logger.warning("the patch of %s is refused: %s", self.job_id, codes)
            return None
        self.plan = loaded.plan
        self.tasks = {task.id: task for task in self.plan.tasks}
        self.max_history_events = loaded.max_history_events
        return report.recompile

    def interrupt(self) -> None:
        """Acts on the control request in force: a manual pause cancels the pool activity of each
        running checkpoint-capable task, whose worker takes a final checkpoint, and a cancel or
        terminate cancels every task path, which cancels its activity."""
        if self.stop == "pause":
            for tid in to_interrupt(self.plan, self.states):
                if (handle := self.in_flight.get(tid)) is not None:
                    handle.cancel()
        elif self.stop in ENDING:
            for task in self.running.values():
                task.cancel()

    def request(self, stop: Stop) -> None:
        """Puts a control request in force unless a stronger one already is."""
        if RANK[stop] > RANK[self.stop]:
            self.stop = stop

    def ended(self) -> bool:
        return self.job_status in status_vocabulary().terminal

    @workflow.signal(name="cancel")
    def cancel(self, signal: ControlSignal) -> None:
        self.end_signal("cancel", signal)

    @workflow.signal(name="terminate")
    def terminate(self, signal: ControlSignal) -> None:
        self.end_signal("terminate", signal)

    def end_signal(self, stop: Stop, signal: ControlSignal) -> None:
        """A cancel or terminate applies whenever it arrives unless the job has ended, and a
        repeat changes nothing.

        Its sequence counts as applied, so a pause or resume read from the row before it, which
        carries no higher sequence, is ignored after it.
        """
        if self.ended():
            return
        self.control_seq = max(self.control_seq, signal.control_seq)
        self.request(stop)

    @workflow.signal(name="pause")
    def pause(self, signal: ControlSignal) -> None:
        if self.newer(signal.control_seq):
            self.request("pause")
            # A pause after a resume leaves nothing to resume: control asks for a pause only of an
            # admitted or running job, so the resume was applied already or is stale.
            self.resume_request = None

    @workflow.signal(name="resume")
    def resume(self, signal: ResumeSignal) -> None:
        if self.newer(signal.control_seq) and self.stop not in ENDING:
            self.resume_request = signal

    def newer(self, control_seq: int) -> bool:
        """Whether a pause or resume applies: only above the last applied sequence, and never
        after the job ended. An applied one takes its sequence."""
        if self.ended() or control_seq <= self.control_seq:
            return False
        self.control_seq = control_seq
        return True

    def finish(self, task_id: str, state: TaskState) -> None:
        """Records the state a task moved to and settles the tasks that wait on it."""
        self.states = settle(self.plan, {**self.states, task_id: state})

    def history_spent(self) -> bool:
        """Records the history length, and whether it reached the budget of the plan's limits
        profile, from which on no new task or attempt is placed."""
        length = workflow.info().get_current_history_length()
        self.history_events.record(length)
        if self.fail_reason is None and length >= self.max_history_events:
            self.fail_reason = f"history-budget: {length}"
        return self.fail_reason is not None

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

    async def emit(
        self, *events: Event, move: str | None = None, expected: str | None = None
    ) -> bool:
        """Writes a batch of events, and the move of the job row to `move` from `expected`, by
        default the workflow's own status, with them, and returns whether it did.

        A move that lost wrote none of the batch. A row already at the target was moved by an
        earlier send of this batch. A row at a terminal status raises Ended. A cancel or terminate
        of a paused job that control moved to `resume-pending` meanwhile is tried once more from
        there. Any other loss is sent again with the backoff of `emit` until the row and the
        workflow agree, and the job places nothing meanwhile, except that a resume gives way to a
        cancel or terminate in force: it returns False and the job keeps its status.
        A typed error of `emit` is a defect of this build and fails the workflow task, which
        Temporal retries until a fixed build runs it.
        """
        expected = expected or self.job_status
        entry = taskpath.named(taskpath.NAMING.activities.local, "emit")
        retry = taskpath.retry_policy(entry.retry)
        backoff = retry.initial_interval
        while True:
            transition = Transition(expected=expected, to=move) if move else None
            batch = Emit(job_id=self.job_id, events=list(events), transition=transition)
            emitted: Emitted = await taskpath.local("emit", [batch], Emitted)
            lost = emitted.lost
            if lost is None or lost == move:
                break
            if lost in status_vocabulary().terminal:
                raise Ended(lost)
            ending = move in ("cancelled", "terminated")
            if lost == "resume-pending" and expected == "paused" and ending:
                expected = lost
                continue
            if move == "running" and expected == "resume-pending" and self.stop in ENDING:
                return False
            self.lost_transitions.add(1)
            workflow.logger.warning("the move %s lost, the row is %s", transition, lost)
            await workflow.sleep(backoff)
            backoff = min(backoff * retry.backoff_coefficient, retry.maximum_interval)
        if move is not None:
            self.job_status = move
        return True

    @workflow.query(name=naming().queries[0])
    def status(self) -> StatusQuery:
        if self.plan is None:
            raise ApplicationError(f"the plan of {self.job_id} is not loaded")
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
                    guest_left=self.budgets[tid].guest_left,
                    infra_left=self.budgets[tid].infra_left,
                    reschedules=self.budgets[tid].reschedules,
                    last_class=self.budgets[tid].last_class,
                    checkpoint=self.budgets[tid].checkpoint,
                )
                for tid, state in self.states.items()
            ],
        )
