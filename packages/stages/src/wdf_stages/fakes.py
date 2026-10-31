"""One fake per stage, each doing only what its stage declares in contracts/stages/stages.yaml.

A stage is a callable `(inbound, facts) -> list[Event]`, where `inbound` is a `Recorded` event or
None for a stage that is started by a request and not by an event.
"""

from typing import Any

from wdf_events import Event, build_event
from wdf_events.models import ATTEMPT_TYPES
from wdf_keys import workflow_id

from .facts import SAMPLE_DATA, Facts
from .recording import Recorded

SOURCES = {
    name: f"/wdf/{name}"
    for name in ("dispatch", "control", "admission", "workflow", "placement", "worker")
}


def _identity(inbound: Recorded | None, facts: Facts) -> dict[str, str]:
    """The job identity, from the accepted event when the stage was started by one."""
    if inbound is not None and inbound.name == "job.accepted":
        return {
            "tenant": inbound.tenant,
            "jobid": inbound.jobid,
            "priorityclass": inbound.priorityclass,
            "jobclass": inbound.jobclass,
            "traceparent": inbound.traceparent,
        }
    return {
        "tenant": facts.tenant,
        "jobid": facts.jobid,
        "priorityclass": facts.priorityclass,
        "jobclass": facts.jobclass,
        "traceparent": facts.traceparent,
    }


def _build(name: str, source: str, who: dict[str, str], facts: Facts) -> Event:
    data = facts.data.get(name, SAMPLE_DATA[name])
    if name == "job.admitted":
        data = {"workflow_id": workflow_id(who["jobid"])}
    return build_event(
        name,
        source=source,
        time=facts.time,
        data=data,
        taskid=facts.taskid if name.startswith("task.") else None,
        attempt=facts.attempt if name in ATTEMPT_TYPES else None,
        resume_counter=facts.resume_counter,
        sequence=facts.sequence,
        **who,
    )


def _emit(stage: str, inbound: Recorded | None, facts: Facts) -> list[Event]:
    who = _identity(inbound, facts)
    return [_build(name, SOURCES[stage], who, facts) for name in facts.emit]


def dispatch(inbound: Recorded | None, facts: Facts) -> list[Event]:
    return _emit("dispatch", inbound, facts)


def control(inbound: Recorded | None, facts: Facts) -> list[Event]:
    return _emit("control", inbound, facts)


def admission(inbound: Recorded | None, facts: Facts) -> list[Event]:
    """Reads the accepted event it admits, or rejects."""
    assert inbound is not None and inbound.name == "job.accepted"
    inbound.data["manifest"], inbound.data["batch_id"], inbound.data["submission_key"]
    return _emit("admission", inbound, facts)


def workflow(inbound: Recorded | None, facts: Facts) -> list[Event]:
    """Reads the accepted event on start, and takes everything else from its own state."""
    if inbound is not None:
        inbound.data["manifest"], inbound.data["batch_id"], inbound.data["submission_key"]
    return _emit("workflow", inbound, facts)


def build_task_request(facts: Facts, **data: Any) -> Event:
    """The task request the workflow hands to an activity, which no stage writes to the history."""
    who = _identity(None, facts)
    request = {**SAMPLE_DATA["task.requested"], **data}
    return build_event(
        "task.requested",
        source=SOURCES["workflow"],
        time=facts.time,
        data=request,
        taskid=facts.taskid,
        **who,
    )


def choose_queue(task_type: str, cpu_cores: float, memory_mib: int) -> str:
    size = "small" if cpu_cores <= 1 and memory_mib <= 1024 else "large"
    return f"wdf.pool.{task_type}.{size}"


def placement(inbound: Recorded | None, facts: Facts) -> list[Event]:
    """Picks a pool queue from the task request and writes no event."""
    assert inbound is not None and inbound.name == "task.requested"
    choose_queue(inbound.data["task_type"], inbound.data["cpu_cores"], inbound.data["memory_mib"])
    return []


def worker(inbound: Recorded | None, facts: Facts) -> list[Event]:
    """Reads the task request and writes references, never bytes, into the heartbeat details."""
    assert inbound is not None and inbound.name == "task.requested"
    checkpoint = inbound.data.get("resume_checkpoint")
    facts.heartbeat.append(
        {
            "request": inbound.id,
            "tenant": inbound.tenant,
            "job": inbound.jobid,
            "task": inbound.taskid,
            "priorityclass": inbound.priorityclass,
            "jobclass": inbound.jobclass,
            "traceparent": inbound.traceparent,
            "task_type": inbound.data["task_type"],
            "deadline": inbound.data["deadline"],
            "references": [*inbound.data["inputs"], *([checkpoint] if checkpoint else [])],
        }
    )
    return []


FAKES = {
    "dispatch": dispatch,
    "control": control,
    "admission": admission,
    "workflow": workflow,
    "placement": placement,
    "worker": worker,
}
