"""A whole job through the fakes: ids are unique and a replay is byte-equal and inserts nothing."""

from datetime import timedelta

from wdf_events import Event, to_bytes, to_event_row
from wdf_stages import FAKES, Facts, build_task_request, invoke
from wdf_stages.contract import sample_event
from wdf_stages.facts import EPOCH
from wdf_stages.recording import type_name


def run_flow() -> list[Event]:
    """One job, three tasks, a retry exhaustion, a resume and a second pause and resume pair."""
    events: list[Event] = []
    ticks = iter(range(1, 1000))
    state = {"resume": 0}

    def step(stage, *emit, inbound=None, **facts):
        moment = EPOCH + timedelta(seconds=next(ticks))
        facts.setdefault("resume_counter", state["resume"])
        events.extend(invoke(stage, FAKES[stage], inbound, Facts(time=moment, emit=emit, **facts)))

    def run_task(task, attempts_failed=0, **facts):
        facts = {"taskid": task, **facts}
        # A task placed again after a resume needs its own sequence, because the id of a task level
        # event carries no attempt identity.
        step("workflow", "task.placed", sequence=state["resume"], **facts)
        request = build_task_request(Facts(taskid=task, resume_counter=state["resume"]))
        step("placement", inbound=request, **facts)
        for attempt in range(1, attempts_failed + 1):
            step("workflow", "task.started", attempt=attempt, **facts)
            step("worker", inbound=request, attempt=attempt, **facts)
            step("workflow", "task.attempt.failed", attempt=attempt, **facts)
        if attempts_failed:
            step("workflow", "task.retries.exhausted", **facts)
            return
        step("workflow", "task.started", attempt=1, **facts)
        step("worker", inbound=request, attempt=1, **facts)
        step("workflow", "task.succeeded", "task.result.ready", attempt=1, **facts)

    def pause_and_resume(sequence):
        step("control", "job.pause.requested", sequence=sequence)
        step("workflow", "job.paused", sequence=sequence)
        step("control", "job.resume.requested", sequence=sequence)
        state["resume"] += 1
        step("workflow", "job.resumed", sequence=sequence)

    accepted = sample_event("job.accepted", Facts())
    step("dispatch", "job.accepted", "job.published")
    step("admission", "job.admitted", inbound=accepted)
    step("workflow", "job.started", inbound=accepted)
    run_task("compile")
    run_task("sim", attempts_failed=3)
    pause_and_resume(0)
    run_task("sim")
    pause_and_resume(1)
    run_task("agg")
    step("workflow", "job.succeeded")
    return events


def rows(events):
    return {(key, moment) for key, moment, _ in map(to_event_row, events)}


def test_no_two_events_share_a_source_and_id():
    events = run_flow()
    keys = [(e.source, e.id) for e in events]
    assert len(keys) == len(set(keys))
    kinds = [type_name(e.type) for e in events]
    assert kinds.count("task.retries.exhausted") == 1
    assert kinds.count("job.paused") == kinds.count("job.resumed") == 2
    assert {e.taskid for e in events if e.taskid} == {"compile", "sim", "agg"}


def test_the_task_request_is_built_but_never_in_the_history():
    assert not [e for e in run_flow() if type_name(e.type) == "task.requested"]


def test_a_second_run_gives_the_same_bytes():
    assert [to_bytes(e) for e in run_flow()] == [to_bytes(e) for e in run_flow()]


def test_a_replay_inserts_no_new_row_key_and_event_time_pair():
    stored = rows(run_flow())
    assert len(stored) == len(run_flow())
    assert rows(run_flow()) <= stored
