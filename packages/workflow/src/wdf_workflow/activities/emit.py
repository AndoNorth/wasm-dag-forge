"""The `emit` local activity: the one point where the workflow writes events and projects the job
row's status, in one transaction under a login in `wdf_workflow`."""

import psycopg
from temporalio import activity
from wdf_events import to_event_row
from wdf_eventstore import insert_events
from wdf_jobstate import IllegalTransition, JobNotFound, Lost, transition

from ..testing.control import Emit, Emitted, control_error

# A move the workflow makes out of `paused` that control may have overtaken with a resume.
STOPS = ("cancelled", "terminated")
CONNECT_TIMEOUT_S = 10
LOCK_TIMEOUT = "10s"


def _move(conn, request: Emit) -> str | None:
    """Moves the row as the batch says, and answers the row's status when the move lost.

    A row already at the target was moved by an earlier send of this batch. A stop out of
    `paused` that a resume overtook is taken again from `resume-pending`.
    """
    move = request.transition
    if move is None:
        return None
    outcome = transition(conn, request.job_id, move.expected, move.to)
    if (
        isinstance(outcome, Lost)
        and outcome.status == "resume-pending"
        and move.expected == "paused"
        and move.to in STOPS
    ):
        outcome = transition(conn, request.job_id, "resume-pending", move.to)
    if isinstance(outcome, Lost) and outcome.status != move.to:
        return outcome.status
    return None


class Emitter:
    """Opens one connection per batch with the DSN of a login in `wdf_workflow`."""

    def __init__(self, dsn: str):
        self.dsn = dsn

    @activity.defn(name="emit")
    def emit(self, request: Emit) -> Emitted:
        """Writes each event of the batch whose stored id, its source and id, is not stored yet,
        whatever its time, and the status move with them, or writes nothing when the move lost."""
        if strays := {e.jobid for e in request.events} - {request.job_id}:
            raise ValueError(f"the batch of {request.job_id} holds events of {sorted(strays)}")
        try:
            # A lock held elsewhere fails the send, which the activity's retry sends again.
            with psycopg.connect(
                self.dsn,
                connect_timeout=CONNECT_TIMEOUT_S,
                options=f"-c lock_timeout={LOCK_TIMEOUT}",
            ) as conn:
                # The row lock orders the sends of one job, so the id check below sees every
                # committed batch.
                if not conn.execute(
                    "SELECT 1 FROM accepted_job WHERE job_id = %s FOR UPDATE", (request.job_id,)
                ).fetchone():
                    raise JobNotFound(request.job_id)
                if (lost := _move(conn, request)) is not None:
                    conn.rollback()
                    return Emitted(inserted=0, lost=lost)
                keys = {to_event_row(event)[0]: event for event in request.events}
                stored = {
                    row[0]
                    for row in conn.execute(
                        "SELECT event_id FROM event WHERE event_id = ANY(%s)", (list(keys),)
                    )
                }
                new = [event for key, event in keys.items() if key not in stored]
                inserted = insert_events(conn, new).inserted
        except IllegalTransition as error:
            raise control_error("IllegalTransition", str(error)) from error
        except JobNotFound as error:
            raise control_error("JobNotFound", f"no row for {error}") from error
        return Emitted(inserted=inserted, lost=None)
