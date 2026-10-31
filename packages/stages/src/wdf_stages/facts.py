"""What a stage knows besides the message it reads, and a valid sample of every event's data."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

JOB = "3a9617fa-df7c-5760-a644-42d559e0f433"
BATCH = "7d1f0c52-3b0e-5a39-8d0e-0a4f0c9e6b11"
TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
EPOCH = datetime(2026, 10, 31, 12, 0, 0, 250000, tzinfo=UTC)
SHA = "a" * 64


@dataclass
class Facts:
    """The identity, clock and workflow counters of one stage invocation.

    `emit` names the event types the stage writes in this invocation, and `data` overrides the
    sample data of a type.
    `heartbeat` is where a worker writes its heartbeat details.
    """

    tenant: str = "acme"
    jobid: str = JOB
    priorityclass: str = "standard"
    jobclass: str = "simulate"
    traceparent: str = TRACEPARENT
    time: datetime = EPOCH
    taskid: str | None = None
    attempt: int | None = None
    resume_counter: int = 0
    sequence: int = 0
    emit: tuple[str, ...] = ()
    data: dict[str, dict[str, Any]] = field(default_factory=dict)
    heartbeat: list[dict[str, Any]] = field(default_factory=list)


def _producer(task: str = "sim", attempt: str = "r0a1") -> dict[str, str]:
    return {"job": JOB, "task": task, "attempt": attempt}


def _reference(kind: str, bucket: str, **extra: Any) -> dict[str, Any]:
    return {
        "uri": f"s3://wdf-{bucket}/acme/b/j/7/sim/r0a1/out.bin",
        "sha256": SHA,
        "size": 10,
        "name": "out.bin",
        "kind": kind,
        **extra,
    }


CHECKPOINT = _reference("checkpoint", "checkpoints", producer=_producer())
REQUEST = {"reason": "operator"}

SAMPLE_DATA: dict[str, dict[str, Any]] = {
    "job.accepted": {
        "manifest": {
            "uri": f"s3://wdf-sources/sha256/aa/{SHA}",
            "sha256": SHA,
            "size": 10,
            "name": "manifest.json",
            "kind": "manifest",
        },
        "batch_id": BATCH,
        "submission_key": "sub-1",
    },
    "job.published": {},
    "job.admitted": {"workflow_id": f"job-{JOB}"},
    "job.started": {},
    "job.paused": {},
    "job.pause.requested": REQUEST,
    "job.resume.requested": REQUEST,
    "job.resumed": {},
    "job.cancel.requested": REQUEST,
    "job.terminate.requested": REQUEST,
    "job.succeeded": {},
    "job.partial": {"skipped_tasks": ["agg"]},
    "job.failed": {"reason": "task failed"},
    "job.cancelled": {},
    "job.terminated": {},
    "job.dead.lettered": {"dlq_reason": "MALFORMED", "detail": "bad data"},
    "task.placed": {"queue": "wdf.pool.small"},
    "task.started": {},
    "task.checkpoint.committed": {"sequence": 0, "checkpoint": CHECKPOINT},
    "task.attempt.failed": {"reason": "worker lost"},
    "task.retries.exhausted": {"attempts": 3, "reason": "worker lost"},
    "task.succeeded": {},
    "task.failed": {"reason": "no retries left"},
    "task.skipped": {"reason": "compile-cache-hit", "cache_key": SHA},
    "task.requested": {
        "task_type": "simulate",
        "cpu_cores": 1.0,
        "memory_mib": 512,
        "inputs": [_reference("component", "components")],
        "deadline": "2026-11-01T12:00:00.000000Z",
    },
    "task.result.ready": {
        "result": _reference("result", "results", producer=_producer()),
    },
}
