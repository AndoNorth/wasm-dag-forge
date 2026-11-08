"""Runs the guest in a capped container, cold then warm, and prints `figure <name> <value>` lines.

Each run gets its own container, which is removed afterwards. The wasmtime binary, the component and
(for a warm run) the module cache are copied into the container's scratch directory from inside the
container, so their page cache is charged to it as a pod's download would be.
"""

import argparse
import shutil
import subprocess
import threading
import time
from pathlib import Path

MIB = 1048576


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
        stat = flat(self.cgroup / "memory.stat")
        current = int((self.cgroup / "memory.current").read_text())
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


def one_run(args, variant, flags, temp, index, seed, hold=0):
    """One container, one guest run. Returns the scratch directory."""
    name = f"wdf-{args.run_id}-{variant}-{temp}-{index}"
    scratch = Path(args.out) / name
    (scratch / "in").mkdir(parents=True)
    (scratch / "out").mkdir()
    (scratch / "cache").mkdir()
    hold_args = f'"hold_mib":{hold}' if hold else ""
    (scratch / "in" / "input.json").write_text(
        f'{{"args":{{{hold_args}}},"seed":7,"workload":"noop"}}\n'
    )
    (scratch / "cache.toml").write_text('[cache]\ndirectory = "/scratch/cache"\n')
    mounts = [f"--volume={p}:{p}:ro" for p in args.closure]
    mounts += [f"--volume={scratch}:/scratch", f"--volume={args.component}:/src/guest.wasm:ro"]
    if seed:
        mounts.append(f"--volume={seed}:/seed:ro")
    docker(
        "run", "--detach", "--name", name, "--label", f"{args.label}={args.run_id}",
        "--memory", "384m", "--memory-swap", "384m", "--cpus", "0.25", "--network", "none",
        "--user", args.user, *mounts, args.image, "sleep", "3600",
    )  # fmt: skip
    prefix = f"{variant}.{temp}.{index}"
    try:
        # cp -p keeps the binary's mtime, which wasmtime puts in the name of its cache directory.
        setup = "cat /src/guest.wasm >/scratch/guest.wasm"
        setup += f" && cp -p {args.wasmtime} /scratch/wasmtime"
        if seed:
            setup += " && cp -r /seed/. /scratch/cache/"
        docker("exec", name, "sh", "-c", setup)
        container_id = docker("inspect", "--format", "{{.Id}}", name).stdout.strip()
        cgroup = Path(f"/sys/fs/cgroup/system.slice/docker-{container_id}.scope")
        before = int((cgroup / "memory.current").read_text())
        figure(f"{prefix}.before_run_mib", round(before / MIB))
        events_before = flat(cgroup / "memory.events")
        sampler = Sampler(cgroup)
        sampler.start()
        command = ["/scratch/wasmtime", "run", "-C", "cache=y"]
        command += ["-C", "cache-config=/scratch/cache.toml"]
        command += [*flags, "--dir", "/scratch/in::/in", "--dir", "/scratch/out::/out"]
        t0 = time.monotonic()
        run = docker("exec", name, *command, "/scratch/guest.wasm", check=False)
        wall = time.monotonic() - t0
        sampler.stop()
        events = flat(cgroup / "memory.events")
        peak = int((cgroup / "memory.peak").read_text()) / MIB
        ok = run.returncode == 0 and (scratch / "out" / "results.xml").is_file()
        figure(f"{prefix}.exit", run.returncode)
        figure(f"{prefix}.completed", "yes" if ok else "no")
        figure(f"{prefix}.wall_s", f"{wall:.2f}")
        figure(f"{prefix}.peak_mib", f"{peak:.0f}")
        figure(f"{prefix}.peak_ws_mib", f"{sampler.ws / MIB:.0f}")
        figure(f"{prefix}.peak_anon_mib", f"{sampler.anon / MIB:.0f}")
        guest = args.guest_mib + hold
        figure(f"{prefix}.guest_overhead_mib", f"{peak - guest:.0f}")
        figure(f"{prefix}.guest_overhead_ws_mib", f"{sampler.ws / MIB - guest:.0f}")
        figure(f"{prefix}.oom_kill", events["oom_kill"] - events_before["oom_kill"])
        figure(f"{prefix}.memory_max_events", events["max"] - events_before["max"])
        cache_bytes = sum(p.stat().st_size for p in (scratch / "cache").rglob("*") if p.is_file())
        figure(f"{prefix}.cache_mib", f"{cache_bytes / MIB:.1f}")
        if not ok:
            (Path(args.out) / f"{name}.stderr").write_text(run.stderr)
    finally:
        docker("rm", "--force", "--volumes", name, check=False)
    return scratch


def seed_cache(args, variant, flags):
    """Warm runs start from a module cache filled by one uncapped host run with the same flags."""
    work = Path(args.out) / f"seed-{variant}"
    for sub in ("in", "out", "cache"):
        (work / sub).mkdir(parents=True)
    (work / "in" / "input.json").write_text('{"args":{},"seed":7,"workload":"noop"}\n')
    (work / "cache.toml").write_text(f'[cache]\ndirectory = "{work / "cache"}"\n')
    command = [args.wasmtime, "run", "-C", "cache=y", "-C", f"cache-config={work / 'cache.toml'}"]
    command += [*flags, "--dir", f"{work / 'in'}::/in", "--dir", f"{work / 'out'}::/out"]
    subprocess.run([*command, args.component], check=True)
    return work / "cache"


def main():
    parser = argparse.ArgumentParser()
    for flag in ("--component", "--wasmtime", "--image", "--label", "--run-id", "--out", "--user"):
        parser.add_argument(flag, required=True)
    parser.add_argument("--closure", nargs="+", required=True)
    parser.add_argument("--guest-mib", type=float, required=True)
    parser.add_argument("--variant", action="append", required=True, help="NAME or NAME=FLAG,FLAG")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--hold", type=int, nargs="*", default=[], help="MiB, one cold and warm run each"
    )
    args = parser.parse_args()
    for spec in args.variant:
        variant, _, flags = spec.partition("=")
        flags = [part for flag in flags.split(",") if flag for part in ("-C", flag)]
        seed = seed_cache(args, variant, flags)
        for index in range(1, args.runs + 1):
            for temp, cache in (("cold", None), ("warm", seed)):
                shutil.rmtree(one_run(args, variant, flags, temp, index, cache), ignore_errors=True)
    seed = Path(args.out) / "seed-default" / "cache"
    for hold in args.hold:
        for temp, cache in (("cold", None), ("warm", seed)):
            scratch = one_run(args, f"hold{hold}", [], temp, 1, cache, hold)
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
