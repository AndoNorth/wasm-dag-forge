"""Builds an event from the facts of what happened, so that a rebuild is byte-equal."""

from datetime import datetime
from typing import Any

from wdf_keys import attempt_identity, cloudevent_id

from .codec import format_time
from .models import ATTEMPT_TYPES, DATA_MODELS, Event


def build_event(
    name: str,
    *,
    source: str,
    tenant: str,
    jobid: str,
    priorityclass: str,
    jobclass: str,
    traceparent: str,
    time: datetime,
    data: dict[str, Any],
    taskid: str | None = None,
    attempt: int | None = None,
    resume_counter: int = 0,
    sequence: int = 0,
    tracestate: str | None = None,
) -> Event:
    """An event of type `dev.wdf.<name>.v1`.

    `time` is the time of the fact and is never read from a clock here.
    The id derives from the job, the task, the attempt identity, the type and the sequence, so the
    same facts give the same id.
    A checkpoint's sequence is the one in its data.
    """
    if (name, "1.0") not in DATA_MODELS:
        raise ValueError(f"unknown event type {name}")
    task = name.startswith("task.")
    if task != (taskid is not None):
        raise ValueError(f"{name} {'needs' if task else 'takes no'} a taskid")
    belongs_to_attempt = name in ATTEMPT_TYPES
    if belongs_to_attempt != (attempt is not None):
        raise ValueError(f"{name} {'needs' if belongs_to_attempt else 'takes no'} an attempt")
    if name == "task.checkpoint.committed":
        sequence = data["sequence"]
    identity = attempt_identity(attempt, resume_counter) if belongs_to_attempt else ""
    return Event.model_validate(
        {
            "specversion": "1.0",
            "id": cloudevent_id(jobid, taskid or "", identity, name, sequence),
            "source": source,
            "type": f"dev.wdf.{name}.v1",
            "time": format_time(time),
            "datacontenttype": "application/json",
            "dataschema": f"urn:wdf:schema:{name}:1.0",
            "subject": f"{jobid}/{taskid}" if task else jobid,
            "tenant": tenant,
            "jobid": jobid,
            "taskid": taskid,
            "attempt": attempt,
            "priorityclass": priorityclass,
            "jobclass": jobclass,
            "traceparent": traceparent,
            "tracestate": tracestate,
            "data": data,
        }
    )
