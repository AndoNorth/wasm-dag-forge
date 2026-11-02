"""Sizes the infra node from the measured peaks and the carried figures, and prints `figure` lines.

Usage: node.py <figures file of the run>
The quota comes from `scripts/cluster/namespaces.sh render` run on a probe copy of the preset with
the proposed infra node, so the rule is the repository's and never retyped here.
"""

import math
import re
import subprocess
import sys
from pathlib import Path

import yaml
from cg import figure

ROOT = Path(__file__).resolve().parents[2]
DAEMONS_MIB = 50  # kindnet on every worker and on the infra node
HEADROOM = 1.2  # the proposed headroom of the pass line
LIMIT_FACTOR = (
    1.25  # a limit of a measured component is its peak plus a quarter, in whole 64 MiB steps
)
CI_RUNNER_MIB = 15984  # 16,367,524 kB of MemTotal on the runner of the kind-smoke run
STEP_MIB = 128
# Carried, not re-run: (peak MiB, limit MiB, source).
CARRIED = {
    "temporal": (686, 1024, "S3b: peak anon with both caches capped, sized at 1 CPU and 1 GiB"),
    "temporaldb": (
        1024,
        1024,
        "S3b: run at 1 GiB, no peak recorded, so the limit stands in for the peak",
    ),
    "rabbitmq": (154.6, 256, "S4: peak 154.6 MiB, limit is a decision"),
}
# An estimate until the dispatch API, the workflow worker and admission replace it.
CONTROL_MIB = 640


def read_figures(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        parts = line.split(" ", 2)
        if parts[0] == "figure":
            values[parts[1]] = parts[2]
    return values


def roundup(value, step):
    return int(math.ceil(value / step) * step)


def allocatable(node_mib):
    return node_mib - (256 + (node_mib + 15) // 16)


def preset(name):
    return yaml.safe_load((ROOT / f"deploy/presets/{name}.yaml").read_text())


def node_mib(doc, role):
    return next(n["memory_mib"] for n in doc["nodes"] if n["role"] == role)


def render_quota(name, infra_mib):
    """The wdf-infra `limits.memory` that `namespaces.sh render` gives for this infra node size."""
    doc = preset(name)
    for node in doc["nodes"]:
        if node["role"] == "infra":
            node["memory_mib"] = infra_mib
    probe = ROOT / f"deploy/presets/s8-probe-{name}.yaml"
    probe.write_text(yaml.safe_dump(doc))
    try:
        text = subprocess.run(
            [str(ROOT / "scripts/cluster/namespaces.sh"), "render", probe.stem],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    finally:
        probe.unlink()
    block = text.split("namespace: wdf-infra", 1)[1].split("---", 1)[0]
    return int(re.search(r"limits\.memory: (\d+)Mi", block).group(1))


def main():
    figures = read_figures(sys.argv[1])
    components = {}
    # The store is sized from the run with its heap bounded, as the shipped default was OOM killed.
    for name, key in (("appdb", "pg"), ("objectstore", "store_tuned")):
        peak = float(figures[f"{key}.peak_ws_mib.load"])
        components[name] = (peak, roundup(peak * LIMIT_FACTOR, 64))
    for name, (peak, limit, _) in CARRIED.items():
        components[name] = (peak, limit)
    wdf_infra = ["appdb", "objectstore", "temporal", "temporaldb", "rabbitmq"]
    for name in wdf_infra:
        peak, limit = components[name]
        figure(f"node.component.{name}.peak_mib", f"{peak:.0f}")
        figure(f"node.component.{name}.limit_mib", limit)
    figure("node.component.control_reserve.limit_mib", CONTROL_MIB)
    limits = sum(components[n][1] for n in wdf_infra)
    peaks = sum(components[n][0] for n in wdf_infra)
    figure("node.sum_limits_mib", limits)
    figure("node.sum_peaks_mib", f"{peaks:.0f}")
    figure("node.sum_limits_with_control_mib", limits + CONTROL_MIB)
    figure("node.sum_peaks_with_control_plus_20pct_mib", f"{(peaks + CONTROL_MIB) * HEADROOM:.0f}")

    verdicts = []
    for name in ("ci", "dev"):
        doc = preset(name)
        now = node_mib(doc, "infra")
        size = now
        while True:
            alloc = allocatable(size)
            quota = render_quota(name, size)
            ok_quota = limits <= quota
            ok_limits = limits + CONTROL_MIB <= alloc - DAEMONS_MIB
            ok_peaks = (peaks + CONTROL_MIB) * HEADROOM <= alloc - DAEMONS_MIB
            total = sum(n["count"] * n["memory_mib"] for n in doc["nodes"]) + size - now
            ok_runner = name != "ci" or total <= CI_RUNNER_MIB
            if size == now:
                figure(f"node.{name}.current.infra_mib", now)
                figure(f"node.{name}.current.quota_mib", quota)
                figure(
                    f"node.{name}.current.pass",
                    "yes" if ok_quota and ok_limits and ok_peaks else "no",
                )
            if (ok_quota and ok_limits and ok_peaks) or total > 40000:
                break
            size += STEP_MIB
        figure(f"node.{name}.infra_mib", size)
        figure(f"node.{name}.allocatable_mib", alloc)
        figure(f"node.{name}.allocatable_after_daemons_mib", alloc - DAEMONS_MIB)
        figure(f"node.{name}.quota_mib", quota)
        figure(f"node.{name}.check.limits_within_quota", "yes" if ok_quota else "no")
        figure(
            f"node.{name}.check.limits_with_control_within_allocatable",
            "yes" if ok_limits else "no",
        )
        figure(
            f"node.{name}.check.peaks_plus_20pct_within_allocatable", "yes" if ok_peaks else "no"
        )
        figure(f"node.{name}.sum_of_node_limits_mib", total)
        if name == "ci":
            figure("node.ci.check.within_runner_mem_total", "yes" if ok_runner else "no")
        verdicts.append(ok_quota and ok_limits and ok_peaks and ok_runner)

    store_pvc = 10240
    volume_mb = int(figures["store.volume_size_limit_mb"])
    figure("pvc.temporaldb_mib", 5120)
    figure("pvc.store_mib", store_pvc)
    figure("pvc.store.volume_size_limit_mb", math.floor(0.8 * store_pvc / (7 * 4)))
    figure(
        "pvc.store.volume_size_limit_matches_run",
        "yes" if math.floor(0.8 * store_pvc / 28) == volume_mb else "no",
    )
    pgdata = float(figures["pg.pgdata_mib.uncompressed"])
    figure("pvc.appdb_mib", roundup(pgdata * 1.25, 1024))
    figure("verdict", "pass" if all(verdicts) else "fail")


if __name__ == "__main__":
    main()
