"""The conformance test of a placer: what the workflow relies on in every answer of `place`."""

from wdf_temporal import event_data_converter

from ..contract import failure_classes, naming, pools
from ..placement import (
    Placement,
    PlacementRequest,
    Placer,
    PlaceResult,
    Unschedulable,
    task_pools,
)

RUN_TIMEOUT_S = 600
# Far above any slot a pool of the cluster can hold.
HUGE_MEMORY_MIB = 1 << 30
HUGE_CPU_MILLI = 1 << 30


def _round_trip(answer):
    """The answer as the workflow reads it back from its history."""
    converter = event_data_converter.payload_converter
    [payload] = converter.to_payloads([PlaceResult(answer)])
    return converter.from_payloads([payload], [PlaceResult])[0].root


def _check_placement(answer, task_type: str, pool: str, escalate: bool) -> list[str]:
    where = f"{task_type} (escalate={escalate})"
    if not isinstance(answer, Placement):
        return [f"{where}: a task that fits its slot got {answer!r}"]
    problems = []
    if answer.queue != naming().pools[pool]:
        problems.append(
            f"{where}: queue {answer.queue} is not {naming().pools[pool]} of naming.yaml"
        )
    if answer.escalated and not escalate:
        problems.append(f"{where}: escalated without being asked")
    if answer.schedule_to_start_s > pools().schedule_to_start_ceiling_s:
        problems.append(
            f"{where}: schedule-to-start {answer.schedule_to_start_s} is above the ceiling"
        )
    if answer.start_to_close_s <= RUN_TIMEOUT_S:
        problems.append(f"{where}: start-to-close {answer.start_to_close_s} leaves no margin")
    if answer.heartbeat_s >= answer.start_to_close_s:
        problems.append(f"{where}: heartbeat {answer.heartbeat_s} is not below start-to-close")
    return problems


def _check_unschedulable(answer, expected: str, where: str) -> list[str]:
    if not isinstance(answer, Unschedulable) or answer.failure != expected:
        return [f"{where}: expected {expected}, got {answer!r}"]
    row = failure_classes().get(answer.failure)
    if row is None or row.source != "placement":
        return [f"{where}: {answer.failure} is not a placement row of failure-classes.yaml"]
    return []


def placer_contract(placer: Placer) -> None:
    """Raises AssertionError listing every rule the placer breaks.

    A task that fits its slot lands on the queue of its registry pool in naming.yaml, a task that
    fits no slot, or names no pool, gets the matching placement class, and every answer survives
    the history payload converter.
    """
    problems = []
    for task_type, pool in task_pools().items():
        fits = PlacementRequest(
            task_type=task_type, cpu_milli=1, memory_mib=1, run_timeout_s=RUN_TIMEOUT_S
        )
        plain = placer.place(fits)
        problems += _check_placement(plain, task_type, pool, escalate=False)
        escalated = placer.place(fits, escalate=True)
        problems += _check_placement(escalated, task_type, pool, escalate=True)
        if (
            isinstance(plain, Placement)
            and isinstance(escalated, Placement)
            and escalated.escalated == (escalated.memory_class == plain.memory_class)
        ):
            problems.append(f"{task_type}: escalated is set exactly when the memory class grows")
        for answer in (plain, escalated):
            if _round_trip(answer) != answer:
                problems.append(f"{task_type}: {answer!r} does not survive the payload converter")
        too_big = {
            "insufficient_memory": fits.model_copy(update={"memory_mib": HUGE_MEMORY_MIB}),
            "insufficient_cpu": fits.model_copy(update={"cpu_milli": HUGE_CPU_MILLI}),
        }
        for expected, request in too_big.items():
            for escalate in (False, True):
                answer = placer.place(request, escalate=escalate)
                problems += _check_unschedulable(answer, expected, f"{task_type} {expected}")
    unknown = PlacementRequest(
        task_type="no-such-type", cpu_milli=1, memory_mib=1, run_timeout_s=RUN_TIMEOUT_S
    )
    problems += _check_unschedulable(placer.place(unknown), "no_matching_pool", "an unknown type")
    assert not problems, "placer contract:\n" + "\n".join(problems)
