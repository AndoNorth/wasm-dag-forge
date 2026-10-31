"""The dependency graph pass: unknown and self dependencies, and cycles, over every kind of edge.

An edge runs from a task to what it waits for: each `depends_on` entry and each input reference
is one.
Inputs that name no task are the reference pass's business and add no edge here.
"""

from collections import deque

from wdf_jobdoc import Document, Issue
from wdf_jobdoc.issues import pointer

__all__ = ["check_graph"]


def check_graph(document: Document) -> tuple[Issue, ...]:
    issues: list[Issue] = []
    edges: dict[str, list[str]] = {}
    for task_id, task in document.tasks.items():
        waits = {}
        for index, dep in enumerate(task.depends_on):
            path = pointer(["tasks", task_id, "depends_on", index])
            if dep not in document.tasks:
                issues.append(
                    Issue(
                        "unknown-dependency",
                        path,
                        f"{task_id} depends on {dep}, which is not a task",
                    )
                )
            else:
                waits.setdefault(dep, path)
        for index, ref in enumerate(task.inputs):
            if ref.task in document.tasks:
                waits.setdefault(ref.task, pointer(["tasks", task_id, "inputs", index, "task"]))
        for dep, path in waits.items():
            if dep == task_id:
                issues.append(Issue("self-dependency", path, f"{task_id} depends on itself"))
        edges[task_id] = sorted(dep for dep in waits if dep != task_id)
    for cycle in _cycles(edges):
        issues.append(
            Issue(
                "dependency-cycle",
                pointer(["tasks", cycle[0]]),
                "each task waits for the next: " + " -> ".join(cycle),
            )
        )
    return tuple(issues)


def _cycles(edges: dict[str, list[str]]) -> list[list[str]]:
    """One cycle per strongly connected component of two or more tasks, closed on its first task."""
    found = []
    for component in _components(edges):
        if len(component) > 1:
            found.append(_walk_back(min(component), edges, component))
    return sorted(found)


def _components(edges: dict[str, list[str]]):
    """Tarjan's strongly connected components, iterative so that a long chain cannot overflow."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    for root in sorted(edges):
        if root in index:
            continue
        index[root] = low[root] = len(index)
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(edges[root]))]
        while work:
            node, successors = work[-1]
            for nxt in successors:
                if nxt not in index:
                    index[nxt] = low[nxt] = len(index)
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(edges[nxt])))
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    component = set()
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.add(member)
                        if member == node:
                            break
                    yield component


def _walk_back(start: str, edges: dict[str, list[str]], component: set[str]) -> list[str]:
    """The shortest walk from `start` back to itself inside its component, ties by task id."""
    came_from: dict[str, str] = {}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for nxt in edges[node]:
            if nxt not in component:
                continue
            if nxt == start:
                walk = [node]
                while walk[-1] != start:
                    walk.append(came_from[walk[-1]])
                return [*reversed(walk), start]
            if nxt not in came_from:
                came_from[nxt] = node
                queue.append(nxt)
    raise AssertionError(
        "a component of two or more tasks always holds a cycle through each member"
    )
