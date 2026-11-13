"""Builds of JobWorkflow outside the workflow package: one that changes nothing, which the recorded
histories replay on, and one that reorders two activities, which they must not replay on."""

from temporalio import workflow
from temporalio.common import VersioningBehavior
from wdf_events import Event
from wdf_events.models import JobParams
from wdf_workflow.contract import naming
from wdf_workflow.job import Identity, JobWorkflow


@workflow.defn(name=naming().workflow.type, versioning_behavior=VersioningBehavior.PINNED)
class Unchanged(JobWorkflow):
    """A build that only delegates, as Reordered does around its one change."""

    @workflow.run
    async def run(self, accepted: Event, params: JobParams) -> str:
        return await super().run(accepted, params)


@workflow.defn(name=naming().workflow.type, versioning_behavior=VersioningBehavior.PINNED)
class Reordered(JobWorkflow):
    """A build that moves the job row to `running` before it loads the plan, so its first two
    activities run in the other order."""

    @workflow.run
    async def run(self, accepted: Event, params: JobParams) -> str:
        return await super().run(accepted, params)

    async def job(self, accepted: Event, params: JobParams) -> str:
        self.identity = Identity.of(accepted, params.class_override)
        self.job_id = self.identity.jobid
        await self.emit(self.job_event("job.started", {}), move="running")
        return await super().job(accepted, params)
