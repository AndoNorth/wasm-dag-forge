"""Loader tests of the cluster contracts, run by `just test` and so by `just ci`."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
# Each contract with the real files that must also load: the presets and the pinned table.
CONTRACTS = {
    "contracts/preset": "deploy/presets/*.yaml",
    "contracts/module": "deploy/**/module.yaml",
    "contracts/topology": "contracts/topology/topology.yaml",
}


@pytest.mark.parametrize("contract", CONTRACTS)
def test_vectors_keep_their_result(contract):
    real = sorted(str(p.relative_to(ROOT)) for p in ROOT.glob(CONTRACTS[contract]))
    run = subprocess.run(
        [ROOT / "scripts/verify/cluster/lib/vectors.sh", contract, *real],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr
