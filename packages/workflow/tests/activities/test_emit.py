"""The emit local activity against a real database, as a plain function under a `wdf_workflow`
login."""

import asyncio
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from temporalio.exceptions import ApplicationError
from wdf_events import build_event, to_event_row
from wdf_jobstate import transition
from wdf_workflow.activities import Emitter
from wdf_workflow.testing.control import ControlCase, Emit, Transition, control_activity_contract

pytestmark = [pytest.mark.integration, pytest.mark.pair]

FACT = datetime(2026, 11, 12, 9, 30, tzinfo=UTC)


@pytest.fixture
def emitter(login):
    return Emitter(login("wdf_workflow"))


@pytest.fixture
def batch(started_event):
    """The events of one move of a job, built from the facts so that a rebuild has the same ids."""

    def build(job_id: str, name: str, expected: str | None, to: str | None, shift=timedelta(0)):
        common = {
            "source": "/wdf/workflow",
            "tenant": started_event.tenant,
            "jobid": job_id,
            "priorityclass": started_event.priorityclass,
            "jobclass": started_event.jobclass,
            "traceparent": started_event.traceparent,
        }
        events = [build_event(name, time=FACT + shift, data={}, **common)]
        if name == "job.started":
            events.append(
                build_event(
                    "task.skipped",
                    time=FACT + shift,
                    data={"reason": "compile-cache-hit", "cache_key": "c" * 64},
                    taskid="build",
                    **common,
                )
            )
        move = Transition(expected=expected, to=to) if to else None
        return Emit(job_id=job_id, events=events, transition=move)

    return build


def test_emit_writes_events_and_moves_row(emitter, job, batch, status, stored):
    job_id = job("admitted")
    sent = batch(job_id, "job.started", "admitted", "running")
    answer = emitter.emit(sent)
    assert (answer.inserted, answer.lost) == (2, None)
    assert status(job_id) == "running"
    assert sorted(i for i, _ in stored(job_id)) == sorted(to_event_row(e)[0] for e in sent.events)


def test_emit_replay_inserts_nothing(emitter, job, batch, status, stored):
    job_id = job("admitted")
    sent = batch(job_id, "job.started", "admitted", "running")
    emitter.emit(sent)
    before = stored(job_id)
    answer = emitter.emit(sent)
    assert (answer.inserted, answer.lost) == (0, None)
    assert stored(job_id) == before
    assert status(job_id) == "running"


def test_emit_retry_with_shifted_time_inserts_nothing(emitter, job, batch, stored):
    job_id = job("admitted")
    emitter.emit(batch(job_id, "job.started", "admitted", "running"))
    before = stored(job_id)
    shifted = batch(job_id, "job.started", "admitted", "running", shift=timedelta(seconds=1))
    assert [e.id for e in shifted.events] == [
        e.id for e in batch(job_id, "job.started", "admitted", "running").events
    ]
    assert emitter.emit(shifted).inserted == 0
    assert stored(job_id) == before


def test_emit_without_move_writes_each_id_once(emitter, job, batch, stored):
    job_id = job("admitted", "running")
    sent = batch(job_id, "job.started", None, None)
    twice = sent.model_copy(update={"events": [*sent.events, *sent.events]})
    assert emitter.emit(twice).inserted == 2
    assert len(stored(job_id)) == 2


def test_emit_lost_cases(emitter, job, batch, status, stored, db, login):
    control = psycopg.connect(login("wdf_control"), autocommit=True)

    # The row holds the target: an earlier send moved it, so the batch counts as moved.
    at_target = job("admitted", "running")
    answer = emitter.emit(batch(at_target, "job.started", "admitted", "running"))
    assert (answer.inserted, answer.lost) == (2, None)
    assert status(at_target) == "running"

    # The job ended outside the workflow: nothing is written and the terminal status is answered.
    ended = job("admitted", "running")
    db.execute("UPDATE accepted_job SET status = 'cancelled' WHERE job_id = %s", (ended,))
    answer = emitter.emit(batch(ended, "job.succeeded", "running", "succeeded"))
    assert (answer.inserted, answer.lost) == (0, "cancelled")
    assert stored(ended) == []

    # A stop out of paused that control overtook with a resume is taken from resume-pending.
    resumed = job("admitted", "running", "paused")
    transition(control, resumed, "paused", "resume-pending")
    answer = emitter.emit(batch(resumed, "job.cancelled", "paused", "cancelled"))
    assert (answer.inserted, answer.lost) == (1, None)
    assert status(resumed) == "cancelled"

    # Any other status rolls the batch back and is answered for the workflow to send again.
    apart = job("admitted", "running", "paused")
    answer = emitter.emit(batch(apart, "job.succeeded", "running", "succeeded"))
    assert (answer.inserted, answer.lost) == (0, "paused")
    assert status(apart) == "paused"
    assert stored(apart) == []
    control.close()


def test_emit_errors_are_typed(emitter, job, batch):
    with pytest.raises(ApplicationError) as raised:
        emitter.emit(batch(job("admitted", "running"), "job.started", "succeeded", "running"))
    assert (raised.value.type, raised.value.non_retryable) == ("IllegalTransition", True)
    with pytest.raises(ApplicationError) as raised:
        emitter.emit(batch("3a9617fa-df7c-5760-a644-42d559e0f433", "job.started", None, None))
    assert (raised.value.type, raised.value.non_retryable) == ("JobNotFound", True)


def test_emit_refuses_events_of_another_job(emitter, job, batch, stored):
    mine, theirs = job("admitted"), job("admitted")
    sent = batch(mine, "job.started", "admitted", "running")
    mixed = sent.model_copy(update={"events": batch(theirs, "job.started", None, None).events})
    with pytest.raises(ValueError, match="holds events of"):
        emitter.emit(mixed)
    assert stored(mine) == stored(theirs) == []


def test_activities_run_under_their_role(emitter, job, batch, db, login):
    user = psycopg.conninfo.conninfo_to_dict(emitter.dsn)["user"]
    superuser, groups = db.execute(
        "SELECT rolsuper, array(SELECT b.rolname FROM pg_auth_members m"
        " JOIN pg_roles b ON b.oid = m.roleid WHERE m.member = r.oid)"
        " FROM pg_roles r WHERE rolname = %s",
        (user,),
    ).fetchone()
    assert (superuser, groups) == (False, ["wdf_workflow"])
    assert emitter.emit(batch(job("admitted"), "job.started", "admitted", "running")).inserted == 2

    # The same batch under a writer without the workflow's grants is refused by the database.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        Emitter(login("wdf_artifacts")).emit(
            batch(job("admitted"), "job.started", "admitted", "running")
        )


def test_emit_passes_control_contract(emitter, job, batch):
    sent = batch(job("admitted"), "job.started", "admitted", "running")
    cases = [
        ControlCase("emit", sent),
        ControlCase("emit", sent),
        ControlCase(
            "emit", batch(sent.job_id, "job.paused", "paused", "cancelled"), lost="running"
        ),
        ControlCase(
            "emit",
            batch(sent.job_id, "job.started", "succeeded", "running"),
            raises="IllegalTransition",
        ),
        ControlCase(
            "emit",
            batch("3a9617fa-df7c-5760-a644-42d559e0f433", "job.started", None, None),
            raises="JobNotFound",
        ),
    ]
    asyncio.run(control_activity_contract({"emit": emitter.emit}, cases))
