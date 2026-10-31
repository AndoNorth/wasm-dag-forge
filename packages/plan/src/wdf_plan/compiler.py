"""Compile a validated document to a plan: defaults resolved, edges derived, tasks ordered."""

import heapq

from wdf_jobdoc import Document
from wdf_jobdoc.models import CompileTask
from wdf_jobdoc.plan_models import PLAN_VERSION, Plan
from wdf_jobdoc.registry import TASK_TYPES


def compile_plan(document: Document, profile) -> Plan:
    """The plan of `document` under `profile`.

    A plan is a function of the document and the profile only: per-job values never enter, so the
    plan keeps its placeholders and is the template of a batch.
    The document is assumed valid, and a graph that is not is refused with ValueError.
    """
    edges = _edges(document)
    tasks = [_plan_task(tid, document.tasks[tid], edges[tid], profile) for tid in _order(edges)]
    return Plan.model_validate(
        {
            "plan_version": PLAN_VERSION,
            "profile": profile.name,
            "max_parallel": document.max_parallel or profile.default_max_parallel,
            "parameters": dict(document.parameters),
            "tasks": tasks,
        }
    )


def _edges(document: Document) -> dict[str, list[str]]:
    """Control dependencies and the data edge of every input, as one sorted union per task."""
    edges = {}
    for tid, task in document.tasks.items():
        needs = set(task.depends_on) | {ref.task for ref in task.inputs}
        unknown = needs - document.tasks.keys()
        if unknown:
            raise ValueError(f"task {tid} needs {sorted(unknown)}, which the document lacks")
        edges[tid] = sorted(needs)
    return edges


def _order(edges: dict[str, list[str]]) -> list[str]:
    """Topological order, the smallest task id first among the tasks that are ready."""
    waiting = {tid: len(needs) for tid, needs in edges.items()}
    followers: dict[str, list[str]] = {tid: [] for tid in edges}
    for tid, needs in edges.items():
        for need in needs:
            followers[need].append(tid)
    ready = [tid for tid, count in waiting.items() if count == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        tid = heapq.heappop(ready)
        order.append(tid)
        for follower in followers[tid]:
            waiting[follower] -= 1
            if waiting[follower] == 0:
                heapq.heappush(ready, follower)
    if len(order) != len(edges):
        raise ValueError("the dependencies and data edges contain a cycle")
    return order


def _plan_task(tid: str, task, needs: list[str], profile) -> dict:
    common = {
        "id": tid,
        "type": task.type,
        "pool": TASK_TYPES[task.type]["pool"],
        "depends_on": needs,
        "inputs": sorted(
            ({"task": ref.task, "output": ref.output} for ref in task.inputs),
            key=lambda ref: (ref["task"], ref["output"]),
        ),
        "checkpoint": getattr(task, "checkpoint", False),
        "allow_partial": getattr(task, "allow_partial", False),
        "params": task.params.model_dump(mode="json", exclude_none=True),
    }
    if isinstance(task, CompileTask):
        slot = profile.pools.compile
        return common | {
            "cpu_milli": slot.cpu_milli,
            "memory_mib": slot.memory_mib,
            "run_timeout_s": profile.compile_run_timeout_s,
            "retries": profile.compile_retries,
        }
    return common | {
        "cpu_milli": task.cpu_milli,
        "memory_mib": task.memory_mib,
        "run_timeout_s": task.run_timeout_s,
        "retries": task.retries,
    }


def task_request_fields(task) -> dict:
    """The fields of a task request that a plan task decides: type, cpu cores and memory.

    The workflow adds the deadline, the input references and a resume checkpoint.
    """
    return {
        "task_type": task.type,
        "cpu_cores": task.cpu_milli / 1000,
        "memory_mib": task.memory_mib,
    }
