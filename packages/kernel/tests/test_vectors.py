"""The golden vectors hold the kernel to the values it gave when they were written, and the level 0
vectors of the workload contract to their formulas."""

import json
from pathlib import Path

import pytest
from wdf_kernel.expected import expected_blocks, expected_digest
from wdf_kernel.rows import WORKLOADS

ROOT = Path(__file__).resolve().parents[3]
VECTORS = json.loads((Path(__file__).parent / "vectors" / "workloads.json").read_text())
LEVEL0 = sorted((ROOT / "contracts" / "workload" / "vectors" / "level0").iterdir())


def jsonable(blocks: list[dict]) -> list[dict]:
    """Blocks as the vectors write them, a hex value as its text."""
    return [
        {
            **block,
            "rows": [
                {k: v.hex() if isinstance(v, bytes) else v for k, v in row.items()}
                for row in block["rows"]
            ],
        }
        for block in blocks
    ]


@pytest.mark.parametrize("vector", VECTORS, ids=lambda v: f"{v['input']['workload']}-{v['digest']}")
def test_golden_vectors(vector):
    workload, seed, args = (vector["input"][k] for k in ("workload", "seed", "args"))
    assert expected_digest(workload, seed, args) == vector["digest"]
    if "blocks" in vector:
        assert jsonable(expected_blocks(workload, seed, args)) == vector["blocks"]


@pytest.mark.parametrize("directory", LEVEL0, ids=lambda d: d.name)
def test_golden_level0_contract_vectors(directory):
    params = json.loads((directory / "input.json").read_text())
    expected = json.loads((directory / "expected.json").read_text())
    assert expected["seed"] == params["seed"]
    assert (
        jsonable(expected_blocks(params["workload"], params["seed"], params["args"]))
        == expected["blocks"]
    )


def test_golden_vectors_cover_every_workload():
    assert {v["input"]["workload"] for v in VECTORS} == set(WORKLOADS)
