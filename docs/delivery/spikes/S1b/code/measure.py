"""Runs install and build steps in containers capped like the `ci` compile slot.

Prints one `figure <name> <value>` line per number.

Each step gets its own container, which is removed afterwards, so at most one runs at a time.
The install step has the default network, and the build step has `--network none`.
The bundle tree is copied into the container's scratch directory from inside the container, so its
page cache is charged to the container as a pod's download would be.
"""

import argparse
import subprocess
import threading
import time
from pathlib import Path

MIB = 1048576
CAP = ["--memory", "1g", "--memory-swap", "1g", "--cpus", "1"]
COMPONENTIZE_PY = "0.25.1"


def figure(name, value):
    print(f"figure {name} {value}", flush=True)


def docker(*args, check=True):
    run = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and run.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)}: {run.stderr.strip()}")
    return run


def flat(path):
    pairs = (line.partition(" ") for line in path.read_text().splitlines())
    return {key: int(value) for key, _, value in pairs}


class Sampler(threading.Thread):
    """Peak working set (memory.current less inactive_file) and peak anon, sampled every 10 ms."""

    def __init__(self, cgroup):
        super().__init__(daemon=True)
        self.cgroup = cgroup
        self.ws = self.anon = 0
        self._stop = threading.Event()

    def sample(self):
        try:
            stat = flat(self.cgroup / "memory.stat")
            current = int((self.cgroup / "memory.current").read_text())
        except OSError:
            return
        self.ws = max(self.ws, current - stat["inactive_file"])
        self.anon = max(self.anon, stat["anon"])

    def run(self):
        while not self._stop.is_set():
            self.sample()
            self._stop.wait(0.01)

    def stop(self):
        self._stop.set()
        self.join()
        self.sample()


class Container:
    """One labelled, capped container that idles until removed."""

    def __init__(self, args, name, network, extra=()):
        self.name = f"wdf-{args.run_id}-{name}"
        docker(
            "run", "--detach", "--name", self.name, "--label", f"{args.label}={args.run_id}",
            *CAP, "--network", network, "--user", args.user, "--env", "HOME=/tmp",
            f"--volume={args.out}:/work", *extra, args.image, "sleep", "3600",
        )  # fmt: skip
        container_id = docker("inspect", "--format", "{{.Id}}", self.name).stdout.strip()
        self.cgroup = Path(f"/sys/fs/cgroup/system.slice/docker-{container_id}.scope")

    def exec(self, prefix, command, workdir="/"):
        """Runs one command and prints its exit, wall time, peaks and OOM kills under `prefix`."""
        events_before = flat(self.cgroup / "memory.events")
        sampler = Sampler(self.cgroup)
        sampler.start()
        t0 = time.monotonic()
        run = docker("exec", "--workdir", workdir, self.name, "sh", "-c", command, check=False)
        wall = time.monotonic() - t0
        sampler.stop()
        events = flat(self.cgroup / "memory.events")
        figure(f"{prefix}.exit", run.returncode)
        figure(f"{prefix}.wall_s", f"{wall:.2f}")
        figure(f"{prefix}.peak_mib", f"{int((self.cgroup / 'memory.peak').read_text()) / MIB:.0f}")
        figure(f"{prefix}.peak_ws_mib", f"{sampler.ws / MIB:.0f}")
        figure(f"{prefix}.peak_anon_mib", f"{sampler.anon / MIB:.0f}")
        figure(f"{prefix}.oom_kill", events["oom_kill"] - events_before["oom_kill"])
        return run

    def remove(self):
        docker("rm", "--force", "--volumes", self.name, check=False)


def install(args, index):
    """The install step, with network: componentize-py into a throwaway venv."""
    box = Container(args, f"install-{index}", "bridge")
    try:
        command = f"rm -rf /work/venv-{index} && python3 -m venv /work/venv-{index}"
        command += f" && /work/venv-{index}/bin/pip install -q --no-cache-dir"
        command += f" componentize-py=={COMPONENTIZE_PY}"
        run = box.exec(f"install.{index}", command)
        if run.returncode != 0:
            raise RuntimeError(f"install {index}: {run.stderr.strip()}")
        version = docker("exec", box.name, f"/work/venv-{index}/bin/componentize-py", "--version")
        figure(f"install.{index}.componentize_py", version.stdout.split()[-1])
    finally:
        box.remove()


def bundle_tree(args, size, modules=1, hold=0):
    """The generated bundle tree of `size` MiB under the work directory, written on first use."""
    name = f"bundle-{size}-{modules}-{hold}"
    if not (Path(args.out) / name).is_dir():
        gen = [
            Path(__file__).with_name("gen.py"),
            "--size-mib",
            str(size),
            "--modules",
            str(modules),
        ]
        gen += ["--hold-mib", str(hold), "--out", Path(args.out) / name]
        run = subprocess.run(["python3", *gen], capture_output=True, text=True, check=True)
        figure(f"{name}.source_bytes", run.stdout.split()[-1])
    return name


def build(args, prefix, size, venv, modules=1, hold=0):
    """The componentize-py step, with no network, over one generated bundle. Returns its exit."""
    bundle = bundle_tree(args, size, modules, hold)
    box = Container(args, prefix.replace(".", "-"), "none")
    try:
        setup = f"rm -rf /tmp/b && cp -r /work/{bundle} /tmp/b && cp -r /work/wit /tmp/wit"
        docker("exec", box.name, "sh", "-c", setup)
        figure(
            f"{prefix}.before_run_mib",
            round(int((box.cgroup / "memory.current").read_text()) / MIB),
        )
        command = f"/work/{venv}/bin/componentize-py -q -d /tmp/wit -w task"
        command += " componentize wdf_workloads.main"
        command += f" -o /tmp/out.wasm && cp /tmp/out.wasm /work/{prefix}.wasm"
        run = box.exec(prefix, command, workdir="/tmp/b")
        wasm = Path(args.out) / f"{prefix}.wasm"
        figure(f"{prefix}.component_bytes", wasm.stat().st_size if wasm.is_file() else 0)
        if run.returncode != 0:
            tail = (run.stderr.strip().splitlines() or [""])[-1]
            figure(f"{prefix}.stderr_tail", tail.strip().replace(" ", "_")[:120] or "-")
        return run.returncode
    finally:
        box.remove()


def userns(args, prefix, extra=()):
    """Whether `unshare --user --net` works in a build container, as the invoking user."""
    box = Container(args, prefix.replace(".", "-"), "none", extra)
    try:
        run = docker(
            "exec", box.name, "unshare", "--user", "--net", "--map-root-user", "true", check=False
        )
        figure(f"{prefix}.exit", run.returncode)
        figure(f"{prefix}.stderr", run.stderr.strip().replace(" ", "_") or "-")
    finally:
        box.remove()


def main():
    parser = argparse.ArgumentParser()
    for flag in ("--image", "--label", "--run-id", "--out", "--user"):
        parser.add_argument(flag, required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--standin", type=float, required=True, help="MiB of generated kernel source"
    )
    parser.add_argument("--heavy", type=float, nargs="+", required=True, help="MiB, ascending")
    parser.add_argument("--over", type=float, nargs="*", default=[], help="MiB, once each")
    parser.add_argument("--split", nargs="*", default=[], help="MiB:MODULES, once each")
    parser.add_argument(
        "--hold", type=int, nargs="*", default=[], help="MiB held at import, once each"
    )
    args = parser.parse_args()
    for index in range(1, args.runs + 1):
        install(args, index)
        venv = f"venv-{index}"
        build(args, f"standin.{index}", args.standin, venv)
        smallest = "none"
        for size in args.heavy:
            if build(args, f"heavy.{index}.{size}", size, venv) == 137:
                smallest = size
                break
        figure(f"heavy.{index}.smallest_killed_mib", smallest)
    for size in args.over:
        build(args, f"over.{size}", size, "venv-1")
    for spec in args.split:
        size, _, modules = spec.partition(":")
        build(args, f"split.{size}.{modules}", float(size), "venv-1", modules=int(modules))
    for hold in args.hold:
        build(args, f"hold.{hold}", args.standin, "venv-1", hold=hold)
    userns(args, "userns.default")
    userns(args, "userns.unconfined", ["--security-opt", "seccomp=unconfined"])


if __name__ == "__main__":
    main()
