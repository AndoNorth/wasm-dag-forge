"""Each fault mode, run in a guest built by the compile library and run by the runtime library,
gets the class it documents."""

from pathlib import Path

import pytest
import yaml
from wdf_compile.build import build
from wdf_runtime.classify import Facts, classify
from wdf_runtime.contract import canonical
from wdf_runtime.models import CONTRACT
from wdf_runtime.run import run, warm
from wdf_workloads import bundle, fault

MEMORY_MIB = 384
TIMEOUT_S = 30
HANG_TIMEOUT_S = 2
ARGS = {
    "memory_kill": {"mib": 64},
    "slow_start": {"seconds": 1},
    "fail_late": {"at_progress": 0.5},
}
EXIT_CLASSES = yaml.safe_load((CONTRACT / "exit-classes.yaml").read_text())
SIMULATE = {row["class"] for row in EXIT_CLASSES["pools"]["simulate"]["classes"]}


@pytest.fixture(scope="module")
def component(tmp_path_factory) -> tuple[Path, Path]:
    """The workload component and a module cache it is already warm in."""
    work = tmp_path_factory.mktemp("workloads")
    built = build(bundle.write(work), "wdf_workloads.main", work / "built").component
    warm(built, work / "cache")
    return built, work / "cache"


def run_mode(component, tmp_path, mode, args, timeout_s=TIMEOUT_S):
    source = tmp_path / "input.json"
    source.write_text(canonical({"args": {"mode": mode, **args}, "seed": 1, "workload": "fault"}))
    ran = run(component[0], source, tmp_path / "guest", MEMORY_MIB, timeout_s, component[1])
    print(f"{mode}: {ran.classified} heartbeats {ran.heartbeats}")
    return ran


@pytest.mark.parametrize("mode", fault.CLASSES)
def test_every_fault_mode_has_its_class(mode, component, tmp_path):
    documented = fault.CLASSES[mode]
    assert documented in SIMULATE
    if mode == "memory_kill":
        # Exit 137 with a node OOM event comes from the container, which only a pod can show.
        classified = classify(137, "", None, Facts(oom_event=True))
        print(f"{mode}: {classified}")
        assert classified.cls == documented
        return
    timeout_s = HANG_TIMEOUT_S if mode == "hang" else TIMEOUT_S
    ran = run_mode(component, tmp_path, mode, ARGS.get(mode, {}), timeout_s)
    assert ran.classified.cls == documented, ran.tail.decode(errors="replace")


DETAIL = {
    "exit_nonzero": (0, "fault-exit-nonzero: the fault workload exits nonzero"),
    "fail_late": (5, "fault-fail-late: failed at progress 0.5"),
    "memory_kill": (0, "fault-memory-kill-survived: held 64 MiB and was not killed"),
    "nope": (0, "unknown-fault-mode: no fault mode named nope"),
}


@pytest.mark.parametrize("mode", DETAIL)
def test_fault_mode_reports_its_detail(mode, component, tmp_path):
    """A mode that fails on its own names itself in a deterministic reason, and a memory_kill that
    no container stops fails rather than passing."""
    heartbeats, detail = DETAIL[mode]
    ran = run_mode(component, tmp_path, mode, ARGS.get(mode, {}))
    assert ran.classified == ("guest_failed", True, f"guest_failed: {detail}")
    assert ran.heartbeats == heartbeats
