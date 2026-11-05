import atexit
import fcntl
import os
import random
import signal
import socket
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
RUN_LABEL = "wdf.dev/verify-run"
PORT_BASE = 19000
MAX_SLOT = 9
PORTS_PER_SLOT = 10
LOCK_COUNT = 2
LOCK_TIMEOUT_SECONDS = 30 * 60
MEMORY_CAP = "1g"

# Image keys of deploy/versions.yaml whose repository is not the key itself.
REPOSITORIES = {"canary": "busybox"}


class DockerUnavailable(RuntimeError):
    """Docker is absent or its daemon is unreachable, which fails a T1 test and never skips it."""


class LockTimeout(RuntimeError):
    """No T1 lock file became free within the timeout."""


class ContainerError(RuntimeError):
    pass


def image_ref(key: str) -> str:
    """The pinned reference `<repository>:<version>@<digest>` of an entry of the images map."""
    pins = yaml.safe_load((REPO_ROOT / "deploy/versions.yaml").read_text())["images"]
    pin = pins[key]
    return f"{REPOSITORIES.get(key, key)}:{pin['version']}@{pin['digest']}"


def lock_paths() -> list[Path]:
    directory = Path(os.environ.get("WDF_T1_LOCK_DIR", "/tmp"))
    return [directory / f"wdf-t1.{i}.lock" for i in range(LOCK_COUNT)]


def slot_from_env() -> int:
    slot = int(os.environ.get("WDF_SLOT", "0"))
    if not 0 <= slot <= MAX_SLOT:
        raise ValueError(f"WDF_SLOT must be 0 to {MAX_SLOT}, got {slot}")
    return slot


def host_port(slot: int, k: int) -> int:
    if not 0 <= k < PORTS_PER_SLOT:
        raise ValueError(f"port index k must be 0 to {PORTS_PER_SLOT - 1}, got {k}")
    return PORT_BASE + slot * PORTS_PER_SLOT + k


def _holders() -> str:
    held = []
    for path in lock_paths():
        try:
            held.append(f"{path}: {path.read_text().strip() or 'unknown holder'}")
        except OSError:
            held.append(f"{path}: unreadable")
    return "; ".join(held)


def _acquire_locks(run_id: str, count: int, timeout: float, poll: float = 0.5) -> list[int]:
    """Takes `count` lock files together, or none of them.

    A session that got fewer than it needs releases what it got before it sleeps, so two sessions
    that each want two never wait on each other while holding one, and the randomized sleep keeps
    them from retrying in step.
    """
    deadline = time.monotonic() + timeout
    while True:
        fds: list[int] = []
        for path in lock_paths():
            if len(fds) == count:
                break
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                continue
            fds.append(fd)
        if len(fds) == count:
            for fd in fds:
                os.ftruncate(fd, 0)
                os.write(fd, f"pid={os.getpid()} run={run_id}\n".encode())
            return fds
        for fd in fds:
            os.close(fd)
        if time.monotonic() >= deadline:
            raise LockTimeout(
                f"waited {timeout:.0f}s for {count} free T1 lock file(s), holders: {_holders()}"
            )
        time.sleep(poll * random.uniform(0.5, 1.5))


def _acquire_lock(run_id: str, timeout: float, poll: float = 0.5) -> int:
    return _acquire_locks(run_id, 1, timeout, poll)[0]


def _docker(*args: str, timeout: float = 300) -> str:
    try:
        run = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise DockerUnavailable("the docker command is not on PATH") from None
    except subprocess.TimeoutExpired:
        raise DockerUnavailable(f"docker {args[0]} timed out after {timeout:.0f}s") from None
    if run.returncode != 0:
        raise ContainerError(f"docker {' '.join(args)} failed: {run.stderr.strip()}")
    return run.stdout.strip()


@dataclass(frozen=True)
class Container:
    id: str
    name: str
    host_ports: dict[int, int]

    def exec(self, *command: str, check: bool = True) -> subprocess.CompletedProcess:
        """Runs a command inside this container and returns its text output.

        A non-zero exit raises ContainerError when `check` is set, and is returned otherwise.
        """
        try:
            run = subprocess.run(
                ["docker", "exec", self.id, *command], capture_output=True, text=True, timeout=300
            )
        except FileNotFoundError:
            raise DockerUnavailable("the docker command is not on PATH") from None
        except subprocess.TimeoutExpired:
            raise DockerUnavailable(f"docker exec timed out after 300s in {self.name}") from None
        if check and run.returncode != 0:
            raise ContainerError(
                f"{' '.join(command)} exited {run.returncode} in {self.name}: {run.stderr.strip()}"
            )
        return run

    def wait_ready(self, k: int, timeout: float = 60) -> None:
        """Waits until the loopback port of index k accepts a TCP connection."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                socket.create_connection(("127.0.0.1", self.host_ports[k]), timeout=2).close()
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise ContainerError(
                        f"port index {k} of {self.name} did not accept connections"
                    ) from None
                time.sleep(0.2)


class Session:
    """Holds the T1 lock files it declares for its whole life and starts named containers.

    `containers` is 1 or 2 and is both the number of lock files taken up front and the number of
    containers that may run at once.
    A session opened while another is open in the same process shares that session's lock files,
    container cap and host ports, because the process already holds the host's share and a second
    request for files would wait on the process itself.
    """

    _open: "Session | None" = None

    def __init__(self, containers: int = 1):
        if not 1 <= containers <= LOCK_COUNT:
            raise ValueError(f"containers must be 1 to {LOCK_COUNT}, got {containers}")
        self.containers = containers
        self.slot = slot_from_env()
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = os.environ.get("WDF_RUN_ID") or f"{stamp}-{self.slot}"
        self.timeout = float(os.environ.get("WDF_T1_LOCK_TIMEOUT", LOCK_TIMEOUT_SECONDS))
        self._outer: Session | None = None
        self._fds: list[int] = []
        self._lanes: dict[str, int] = {}
        self._count = 0

    def __enter__(self):
        if Session._open is not None:
            self._outer = Session._open
            self.run_id = self._outer.run_id
            return self
        try:
            _docker("info", timeout=30)
        except ContainerError as exc:
            raise DockerUnavailable(f"the docker daemon is unreachable: {exc}") from None
        self._fds = _acquire_locks(self.run_id, self.containers, self.timeout)
        Session._open = self
        atexit.register(self.close)
        try:
            signal.signal(signal.SIGTERM, self._terminate)
        except ValueError:  # not the main thread, where signals cannot be installed
            pass
        return self

    def __exit__(self, *exc):
        self.close()

    def _terminate(self, signum, frame):
        self.close()
        raise SystemExit(128 + signum)

    def remove_containers(self) -> None:
        """Removes every container that carries this run's label, and no other."""
        ids = _docker("ps", "-aq", "--filter", f"label={RUN_LABEL}={self.run_id}").split()
        if ids:
            _docker("rm", "-f", *ids)

    def close(self) -> None:
        if self._outer is not None:
            self._outer = None
            return
        if not self._fds:
            return
        Session._open = None
        try:
            self.remove_containers()
        finally:
            for fd in self._fds:
                os.close(fd)
            self._fds = []
            atexit.unregister(self.close)

    def _remove_container(self, docker_name: str) -> None:
        ids = _docker("ps", "-aq", "--filter", f"name=^{docker_name}$").split()
        if ids:
            _docker("rm", "-f", *ids)

    @contextmanager
    def container(
        self,
        image_key: str,
        ports: dict[int, int] | None = None,
        command=(),
        env=None,
        tmpfs: dict[str, int] | None = None,
        mounts: dict[str | Path, str] | None = None,
        name: str | None = None,
    ):
        """Starts one container, yields it and removes it, and only it, afterwards.

        `name` identifies the container within the session, and a name that is running is refused.
        `ports` maps a port index k to a container port.
        A session of one container publishes it on 127.0.0.1:<19000 + slot * 10 + k> for k below 10.
        A session of two gives each running container half of the slot, so two containers that both
        ask for index 0 get different host ports, and k is then below 5.
        `tmpfs` maps a container path to the size cap in MiB of a memory-backed mount there,
        which counts against the memory cap of the container.
        `mounts` maps an existing host file or directory to a container path, mounted read-only.
        """
        if self._outer is not None:
            with self._outer.container(image_key, ports, command, env, tmpfs, mounts, name) as c:
                yield c
            return
        if not self._fds:
            raise ContainerError("the session is not open")
        name = name or f"c{self._count + 1}"
        if name in self._lanes:
            raise ContainerError(f"a container named {name!r} is already running in this session")
        if len(self._lanes) >= self.containers:
            raise ContainerError(
                "one container at a time, stop the running one first"
                if self.containers == 1
                else f"at most {self.containers} containers at a time, stop a running one first"
            )
        width = PORTS_PER_SLOT // self.containers
        lane = min(set(range(self.containers)) - set(self._lanes.values()))
        for k in ports or {}:
            if not 0 <= k < width:
                raise ValueError(f"port index k must be 0 to {width - 1}, got {k}")
        self._count += 1
        docker_name = f"wdf-{self.run_id}-{self._count}"
        mapping = {k: host_port(self.slot, lane * width + k) for k in (ports or {})}
        args = ["run", "-d", "--name", docker_name, "--label", f"{RUN_LABEL}={self.run_id}"]
        args += ["--memory", MEMORY_CAP, "--memory-swap", MEMORY_CAP]
        for k, container_port in (ports or {}).items():
            args += ["-p", f"127.0.0.1:{mapping[k]}:{container_port}"]
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        for path, size_mib in (tmpfs or {}).items():
            args += ["--tmpfs", f"{path}:rw,mode=1777,size={size_mib}m"]
        for host_path, container_path in (mounts or {}).items():
            host = Path(host_path)
            if not host.is_absolute() or not host.exists():
                raise ContainerError(f"a mount source must be an existing absolute path: {host}")
            args += ["--mount", f"type=bind,src={host},dst={container_path},readonly"]
        self._lanes[name] = lane
        try:
            container_id = _docker(*args, image_ref(image_key), *command)
            yield Container(container_id, docker_name, mapping)
        finally:
            try:
                self._remove_container(docker_name)
            finally:
                del self._lanes[name]
