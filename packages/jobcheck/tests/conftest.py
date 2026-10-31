import copy
import json
from pathlib import Path

import pytest
from wdf_jobdoc import parse_document
from wdf_jobdoc.profile_files import load_profile

ROOT = Path(__file__).resolve().parents[3]
VECTORS = ROOT / "contracts/job/vectors"

HASH = "a" * 64
SOURCE = {
    "uri": f"s3://wdf-sources/sha256/aa/{HASH}",
    "sha256": HASH,
    "size": 2048,
    "name": "bundle.tar.gz",
    "kind": "source",
}
GOOD = {
    "schema": "urn:wdf:schema:job:1.0",
    "parameters": {"seed": "seed"},
    "tasks": {
        "build": {"type": "compile", "params": {"source": SOURCE, "entry": "sim"}},
        "sim": {
            "type": "simulate",
            "inputs": [{"task": "build", "output": "component"}],
            "cpu": 0.25,
            "memory_mib": 384,
            "run_timeout_s": 600,
            "retries": 1,
            "params": {"workload": "pi", "args": {"n": 1000}, "seed": {"param": "seed"}},
        },
        "agg": {
            "type": "aggregate",
            "inputs": [{"task": "sim", "output": "result"}],
            "cpu": 1,
            "memory_mib": 512,
            "run_timeout_s": 600,
            "params": {"aggregation": "summary_stats", "args": {"blocks": ["pi"]}},
        },
    },
}


@pytest.fixture
def good():
    """A valid document as a mutable dict, fresh for each test."""
    return copy.deepcopy(GOOD)


@pytest.fixture
def parse():
    """The Document of a dict, through the strict loader."""
    return lambda raw: parse_document(json.dumps(raw).encode(), 1 << 20)


@pytest.fixture(scope="session")
def profiles():
    """The limits profiles by name, with the pool table of the slots fixture."""
    fixture = VECTORS / "slots-fixture"
    return {name: load_profile(name, ROOT, fixture) for name in ("ci", "permissive")}
