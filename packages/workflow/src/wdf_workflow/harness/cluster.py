"""T2: the harness against the cluster of a kube context, through its own port-forwards bound to
127.0.0.1, with the credentials of the state directory.

The deployed workflow worker runs the real control activities, so the harness seeds what they read
as the dispatch API would: each job's row, claimed from `accepted` to `admitted`, and its manifest
and source in the store under the `submit` identity, indexed and linked to the job. The FakePool
of each pool holds the store identity of that pool, as the pool workers will.
"""

import asyncio
import json
import os
import socket
import subprocess
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import psycopg
from temporalio.client import Client, WorkflowHandle
from temporalio.service import RPCError
from wdf_artifactclient.client import ArtifactClient
from wdf_jobstate import JobRow, insert_accepted, request_action, transition
from wdf_storecheck.s3 import load_credentials

from .. import versioning
from ..contract import naming
from ..models import ControlSignal, ResumeSignal, StatusQuery, TaskInput
from . import (
    DEADLINE_S,
    PARAMS,
    WORKFLOW_WORKER,
    Job,
    Pool,
    Refused,
    Scripts,
    _until,
    search_attributes,
)

INFRA = "wdf-infra"
CONTROL = versioning.KUBE_NAMESPACE
# Each forward: the namespace, the resource and the port behind it.
FORWARDS = {
    "temporal": (INFRA, "svc/temporal-frontend", 7233),
    "appdb": (INFRA, "pod/appdb-0", 5432),
    "objectstore": (INFRA, "svc/objectstore", 8333),
}
WORKER_LABEL = f"app.kubernetes.io/name={WORKFLOW_WORKER}"
REGISTRY = "localhost:5001"
IMAGE = f"wdf-{WORKFLOW_WORKER}"
# Run from the root of the tree whose image a rollout builds.
BUILD = Path("deploy/workflow/image.sh")
# How long a restarted pod, a forward or a new build has to come up.
UP_S = 300.0


def state_dir() -> Path:
    state = os.environ.get("WDF_STATE_DIR") or str(
        Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "wasm-dag-forge"
    )
    return Path(state)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


class Forward:
    """A `kubectl port-forward` on a fixed local port, so the clients that use it reconnect to the
    same address once it is opened again after its pod restarted."""

    def __init__(self, context: str, namespace: str, resource: str, remote: int):
        self.argv = [
            "kubectl",
            f"--context={context}",
            f"--namespace={namespace}",
            "port-forward",
            "--address=127.0.0.1",
            resource,
        ]
        self.remote = remote
        self.port = free_port()
        self.process: subprocess.Popen | None = None

    def open(self) -> None:
        self.close()
        self.process = subprocess.Popen(
            [*self.argv, f"{self.port}:{self.remote}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + UP_S
        while not _listening(self.port):
            if self.process.poll() is not None:
                # The resource is not up yet, as right after its pod restarted.
                self.process = subprocess.Popen(
                    [*self.argv, f"{self.port}:{self.remote}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            if time.monotonic() > deadline:
                raise TimeoutError(f"{' '.join(self.argv)} did not listen within {UP_S:.0f} s")
            time.sleep(0.5)

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def close(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            self.process.wait()


def kubectl(context: str, *args: str, capture: bool = True) -> str:
    done = subprocess.run(
        ["kubectl", f"--context={context}", *args], check=True, capture_output=capture, text=True
    )
    return done.stdout if capture else ""


class Cluster:
    """T2: the cluster of a kube context with the workflow worker deployed."""

    tier = "T2"

    def __init__(self, context: str):
        self.context = context
        self.forwards = {name: Forward(context, *spec) for name, spec in FORWARDS.items()}
        self.client: Client | None = None
        self.db: psycopg.Connection | None = None
        self._stack = AsyncExitStack()
        self._watch: asyncio.Task | None = None

    def refuse(self, command: str, scenario: str | None, targets: list[str]) -> None:
        if command == "rollout" and not BUILD.is_file():
            raise Refused(f"rollout builds the image with {BUILD}; run it from the tree's root")

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.forwards['objectstore'].port}"

    async def __aenter__(self) -> "Cluster":
        from ..testing import connect

        for forward in self.forwards.values():
            await asyncio.to_thread(forward.open)
        self._watch = asyncio.create_task(self._reopen())
        state = state_dir()
        self.credentials = load_credentials(state / "objectstore.credentials.json")
        self.password = (state / "appdb.credential").read_text().strip()
        self.db = self._connect()
        self.submit = ArtifactClient(self.endpoint, self.credentials["submit"])
        self.client = await connect(f"127.0.0.1:{self.forwards['temporal'].port}")
        return self

    async def __aexit__(self, *exc) -> None:
        self._watch.cancel()
        self.db.close()
        for forward in self.forwards.values():
            forward.close()

    def _connect(self) -> psycopg.Connection:
        port = self.forwards["appdb"].port
        return psycopg.connect(
            f"postgresql://postgres@127.0.0.1:{port}/postgres",
            password=self.password,
            autocommit=True,
        )

    async def _reopen(self) -> None:
        """Opens again each forward whose kubectl exited, as it does when its pod goes away."""
        while True:
            for forward in self.forwards.values():
                if not forward.alive:
                    await asyncio.to_thread(forward.open)
            await asyncio.sleep(1)

    def _seed(self, jobs: list[Job]) -> None:
        rows = [
            JobRow(
                job_id=job.job_id,
                tenant=job.event.tenant,
                submission_key=job.event.data["submission_key"],
                batch_id=job.event.data["batch_id"],
                priority_class=job.event.priorityclass,
                job_class=job.event.jobclass,
                manifest_ref=job.event.data["manifest"],
                schema_version="urn:wdf:schema:job.accepted:1.0",
                traceparent=job.event.traceparent,
                seed=PARAMS.seed,
                parameters=PARAMS.parameters,
                fingerprint=job.event.data["manifest"]["sha256"],
            )
            for job in jobs
        ]
        insert_accepted(self.db, rows)
        for job in jobs:
            self.submit.put_content(
                job.manifest, "manifest", "manifest.json", conn=self.db, job_id=job.job_id
            )
            self.submit.put_content(
                job.source, "source", "bundle.tar", conn=self.db, job_id=job.job_id
            )
            moved = transition(self.db, job.job_id, "accepted", "admitted")
            if moved.status != "admitted":
                raise RuntimeError(f"the row of {job.job_id} could not be claimed: {moved}")

    async def seed(self, jobs: list[Job]) -> None:
        await asyncio.to_thread(self._seed, jobs)

    def start_options(self, job: Job) -> dict:
        return {"search_attributes": search_attributes(job.event)}

    @asynccontextmanager
    async def running(self, scripts: Scripts, hold: dict[str, float]) -> AsyncIterator[dict]:
        async with AsyncExitStack() as stack:
            fakes = {}
            for pool, script in scripts.items():
                store = ArtifactClient(self.endpoint, self.credentials[pool])
                fakes[pool] = await stack.enter_async_context(
                    Pool(self.client, pool, script, store=store, hold_s=hold.get(pool, 0.0))
                )
            yield fakes

    def _pods(self, target: str) -> tuple[str, list[str]]:
        """The namespace of a target and the names of its pods: the workflow worker's by label,
        a Temporal service's by the name of its Deployment."""
        if target == WORKFLOW_WORKER:
            names = kubectl(
                self.context,
                f"--namespace={CONTROL}",
                "get",
                "pods",
                "-l",
                WORKER_LABEL,
                "-o",
                "jsonpath={.items[*].metadata.name}",
            ).split()
            return CONTROL, names
        pods = json.loads(
            kubectl(self.context, f"--namespace={INFRA}", "get", "pods", "-o", "json")
        )
        names = [
            pod["metadata"]["name"]
            for pod in pods["items"]
            if pod["metadata"]["name"].startswith(f"{target}-")
            and pod["status"].get("phase") != "Succeeded"
        ]
        return INFRA, names

    def _ready_again(self, target: str, gone: set[str]) -> bool:
        namespace, names = self._pods(target)
        for name in set(names) - gone:
            pod = json.loads(
                kubectl(self.context, f"--namespace={namespace}", "get", "pod", name, "-o", "json")
            )
            conditions = pod["status"].get("conditions", [])
            if any(c["type"] == "Ready" and c["status"] == "True" for c in conditions):
                return True
        return False

    def _restart(self, targets: list[str]) -> None:
        deleted = {}
        for target in targets:
            namespace, names = self._pods(target)
            if not names:
                raise Refused(f"no pod of {target} in the cluster of {self.context}")
            deleted[target] = (namespace, set(names))
        # Every target goes down at once, while the tasks of the jobs are in flight.
        for namespace, names in deleted.values():
            kubectl(
                self.context,
                f"--namespace={namespace}",
                "delete",
                "pod",
                "--wait=false",
                *sorted(names),
            )
        deadline = time.monotonic() + UP_S
        for target, (_, names) in deleted.items():
            while not self._ready_again(target, names):
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{target} was not ready again within {UP_S:.0f} s")
                time.sleep(1)
        for forward in self.forwards.values():
            forward.open()
        self.db.close()
        self.db = self._connect()

    async def restart(self, targets: list[str]) -> None:
        await asyncio.to_thread(self._restart, targets)

    async def zombie_commits(self, zombies: list[TaskInput]) -> int:
        """The index rows of the outputs of the attempts that zombies completed after timing out."""
        from ..testing.pool import producer_of

        attempts = [producer_of(task) for task in zombies]

        def count() -> int:
            return sum(
                self.db.execute(
                    "SELECT count(*) FROM artifact WHERE producer_job = %s"
                    " AND producer_task = %s AND producer_attempt = %s",
                    (p.job, p.task, p.attempt),
                ).fetchone()[0]
                for p in attempts
            )

        return await asyncio.to_thread(count)

    async def _status(self, handle: WorkflowHandle) -> StatusQuery | None:
        try:
            return await handle.query(naming().queries[0], result_type=StatusQuery)
        except RPCError:
            # A job whose plan is not loaded yet answers no status.
            return None

    async def pause_resume(self, handle: WorkflowHandle, job: Job) -> bool:
        """Pauses the job while its simulate tasks run and resumes it once it is paused, as the
        control API and admission would, and says whether it was paused and then resumed."""

        async def running() -> bool:
            status = await self._status(handle)
            return status is not None and any(
                t.state == "running" and t.id.startswith("sim-") for t in status.tasks
            )

        await _until(running, DEADLINE_S, f"simulate task of {job.job_id} running")
        pause = await asyncio.to_thread(request_action, self.db, job.job_id, "pause")
        await handle.signal("pause", ControlSignal(control_seq=pause.control_seq, reason="harness"))

        async def paused() -> bool:
            status = await self._status(handle)
            return status is not None and status.job_status == "paused"

        await _until(paused, DEADLINE_S, f"pause of {job.job_id}")

        def request_resume() -> int:
            resume = request_action(self.db, job.job_id, "resume")
            transition(self.db, job.job_id, "paused", "resume-pending")
            return resume.control_seq

        control_seq = await asyncio.to_thread(request_resume)
        await handle.signal("resume", ResumeSignal(control_seq=control_seq, manifest=None))
        return True

    def _deploy(self, old: str, new: str) -> None:
        """Builds the image of this tree as build `new`, pushes it, and runs it beside build `old`
        as its own Deployment, a copy of the old one with the build id and the image changed."""
        image = f"{REGISTRY}/{IMAGE}:{new}"
        env = {**os.environ, naming().workflow.build_id_env: new}
        subprocess.run([str(BUILD), image], check=True, env=env)
        subprocess.run(["docker", "push", "--quiet", image], check=True)
        deployment = json.loads(
            kubectl(
                self.context,
                f"--namespace={CONTROL}",
                "get",
                "deployment",
                f"{WORKFLOW_WORKER}-{old}",
                "-o",
                "json",
            )
        )
        meta = deployment["metadata"]
        deployment["metadata"] = {
            "name": f"{WORKFLOW_WORKER}-{new}",
            "namespace": meta["namespace"],
            "labels": {**meta["labels"], "wdf.dev/build-id": new},
        }
        deployment.pop("status", None)
        spec = deployment["spec"]
        for labels in (spec["selector"]["matchLabels"], spec["template"]["metadata"]["labels"]):
            labels["wdf.dev/build-id"] = new
        [container] = spec["template"]["spec"]["containers"]
        container["image"] = image
        subprocess.run(
            ["kubectl", f"--context={self.context}", "apply", "-f", "-"],
            input=json.dumps(deployment),
            text=True,
            check=True,
            capture_output=True,
        )
        kubectl(
            self.context,
            f"--namespace={CONTROL}",
            "rollout",
            "status",
            f"deployment/{WORKFLOW_WORKER}-{new}",
            f"--timeout={UP_S:.0f}s",
        )

    async def deploy(self, old: str, new: str) -> None:
        await asyncio.to_thread(self._deploy, old, new)

    def deleter(self):
        return versioning.kubectl_delete(self.context)
