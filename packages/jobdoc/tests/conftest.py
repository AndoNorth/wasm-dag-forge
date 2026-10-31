import copy
import json
from pathlib import Path

import pytest
import yaml
from wdf_jobdoc import DocumentInvalid, parse_document

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "contracts/job"
# The `ci` document limit of the limits profile, 1 MiB.
CI_DOCUMENT_LIMIT = 1024 * 1024

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


def error_codes() -> set[str]:
    return set(yaml.safe_load((CONTRACT / "errors.yaml").read_text())["codes"])


@pytest.fixture
def root():
    return ROOT


@pytest.fixture
def contract():
    return CONTRACT


@pytest.fixture
def ci_document_limit():
    return CI_DOCUMENT_LIMIT


@pytest.fixture
def good():
    """A valid document as a mutable dict, fresh for each test."""
    return copy.deepcopy(GOOD)


@pytest.fixture
def load():
    """Parses a dict or bytes with the ci limit, and checks every issue code is in the table."""

    def run(document, max_bytes=CI_DOCUMENT_LIMIT):
        data = document if isinstance(document, bytes) else json.dumps(document).encode()
        try:
            return parse_document(data, max_bytes)
        except DocumentInvalid as exc:
            assert {i.code for i in exc.issues} <= error_codes()
            raise

    return run


@pytest.fixture
def issues(load):
    """The issues of a document that must be refused, as (code, path) pairs."""

    def run(document, max_bytes=CI_DOCUMENT_LIMIT):
        with pytest.raises(DocumentInvalid) as info:
            load(document, max_bytes)
        return [(i.code, i.path) for i in info.value.issues]

    return run
