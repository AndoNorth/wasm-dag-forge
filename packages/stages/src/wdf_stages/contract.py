"""The contract runner: runs a stage callable against its declaration in stages.yaml."""

from collections.abc import Callable

import yaml
from wdf_events import Event, build_event, from_bytes, to_bytes

from .check import TABLE
from .facts import SAMPLE_DATA, Facts
from .fakes import build_task_request
from .recording import Recorded, type_name

Stage = Callable[[Recorded | None, Facts], list[Event]]
Case = tuple[Event | None, Facts]


class ContractViolation(AssertionError):
    pass


def declaration(stage_name: str) -> dict:
    return yaml.safe_load(TABLE.read_text())["stages"][stage_name]


def sample_event(name: str, facts: Facts) -> Event:
    """A valid event of a type, as the stage that reads it would receive it."""
    if name == "task.requested":
        return build_task_request(Facts(taskid="sim"))
    return build_event(
        name,
        source="/wdf/dispatch",
        tenant=facts.tenant,
        jobid=facts.jobid,
        priorityclass=facts.priorityclass,
        jobclass=facts.jobclass,
        traceparent=facts.traceparent,
        time=facts.time,
        data=SAMPLE_DATA[name],
    )


def default_cases(stage_name: str) -> list[Case]:
    """One case per type the stage declares to emit, and one per type it reads."""
    spec = declaration(stage_name)
    base = Facts(taskid="sim", attempt=1)
    inbound_types = list(spec["reads"]["data"]) or [None]
    inbound = [sample_event(n, base) if n else None for n in inbound_types]
    cases: list[Case] = [(event, Facts(taskid="sim", attempt=1)) for event in inbound]
    for emitted in spec["emits"]:
        for event in inbound:
            cases.append((event, Facts(taskid="sim", attempt=1, emit=(emitted["type"],))))
    return cases


def invoke(stage_name: str, stage: Stage, inbound: Event | None, facts: Facts) -> list[Event]:
    """Calls the stage once, and raises ContractViolation for anything it does beyond its table."""
    spec = declaration(stage_name)
    recorded = Recorded(inbound) if inbound is not None else None
    emitted = stage(recorded, facts)
    if recorded is not None:
        undeclared = recorded.attribute_reads - set(spec["reads"]["attributes"])
        if undeclared:
            raise ContractViolation(
                f"{stage_name} reads undeclared attributes {sorted(undeclared)}"
            )
        declared = spec["reads"]["data"].get(recorded.name, [])
        fields = {f for n, f in recorded.data_reads if n == recorded.name}
        if fields - set(declared):
            raise ContractViolation(
                f"{stage_name} reads undeclared data fields {sorted(fields - set(declared))} "
                f"of {recorded.name}"
            )
    allowed = {(e["type"], e["source"]) for e in spec["emits"]}
    for event in emitted:
        if (type_name(event.type), event.source) not in allowed:
            raise ContractViolation(
                f"{stage_name} emitted {type_name(event.type)} from {event.source}, "
                f"which its table does not declare"
            )
        if from_bytes(to_bytes(event)) != event:
            raise ContractViolation(f"{stage_name} emitted an event that does not round trip")
    return emitted


def run_stage_contract(stage_name: str, stage_callable: Stage, cases: list[Case] | None = None):
    """Runs the stage over `cases` (by default a sample per declared read and emit).

    Raises ContractViolation naming the stage and the read or event that the table does not declare.
    """
    for inbound, facts in cases if cases is not None else default_cases(stage_name):
        invoke(stage_name, stage_callable, inbound, facts)
