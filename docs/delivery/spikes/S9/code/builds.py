"""Three builds of one pinned workflow type, as three deployment builds would ship it.

A is the build with the bug, B fixes the step after the signal behind `workflow.patched`,
and C makes an unguarded change before the signal, so a history started on A or B does not
replay on C.
Each build records the deployment version the SDK reports at every step, and the pool activity
records the build that called it.
"""

from collections import Counter
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import VersioningBehavior

POOL_QUEUE = "wdf-pool-simulate"
served: Counter[str] = Counter()


@activity.defn
async def step(build: str, label: str) -> str:
    served[build] += 1
    return label


def _version() -> str:
    version = workflow.info().get_current_deployment_version()
    return version.to_canonical_string() if version else "none"


async def _step(build: str, label: str) -> str:
    return await workflow.execute_activity(
        step,
        args=[build, label],
        task_queue=POOL_QUEUE,
        start_to_close_timeout=timedelta(seconds=30),
    )


class _Job:
    build = "?"

    def __init__(self) -> None:
        self.finish = False
        self.versions: list[str] = []

    @workflow.signal
    def go(self) -> None:
        self.finish = True

    async def before(self) -> str:
        return await _step(self.build, "prepare")

    async def after(self) -> str:
        return await _step(self.build, "finish-buggy")

    async def body(self) -> dict:
        self.versions.append(_version())
        steps = [await self.before()]
        self.versions.append(_version())
        await workflow.wait_condition(lambda: self.finish)
        self.versions.append(_version())
        steps.append(await self.after())
        self.versions.append(_version())
        return {"code": self.build, "steps": steps, "versions": self.versions}


@workflow.defn(name="JobWorkflow", versioning_behavior=VersioningBehavior.PINNED)
class JobA(_Job):
    build = "A"

    @workflow.run
    async def run(self) -> dict:
        return await self.body()


@workflow.defn(name="JobWorkflow", versioning_behavior=VersioningBehavior.PINNED)
class JobB(_Job):
    build = "B"

    async def after(self) -> str:
        if workflow.patched("fix-finish"):
            return await _step(self.build, "finish-fixed")
        return await super().after()

    @workflow.run
    async def run(self) -> dict:
        return await self.body()


@workflow.defn(name="JobWorkflow", versioning_behavior=VersioningBehavior.PINNED)
class JobC(_Job):
    build = "C"

    async def before(self) -> str:
        await workflow.sleep(0.1)
        return await super().before()

    @workflow.run
    async def run(self) -> dict:
        return await self.body()


BUILDS = {"A": JobA, "B": JobB, "C": JobC}
