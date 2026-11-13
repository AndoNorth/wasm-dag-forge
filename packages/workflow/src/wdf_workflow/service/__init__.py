"""The workflow worker service: one build's workflow worker, its control activity worker and the
reaper's Schedule worker in one process, on a client of one namespace.

`python -m wdf_workflow.service` reads its configuration from the environment only, the names of
ENV and REAPER_ENV, serves the SDK's Prometheus metrics, which carry the workflow's own metrics,
on the address of WDF_METRICS_ADDRESS, and drains on SIGTERM or SIGINT: it stops polling, gives
the workflow and control tasks in flight DRAIN to finish and report, and returns. The reaper worker
keeps the shutdown of wdf_artifactreaper.schedule, whose pass is retried by its own policy.
When READY_ENV names a file, the service creates it once Temporal answers for its namespace and
its workers have started, and removes it when it starts to drain, so a readiness probe can test
for it. A file left by a killed process is removed before the service connects.
"""

import asyncio
import logging
import os
import signal
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest
from temporalio.client import Client
from temporalio.common import WorkerDeploymentVersion
from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig
from temporalio.worker import Worker, WorkerDeploymentConfig
from wdf_artifactreaper import schedule
from wdf_artifactreaper.schedule import RunPass
from wdf_temporal import event_data_converter, tracing_interceptor

from ..contract import naming
from ..job import JobWorkflow
from ..placement import StaticPlacer, place_activity

log = logging.getLogger(__name__)

# How long the tasks in flight have to finish after a stop, below the pod's grace period.
DRAIN = timedelta(seconds=60)
# The threads of each worker's blocking activities, and so its database connections at once.
THREADS = 8
# The workflow tasks run at once. A task takes tens of milliseconds of CPU, so on the pod's half
# core more of them at once only queue inside the process until they pass their timeout.
WORKFLOW_TASKS = 8
# The workflows kept in memory between their tasks, up to half a MiB each, so the cache fits in
# the pod's 256 MiB however many jobs are open. A workflow out of it replays its history on its
# next task.
CACHED_WORKFLOWS = 150

ENV = {
    "temporal_address": "WDF_TEMPORAL_ADDRESS",
    "namespace": "WDF_TEMPORAL_NAMESPACE",
    "build_id": naming().workflow.build_id_env,
    "metrics_address": "WDF_METRICS_ADDRESS",
    # A login in wdf_workflow, for emit.
    "workflow_dsn": "WDF_WORKFLOW_DSN",
    # A login in wdf_artifacts, for the control activities that write the index.
    "artifacts_dsn": "WDF_ARTIFACTS_DSN",
    "store_endpoint": "WDF_STORE_ENDPOINT",
    # Credentials files of wdf_storecheck.s3.load_credentials holding the role of each name.
    "workflow_credentials": "WDF_WORKFLOW_CREDENTIALS",
    "indexer_credentials": "WDF_INDEXER_CREDENTIALS",
}
# Read by the reaper's own pass, with its login in wdf_reaper and the `reaper` store identity.
REAPER_ENV = (schedule.ENV_DSN, schedule.ENV_ENDPOINT, schedule.ENV_CREDENTIALS)
# Optional: the file that marks the service ready.
READY_ENV = "WDF_READY_FILE"


class MissingEnv(Exception):
    def __init__(self, names: Sequence[str]):
        super().__init__(f"missing environment: {', '.join(names)}")
        self.names = list(names)


@dataclass(frozen=True)
class Config:
    temporal_address: str
    namespace: str
    build_id: str
    metrics_address: str
    # The DSNs carry passwords, so a printed configuration leaves them out.
    workflow_dsn: str = field(repr=False)
    artifacts_dsn: str = field(repr=False)
    store_endpoint: str
    workflow_credentials: str
    indexer_credentials: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> "Config":
        """The configuration of `environ`, or MissingEnv naming every variable that is unset or
        empty, the reaper's included, before anything connects."""
        if missing := [name for name in [*ENV.values(), *REAPER_ENV] if not environ.get(name)]:
            raise MissingEnv(missing)
        return cls(**{key: environ[name] for key, name in ENV.items()})


def real_control(config: Config) -> tuple[list[Callable], Callable]:
    """The control activities of the control queue, and `emit`, over the database logins and the
    store identities of the configuration."""
    from wdf_artifactclient.client import ArtifactClient
    from wdf_storecheck.s3 import load_credentials

    from ..activities import Committer, CompileCache, Emitter, IndexBuilder, PlanLoader

    def store(path: str, role: str) -> ArtifactClient:
        return ArtifactClient(config.store_endpoint, load_credentials(path)[role])

    reader = store(config.workflow_credentials, "workflow")
    indexer = store(config.indexer_credentials, "indexer")
    cache = CompileCache(config.artifacts_dsn, reader)
    committer = Committer(config.artifacts_dsn, reader)
    return [
        PlanLoader(reader).load_plan,
        cache.cache_lookup,
        cache.cache_record,
        cache.cache_invalidate,
        committer.commit,
        committer.index_checkpoint,
        IndexBuilder(config.artifacts_dsn, reader, indexer).build_index,
    ], Emitter(config.workflow_dsn).emit


def control_queue(build_id: str) -> str:
    return naming().control_queue.replace("<build id>", build_id)


def prometheus(address: str) -> Runtime:
    return Runtime(telemetry=TelemetryConfig(metrics=PrometheusConfig(bind_address=address)))


async def serve(
    config: Config,
    control_activities: list[Callable],
    emit: Callable,
    run_pass: RunPass,
    stop: asyncio.Event | None = None,
    runtime: Runtime | None = None,
    ready_file: Path | None = None,
) -> None:
    """Runs the three workers until `stop` is set or the process gets SIGTERM or SIGINT, then
    drains them.

    The workflow reads its control queue from the environment's build id, so a `config.build_id`
    that differs from it, whose workflows would wait on a queue nobody polls, is refused. A
    namespace the server does not know fails before `ready_file` is created. A fatal error of any
    worker drains the others and is raised.
    """
    if (env_build := os.environ.get(naming().workflow.build_id_env)) != config.build_id:
        raise ValueError(f"build id {config.build_id} is not the environment's {env_build}")
    if ready_file:
        ready_file.unlink(missing_ok=True)
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    try:
        client = await Client.connect(
            config.temporal_address,
            namespace=config.namespace,
            data_converter=event_data_converter,
            interceptors=[tracing_interceptor()],
            runtime=runtime or prometheus(config.metrics_address),
        )
        await client.workflow_service.describe_namespace(
            DescribeNamespaceRequest(namespace=config.namespace)
        )
        with (
            ThreadPoolExecutor(THREADS) as local,
            ThreadPoolExecutor(THREADS) as control,
            ThreadPoolExecutor(1) as reaper,
        ):
            workers = [
                Worker(
                    client,
                    task_queue=naming().workflow.queue,
                    workflows=[JobWorkflow],
                    activities=[place_activity(StaticPlacer()), emit],
                    activity_executor=local,
                    max_cached_workflows=CACHED_WORKFLOWS,
                    max_concurrent_workflow_tasks=WORKFLOW_TASKS,
                    max_concurrent_local_activities=THREADS,
                    graceful_shutdown_timeout=DRAIN,
                    deployment_config=WorkerDeploymentConfig(
                        version=WorkerDeploymentVersion(
                            naming().workflow.deployment, config.build_id
                        ),
                        use_worker_versioning=True,
                    ),
                ),
                Worker(
                    client,
                    task_queue=control_queue(config.build_id),
                    activities=control_activities,
                    activity_executor=control,
                    max_concurrent_activities=THREADS,
                    graceful_shutdown_timeout=DRAIN,
                ),
                schedule.build_worker(client, run_pass, reaper),
            ]
            log.info(
                "polling in namespace %s: %s as build %s of %s, %s and %s",
                config.namespace,
                naming().workflow.queue,
                config.build_id,
                naming().workflow.deployment,
                control_queue(config.build_id),
                schedule.TASK_QUEUE,
            )
            await _run_until(workers, stop, ready_file)
    finally:
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(signum)


async def _run_until(workers: list[Worker], stop: asyncio.Event, ready_file: Path | None) -> None:
    runs = [asyncio.create_task(worker.run()) for worker in workers]
    stopped = asyncio.create_task(stop.wait())
    if ready_file:
        ready_file.touch()
    try:
        await asyncio.wait([*runs, stopped], return_when=asyncio.FIRST_COMPLETED)
    finally:
        if ready_file:
            ready_file.unlink(missing_ok=True)
    stopped.cancel()
    log.info("draining for at most %s", DRAIN)
    await asyncio.gather(*(worker.shutdown() for worker in workers))
    await asyncio.gather(*runs)
    log.info("drained")
