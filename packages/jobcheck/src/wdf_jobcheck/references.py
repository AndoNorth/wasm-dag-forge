"""The reference pass: inputs against the registry, the aggregate-only form and the placeholders.

Everything here reads the document and the registry only, so no resolver is needed.
"""

from wdf_jobdoc import Document, Issue, Placeholder
from wdf_jobdoc.issues import pointer
from wdf_jobdoc.registry import AGGREGATIONS, TASK_TYPES

__all__ = ["check_references"]


def check_references(document: Document) -> tuple[Issue, ...]:
    issues: list[Issue] = []
    for task_id, task in document.tasks.items():
        issues += _inputs(document, task_id, task)
        if task.type == "aggregate":
            issues += _aggregate_form(document, task_id, task)
        issues += _placeholders(document, task_id, task)
    return tuple(issues)


def _inputs(document: Document, task_id: str, task) -> list[Issue]:
    rule = TASK_TYPES[task.type]["inputs"]
    issues = []
    seen = set()
    accepted = 0
    for index, ref in enumerate(task.inputs):
        path = pointer(["tasks", task_id, "inputs", index])
        producer = document.tasks.get(ref.task)
        if producer is None:
            issues.append(
                Issue("unknown-producer", f"{path}/task", f"{ref.task} is not a task of the job")
            )
            continue
        kind = TASK_TYPES[producer.type]["outputs"].get(ref.output)
        if kind is None:
            issues.append(
                Issue(
                    "undeclared-output",
                    f"{path}/output",
                    f"the {producer.type} task declares no output {ref.output}",
                )
            )
            continue
        if (ref.task, ref.output) in seen:
            issues.append(
                Issue("duplicate-input", path, f"{ref.task}/{ref.output} is listed twice")
            )
        seen.add((ref.task, ref.output))
        if kind not in rule["accepts"]:
            accepts = ", ".join(rule["accepts"]) or "no input"
            issues.append(
                Issue(
                    "input-kind-not-accepted",
                    path,
                    f"{task.type} accepts {accepts}, and {ref.task}/{ref.output} is {kind}",
                )
            )
        else:
            accepted += 1
    # A bad input is already reported, so a task that has one is not also reported as short of one.
    if accepted < rule["min"] and not issues:
        issues.append(
            Issue(
                "missing-required-input",
                pointer(["tasks", task_id]),
                f"the {task.type} task needs {rule['min']} input of {', '.join(rule['accepts'])}",
            )
        )
    if rule["max"] is not None and len(task.inputs) > rule["max"]:
        issues.append(
            Issue(
                "too-many-inputs",
                pointer(["tasks", task_id, "inputs", rule["max"]]),
                f"{task.type} takes at most {rule['max']} input(s), has {len(task.inputs)}",
            )
        )
    return issues


def _aggregate_form(document: Document, task_id: str, task) -> list[Issue]:
    """An aggregate reads results of its job or one index object, never both, and alone."""
    issues = []
    index_path = pointer(["tasks", task_id, "params", "index"])
    if task.params.index is not None:
        if task.inputs:
            issues.append(
                Issue(
                    "index-and-inputs", index_path, "an aggregate has inputs or an index, not both"
                )
            )
        if len(document.tasks) > 1:
            issues.append(
                Issue(
                    "index-job-not-alone", index_path, "an index job holds the aggregate task alone"
                )
            )
    elif not task.inputs:
        issues.append(
            Issue(
                "aggregate-without-input",
                pointer(["tasks", task_id]),
                "an aggregate has an input or an index reference",
            )
        )
    return issues


def _placeholders(document: Document, task_id: str, task) -> list[Issue]:
    """A placeholder names a declared parameter whose type is the type of its position."""
    issues = []
    base = ["tasks", task_id, "params"]
    stands = []
    if task.type == "simulate":
        stands.append((base + ["seed"], task.params.seed, "seed"))
        stands += [(base + ["args", k], v, None) for k, v in task.params.args.items()]
    elif task.type == "aggregate":
        wants = AGGREGATIONS[task.params.aggregation]["args"]
        for name, spec in wants.items():
            stands.append((base + ["args", name], getattr(task.params.args, name), spec["type"]))
    for tokens, value, expected in stands:
        if not isinstance(value, Placeholder):
            continue
        declared = document.parameters.get(value.param)
        if declared is None:
            issues.append(
                Issue(
                    "unknown-placeholder",
                    pointer(tokens),
                    f"{value.param} is not a declared parameter",
                )
            )
        elif expected is not None and declared != expected:
            issues.append(
                Issue(
                    "placeholder-type-mismatch",
                    pointer(tokens),
                    f"{value.param} is declared {declared} and this position takes {expected}",
                )
            )
    return issues
