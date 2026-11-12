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

RUN_TIMEOUTS_S = (600, 3600)
# Far above any slot a pool of the cluster can hold.
HUGE_MEMORY_MIB = 1 << 30
HUGE_CPU_MILLI = 1 << 30


def _survives(answer) -> bool:
    """Whether the answer reads back equal from the workflow history."""
    converter = event_data_converter.payload_converter
    try:
        [payload] = converter.to_payloads([PlaceResult(answer)])
        return converter.from_payloads([payload], [PlaceResult])[0].root == answer
    except ValueError:
        return False


def _mib(memory_class: str) -> int:
    return int(memory_class.removesuffix("Mi"))


def _check_placement(answer, request: PlacementRequest, pool: str, escalate: bool) -> list[str]:
    where = f"{request.task_type} {request.memory_mib} MiB (escalate={escalate})"
    if not isinstance(answer, Placement):
        return [f"{where}: a task that fits its slot got {answer!r}"]
    problems = []
    if answer.queue != naming().pools[pool]:
        problems.append(
            f"{where}: queue {answer.queue} is not {naming().pools[pool]} of naming.yaml"
        )
    if answer.escalated and not escalate:
        problems.append(f"{where}: escalated without being asked")
    if _mib(answer.memory_class) < request.memory_mib:
        problems.append(f"{where}: memory class {answer.memory_class} is below the request")
    if answer.schedule_to_start_s > pools().schedule_to_start_ceiling_s:
        problems.append(
            f"{where}: schedule-to-start {answer.schedule_to_start_s} is above the ceiling"
        )
    if answer.start_to_close_s <= request.run_timeout_s:
        problems.append(
            f"{where}: start-to-close {answer.start_to_close_s} leaves no margin over the "
            f"run timeout {request.run_timeout_s}"
        )
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

    A task up to the slot of its pool in pools.yaml lands on the queue of its registry pool in
    naming.yaml, in a memory class that holds it, with a start-to-close timeout above its run
    timeout. An escalation is flagged exactly when it offers a larger class. A task that fits no
    slot, or names no pool, gets the matching placement class, and every answer survives the
    history payload converter.
    """
    problems = []
    answers = []
    for task_type, pool in task_pools().items():
        slot = pools().pools[pool]
        for run_timeout_s in RUN_TIMEOUTS_S:
            for cpu_milli, memory_mib in ((1, 1), (slot.cpu_milli, slot.memory_mib)):
                fits = PlacementRequest(
                    task_type=task_type,
                    cpu_milli=cpu_milli,
                    memory_mib=memory_mib,
                    run_timeout_s=run_timeout_s,
                )
                plain = placer.place(fits)
                escalated = placer.place(fits, escalate=True)
                answers += [plain, escalated]
                problems += _check_placement(plain, fits, pool, escalate=False)
                problems += _check_placement(escalated, fits, pool, escalate=True)
                if not (isinstance(plain, Placement) and isinstance(escalated, Placement)):
                    continue
                grew = _mib(escalated.memory_class) > _mib(plain.memory_class)
                if escalated.escalated != grew:
                    problems.append(
                        f"{task_type}: escalated is {escalated.escalated} from "
                        f"{plain.memory_class} to {escalated.memory_class}"
                    )
        too_big = {
            "insufficient_memory": {"memory_mib": HUGE_MEMORY_MIB},
            "insufficient_cpu": {"cpu_milli": HUGE_CPU_MILLI},
        }
        for expected, change in too_big.items():
            request = PlacementRequest(
                task_type=task_type, cpu_milli=1, memory_mib=1, run_timeout_s=RUN_TIMEOUTS_S[0]
            ).model_copy(update=change)
            for escalate in (False, True):
                answer = placer.place(request, escalate=escalate)
                answers.append(answer)
                problems += _check_unschedulable(answer, expected, f"{task_type} {expected}")
    unknown = PlacementRequest(
        task_type="no-such-type", cpu_milli=1, memory_mib=1, run_timeout_s=RUN_TIMEOUTS_S[0]
    )
    answers.append(placer.place(unknown))
    problems += _check_unschedulable(answers[-1], "no_matching_pool", "an unknown type")
    problems += [
        f"{a!r} does not survive the payload converter" for a in answers if not _survives(a)
    ]
    assert not problems, "placer contract:\n" + "\n".join(problems)
