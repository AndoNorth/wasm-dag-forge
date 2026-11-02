"""Samples the cgroup of one container once a second and reports peaks per named phase."""

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
    return run.stdout.strip()


def _flat(path):
    out = {}
    for line in Path(path).read_text().splitlines():
        key, _, value = line.partition(" ")
        out[key] = int(value)
    return out


class Sampler(threading.Thread):
    """The working set is memory.current less inactive_file, the figure `docker stats` reports."""

    def __init__(self, container, interval=1.0):
        super().__init__(daemon=True)
        container_id = docker("inspect", "--format", "{{.Id}}", container)
        self.dir = Path(f"/sys/fs/cgroup/system.slice/docker-{container_id}.scope")
        self.interval = interval
        self.phase = "start"
        self.samples = []
        self._stop = threading.Event()
        self.events_at_start = self.events()

    def events(self):
        return _flat(self.dir / "memory.events")

    def read(self):
        stat = _flat(self.dir / "memory.stat")
        current = int((self.dir / "memory.current").read_text())
        cpu = _flat(self.dir / "cpu.stat")["usage_usec"]
        return {
            "t": time.monotonic(),
            "phase": self.phase,
            "current": current,
            "ws": current - stat["inactive_file"],
            "anon": stat["anon"],
            "shmem": stat["shmem"],
            "cpu": cpu,
        }

    def run(self):
        while not self._stop.is_set():
            self.samples.append(self.read())
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
        self.join()
        self.samples.append(self.read())

    def peaks(self, phase=None):
        """Peak MiB of working set, anon, shmem and current, and peak cores, for a phase or all."""
        rows = [s for s in self.samples if phase is None or s["phase"] == phase]
        if not rows:
            return None
        cores = 0.0
        for a, b in zip(self.samples, self.samples[1:], strict=False):
            if (phase is None or b["phase"] == phase) and b["t"] > a["t"]:
                cores = max(cores, (b["cpu"] - a["cpu"]) / 1e6 / (b["t"] - a["t"]))
        peak = {k: max(s[k] for s in rows) / MIB for k in ("ws", "anon", "shmem", "current")}
        peak["cores"] = cores
        return peak

    def phases(self):
        seen = []
        for s in self.samples:
            if s["phase"] not in seen:
                seen.append(s["phase"])
        return seen

    def report(self, prefix, phases=None):
        for phase in phases or self.phases():
            p = self.peaks(phase)
            if p:
                figure(f"{prefix}.peak_ws_mib.{phase}", f"{p['ws']:.0f}")
        p = self.peaks()
        figure(f"{prefix}.peak_ws_mib.all", f"{p['ws']:.0f}")
        figure(f"{prefix}.peak_anon_mib.all", f"{p['anon']:.0f}")
        figure(f"{prefix}.peak_shmem_mib.all", f"{p['shmem']:.0f}")
        figure(f"{prefix}.peak_current_mib.all", f"{p['current']:.0f}")
        figure(f"{prefix}.peak_cpu_cores.all", f"{p['cores']:.2f}")
        now = self.events()
        figure(f"{prefix}.oom_kill", now["oom_kill"] - self.events_at_start["oom_kill"])
        figure(f"{prefix}.memory_max_events", now["max"] - self.events_at_start["max"])
