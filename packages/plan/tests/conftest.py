import copy
import json
from pathlib import Path

import pytest
from wdf_jobdoc import parse_document
from wdf_jobdoc.profile_files import load_profile

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "contracts/job"
SLOTS_FIXTURE = CONTRACT / "vectors/slots-fixture"

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


def _profile(name="ci"):
    return load_profile(name, ROOT, SLOTS_FIXTURE)


def _document(data):
    raw = data if isinstance(data, bytes) else json.dumps(data).encode()
    return parse_document(raw, _profile().max_document_bytes)


@pytest.fixture(scope="session")
def root():
    return ROOT


@pytest.fixture(scope="session")
def profile_of():
    """The limits profile `name` over the slots fixture, so a live slots edit changes no result."""
    return _profile


@pytest.fixture(scope="session")
def parse():
    """The Document of a dict or bytes, parsed with the ci document limit."""
    return _document


@pytest.fixture(scope="session")
def plan_of():
    """The plan of a dict or bytes under the named profile, `ci` by default."""
    from wdf_plan import compile_plan

    return lambda data, name="ci": compile_plan(_document(data), _profile(name))


@pytest.fixture(scope="session")
def valid_documents():
    """Every document the tests compile: the one built here and each valid vector."""
    found = {"good": GOOD}
    for path in sorted((CONTRACT / "vectors/valid").glob("*.json")):
        found[path.stem] = json.loads(path.read_text())
    return found


@pytest.fixture
def good():
    return copy.deepcopy(GOOD)


def _build(task_id="build", source=SOURCE):
    return {"type": "compile", "params": {"source": source, "entry": "sim"}}


def _sim(build="build", timeout=100, retries=0, **extra):
    return {
        "type": "simulate",
        "inputs": [{"task": build, "output": "component"}],
        "cpu": 0.25,
        "memory_mib": 384,
        "run_timeout_s": timeout,
        "retries": retries,
        "params": {"workload": "pi", "seed": 1},
    } | extra


def _agg(*sims, timeout=600):
    return {
        "type": "aggregate",
        "inputs": [{"task": s, "output": "result"} for s in sims],
        "cpu": 1,
        "memory_mib": 512,
        "run_timeout_s": timeout,
        "params": {"aggregation": "digest_list"},
    }


@pytest.fixture(scope="session")
def shapes():
    """Documents of the shapes the stats are checked on, each a dict keyed by shape name."""
    index = {**SOURCE, "kind": "index", "uri": f"s3://wdf-sources/sha256/aa/{HASH}"}
    schema = "urn:wdf:schema:job:1.0"
    return {
        "chain": GOOD,
        "fan-out": {
            "schema": schema,
            "tasks": {"build": _build()} | {f"s{i}": _sim() for i in range(8)},
        },
        "fan-in": {
            "schema": schema,
            "tasks": {f"b{i}": _build() for i in range(8)}
            | {f"s{i}": _sim(f"b{i}") for i in range(8)}
            | {"agg": _agg(*(f"s{i}" for i in range(8)))},
        },
        "aggregate-only": {
            "schema": schema,
            "tasks": {
                "agg": {
                    "type": "aggregate",
                    "cpu": 1,
                    "memory_mib": 512,
                    "run_timeout_s": 600,
                    "params": {"aggregation": "digest_list", "index": index},
                }
            },
        },
        "depends-only": {
            "schema": schema,
            "tasks": {
                "b1": _build(),
                "b2": _build(),
                "s1": _sim("b1", depends_on=["b2"]),
                "s2": _sim("b2"),
            },
        },
    }


@pytest.fixture(scope="session")
def ci(profile_of):
    return profile_of("ci")
