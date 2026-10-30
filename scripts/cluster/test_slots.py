"""Pod slot tests, run by `just ci`: requests equal limits in every preset."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PRESETS = ["ci", "dev", "soak"]


def slots(preset):
    return subprocess.run(
        [ROOT / "scripts/cluster/slots.sh", preset], cwd=ROOT, capture_output=True, text=True
    )


@pytest.mark.parametrize("preset", PRESETS)
def test_slots_have_requests_equal_to_limits(preset):
    run = slots(preset)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "slot compile:" in run.stdout and "slot aggregate:" in run.stdout


def test_unequal_requests_are_refused():
    target = ROOT / "deploy/presets/slots/ci.yaml"
    original = target.read_text()
    try:
        target.write_text(
            original.replace(
                'requests: {cpu: "1", memory: 1Gi}', 'requests: {cpu: "1", memory: 512Mi}', 1
            )
        )
        run = slots("ci")
    finally:
        target.write_text(original)
    assert run.returncode == 1
    assert "differs from limit" in run.stderr


def test_slot_that_fits_the_allocatable_but_not_after_daemons_is_refused():
    # 1,160 MiB fits the 1,184 MiB allocatable but not the 1,134 MiB left after daemons.
    target = ROOT / "deploy/presets/slots/ci.yaml"
    original = target.read_text()
    try:
        target.write_text(original.replace("memory: 1Gi", "memory: 1160Mi"))
        run = slots("ci")
    finally:
        target.write_text(original)
    assert run.returncode == 1
    assert "DOES NOT FIT" in run.stdout
