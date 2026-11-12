"""The aggregate index of a job: the recorded results of an aggregate task's inputs, in task id
order, which the `build_index` control activity puts and commits."""

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from wdf_artifactclient.models import IndexEntry
    from wdf_events.models import ContentReference

    from .testing.control import BuildIndex


def index_request(job, task) -> tuple[str, BuildIndex, type]:
    """The `build_index` call of an aggregate task, whose every input succeeded."""
    entries = [
        IndexEntry(task=ref.task, status="succeeded", reference=job.outputs[ref.task][ref.output])
        for ref in sorted(task.inputs, key=lambda ref: ref.task)
    ]
    request = BuildIndex(
        job_id=job.job_id, tenant=job.identity.tenant, task=task.id, partial=False, entries=entries
    )
    return "build_index", request, ContentReference
