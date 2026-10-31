"""The worker-side check of a task request."""

from pydantic import BaseModel

from .codec import from_bytes
from .errors import SchemaInvalid
from .models import TaskRequestData

TYPE = "dev.wdf.task.requested.v1"


class TaskRequest(BaseModel):
    """What a worker needs from a task request, with the data typed."""

    id: str
    tenant: str
    jobid: str
    taskid: str
    priorityclass: str
    jobclass: str
    traceparent: str
    data: TaskRequestData


def check_task_request(data: bytes) -> TaskRequest:
    """Reads the bytes of a task request event and returns the typed request.

    Raises UnsupportedMajor, SchemaInvalid or EventTooLarge.
    A field added by a newer minor was accepted by the reader rule and is dropped here.
    """
    event = from_bytes(data)
    if event.type != TYPE:
        raise SchemaInvalid("wrong-type", f"expected {TYPE}, got {event.type}")
    known = {k: v for k, v in event.data.items() if k in TaskRequestData.model_fields}
    return TaskRequest(
        id=event.id,
        tenant=event.tenant,
        jobid=event.jobid,
        taskid=event.taskid,
        priorityclass=event.priorityclass,
        jobclass=event.jobclass,
        traceparent=event.traceparent,
        data=TaskRequestData.model_validate(known),
    )
