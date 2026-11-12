"""The aggregate index of a job: the recorded results of an aggregate task's inputs, in task id
order, which the `build_index` control activity puts and commits."""

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from wdf_artifactclient.models import IndexEntry
    from wdf_events.models import ContentReference

    from .ready import DONE
    from .testing.control import BuildIndex


def entry(job, task_id: str, output: str) -> IndexEntry:
    """The index entry of one input: its recorded result, or the input's failure when it did not
    run to completion, which a failed task names and any other unfinished task leaves missing."""
    state = job.states[task_id]
    if state in DONE:
        return IndexEntry(task=task_id, status="succeeded", reference=job.outputs[task_id][output])
    return IndexEntry(task=task_id, status="failed" if state == "failed" else "missing")


def index_request(job, task) -> tuple[str, BuildIndex, type]:
    """The `build_index` call of an aggregate task, whose every input succeeded, or with
    `allow_partial` whose inputs ended and at least one succeeded."""
    entries = [
        entry(job, ref.task, ref.output) for ref in sorted(task.inputs, key=lambda r: r.task)
    ]
    request = BuildIndex(
        job_id=job.job_id,
        tenant=job.identity.tenant,
        task=task.id,
        partial=task.allow_partial,
        entries=entries,
    )
    return "build_index", request, ContentReference
