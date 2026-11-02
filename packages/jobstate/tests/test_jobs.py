"""The guarded library against a real Postgres."""

import itertools
import threading
import uuid

import psycopg
import pytest
from wdf_jobstate import (
    ControlRequest,
    IllegalTransition,
    JobNotFound,
    JobRow,
    Lost,
    Moved,
    get_job,
    insert_accepted,
    mark_delivered,
    mark_published,
    request_action,
    status_vocabulary,
    transition,
)

pytestmark = pytest.mark.integration

SHA = "a" * 64
VOCABULARY = status_vocabulary()
KEYS = itertools.count()


def make_row(**overrides) -> JobRow:
    job_id = str(uuid.uuid4())
    fields = {
        "job_id": job_id,
        "tenant": "acme",
        "submission_key": f"key-{next(KEYS)}",
        "batch_id": str(uuid.uuid4()),
        "priority_class": "standard",
        "manifest_ref": {
            "uri": f"s3://wdf-sources/sha256/aa/{SHA}",
            "sha256": SHA,
            "size": 7,
            "name": "m",
            "kind": "manifest",
        },
        "schema_version": "wdf.job.accepted/3",
        "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
        "seed": 3,
        "parameters": {"a": 1},
        "fingerprint": "b" * 64,
    }
    return JobRow(**{**fields, **overrides})


@pytest.fixture
def conn(appdb):
    with psycopg.connect(appdb, autocommit=True) as connection:
        yield connection


@pytest.fixture
def job(conn):
    row = make_row()
    insert_accepted(conn, [row])
    return row.job_id


def put_in(conn, job_id, status):
    """Walks a job to `status` along the shortest legal path, as the workflow and control would."""
    paths = {
        "accepted": [],
        "admitted": [("accepted", "admitted", False)],
        "running": [("accepted", "admitted", False), ("admitted", "running", False)],
        "paused": [("accepted", "paused", True)],
        "resume-pending": [("accepted", "paused", True), ("paused", "resume-pending", False)],
        "succeeded": [
            ("accepted", "admitted", False),
            ("admitted", "running", False),
            ("running", "succeeded", False),
        ],
        "partial": [
            ("accepted", "admitted", False),
            ("admitted", "running", False),
            ("running", "partial", False),
        ],
        "failed": [("accepted", "admitted", False), ("admitted", "failed", False)],
        "cancelled": [("accepted", "cancelled", True)],
        "terminated": [("accepted", "terminated", True)],
    }
    for expected, to, row_only in paths[status]:
        assert transition(conn, job_id, expected, to, row_only=row_only) == Moved(to)


def test_insert_then_get_round_trips_the_row(conn):
    row = make_row()
    result = insert_accepted(conn, [row])
    assert result.inserted == [row.job_id] and result.duplicates == []
    stored = get_job(conn, row.job_id)
    assert stored.model_dump(
        exclude={"created_at", "updated_at", "manifest_sha256"}
    ) == row.model_dump(exclude={"created_at", "updated_at", "manifest_sha256"})
    assert stored.status == "accepted" and stored.manifest_sha256 == SHA
    assert stored.created_at is not None and stored.terminal_at is None


def test_get_job_of_an_unknown_job_is_none(conn):
    assert get_job(conn, str(uuid.uuid4())) is None


def test_insert_accepted_reports_the_fingerprint_of_a_duplicate(conn):
    first = make_row(fingerprint="c" * 64)
    insert_accepted(conn, [first])
    transition(conn, first.job_id, "accepted", "admitted")
    again = make_row(tenant=first.tenant, submission_key=first.submission_key, fingerprint="d" * 64)
    fresh = make_row()
    result = insert_accepted(conn, [again, fresh])
    assert result.inserted == [fresh.job_id]
    [duplicate] = result.duplicates
    assert (duplicate.job_id, duplicate.status, duplicate.fingerprint) == (
        first.job_id,
        "admitted",
        "c" * 64,
    )


def test_a_duplicate_inside_one_batch_is_reported(conn):
    first = make_row()
    second = make_row(tenant=first.tenant, submission_key=first.submission_key)
    result = insert_accepted(conn, [first, second])
    assert result.inserted == [first.job_id] and result.duplicates[0].job_id == first.job_id


def test_nothing_is_committed_by_the_library(appdb):
    row = make_row()
    with psycopg.connect(appdb) as writer:
        insert_accepted(writer, [row])
        assert get_job(writer, row.job_id) is not None
        writer.rollback()
        assert get_job(writer, row.job_id) is None


def test_every_contract_transition_is_accepted_and_every_other_refused(conn):
    for expected, to in itertools.product(VOCABULARY.statuses, repeat=2):
        row = make_row()
        insert_accepted(conn, [row])
        put_in(conn, row.job_id, expected)
        legal = (expected, to) in VOCABULARY.transitions
        if legal:
            assert transition(conn, row.job_id, expected, to, row_only=True) == Moved(to)
            assert get_job(conn, row.job_id).status == to
        else:
            with pytest.raises(IllegalTransition):
                transition(conn, row.job_id, expected, to, row_only=True)
            assert get_job(conn, row.job_id).status == expected


def test_nothing_leaves_a_terminal_status(conn):
    for terminal in VOCABULARY.terminal:
        row = make_row()
        insert_accepted(conn, [row])
        put_in(conn, row.job_id, terminal)
        for to in VOCABULARY.statuses:
            with pytest.raises(IllegalTransition):
                transition(conn, row.job_id, terminal, to, row_only=True)
            if to != terminal:
                with pytest.raises(psycopg.errors.CheckViolation):
                    conn.execute(
                        "UPDATE accepted_job SET status = %s WHERE job_id = %s", (to, row.job_id)
                    )


def test_row_only_moves_need_the_flag(conn):
    row_only = [pair for pair, only in VOCABULARY.transitions.items() if only]
    assert row_only
    for expected, to in row_only:
        row = make_row()
        insert_accepted(conn, [row])
        put_in(conn, row.job_id, expected)
        with pytest.raises(IllegalTransition):
            transition(conn, row.job_id, expected, to)
        assert get_job(conn, row.job_id).status == expected
        assert transition(conn, row.job_id, expected, to, row_only=True) == Moved(to)


def test_late_writer_loses(appdb):
    row = make_row()
    with psycopg.connect(appdb, autocommit=True) as setup:
        insert_accepted(setup, [row])
        put_in(setup, row.job_id, "admitted")
    results = {}
    first = psycopg.connect(appdb)
    second = psycopg.connect(appdb)
    try:
        results["first"] = transition(first, row.job_id, "admitted", "running")

        def late():
            results["second"] = transition(second, row.job_id, "admitted", "cancelled")
            second.commit()

        thread = threading.Thread(target=late)
        thread.start()
        thread.join(timeout=1)
        assert thread.is_alive(), "the second writer waits for the row lock of the first"
        first.commit()
        thread.join(timeout=30)
    finally:
        first.close()
        second.close()
    assert results["first"] == Moved("running")
    assert results["second"] == Lost("running")


def test_replayed_message_is_a_noop_for_a_terminal_row(conn, job):
    put_in(conn, job, "running")
    assert transition(conn, job, "running", "succeeded") == Moved("succeeded")
    stamp = get_job(conn, job).terminal_at
    assert transition(conn, job, "running", "succeeded") == Lost("succeeded")
    assert transition(conn, job, "running", "failed") == Lost("succeeded")
    assert get_job(conn, job).terminal_at == stamp


def test_a_move_of_an_unknown_job_raises(conn):
    with pytest.raises(JobNotFound):
        transition(conn, str(uuid.uuid4()), "accepted", "admitted")


def test_terminal_at_is_set_on_the_terminal_move(conn, job):
    put_in(conn, job, "running")
    assert get_job(conn, job).terminal_at is None
    transition(conn, job, "running", "partial")
    assert get_job(conn, job).terminal_at is not None


def test_updated_at_moves_on_every_transition(conn, job):
    before = get_job(conn, job).updated_at
    transition(conn, job, "accepted", "admitted")
    assert get_job(conn, job).updated_at >= before


def test_request_action_repeat_is_a_noop_and_a_new_action_takes_the_next_sequence(conn, job):
    assert request_action(conn, job, "pause") == ControlRequest("pause", 1)
    assert request_action(conn, job, "pause") == ControlRequest("pause", 1)
    assert request_action(conn, job, "cancel") == ControlRequest("cancel", 2)
    row = get_job(conn, job)
    assert (row.requested_action, row.control_seq) == ("cancel", 2)


def test_request_action_refuses_an_unknown_action_and_job(conn, job):
    with pytest.raises(ValueError):
        request_action(conn, job, "pausing")
    with pytest.raises(JobNotFound):
        request_action(conn, str(uuid.uuid4()), "pause")


def test_repeat_after_delivery_is_still_a_noop(conn, job):
    first = request_action(conn, job, "pause")
    mark_delivered(conn, job, first.control_seq)
    delivered = get_job(conn, job)
    assert delivered.control_delivered_at is not None and delivered.requested_action == "pause"
    assert request_action(conn, job, "pause") == first
    assert get_job(conn, job).control_delivered_at == delivered.control_delivered_at


def test_a_new_request_clears_the_delivery_and_a_stale_delivery_is_ignored(conn, job):
    first = request_action(conn, job, "pause")
    mark_delivered(conn, job, first.control_seq)
    second = request_action(conn, job, "cancel")
    assert get_job(conn, job).control_delivered_at is None
    mark_delivered(conn, job, first.control_seq)
    assert get_job(conn, job).control_delivered_at is None
    mark_delivered(conn, job, second.control_seq)
    assert get_job(conn, job).control_delivered_at is not None


def test_the_move_into_paused_clears_the_requested_pause(conn, job):
    put_in(conn, job, "running")
    request_action(conn, job, "pause")
    transition(conn, job, "running", "paused")
    assert get_job(conn, job).requested_action is None


def test_the_move_into_paused_keeps_another_outstanding_action(conn, job):
    put_in(conn, job, "running")
    request_action(conn, job, "cancel")
    transition(conn, job, "running", "paused")
    assert get_job(conn, job).requested_action == "cancel"


def test_a_resume_is_cleared_by_the_move_that_applies_it(conn, job):
    put_in(conn, job, "resume-pending")
    request_action(conn, job, "resume")
    transition(conn, job, "resume-pending", "running")
    assert get_job(conn, job).requested_action is None
    other = make_row()
    insert_accepted(conn, [other])
    put_in(conn, other.job_id, "resume-pending")
    request_action(conn, other.job_id, "resume")
    transition(conn, other.job_id, "resume-pending", "accepted", row_only=True)
    assert get_job(conn, other.job_id).requested_action is None


def test_a_terminal_move_clears_the_requested_action(conn, job):
    put_in(conn, job, "running")
    request_action(conn, job, "cancel")
    transition(conn, job, "running", "cancelled")
    row = get_job(conn, job)
    assert row.requested_action is None and row.control_seq == 1


def test_mark_published_keeps_the_first_receipt(conn, job):
    assert get_job(conn, job).published_at is None
    mark_published(conn, job)
    first = get_job(conn, job).published_at
    assert first is not None
    conn.execute("SELECT pg_sleep(0.01)")
    mark_published(conn, job)
    assert get_job(conn, job).published_at == first
    with pytest.raises(JobNotFound):
        mark_published(conn, str(uuid.uuid4()))


def test_status_vocabulary_is_the_contract_and_the_table(conn):
    assert len(VOCABULARY.statuses) == 10 and len(VOCABULARY.transitions) == 22
    in_table = set(
        conn.execute("SELECT from_status, to_status, row_only FROM job_transition").fetchall()
    )
    assert in_table == {(a, b, only) for (a, b), only in VOCABULARY.transitions.items()}
    assert {
        s for s, t in conn.execute("SELECT status, terminal FROM job_status").fetchall() if t
    } == VOCABULARY.terminal
