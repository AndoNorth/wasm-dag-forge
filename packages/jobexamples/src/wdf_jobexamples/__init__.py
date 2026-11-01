"""The example library, the parameter vectors and the generator."""

import json
from pathlib import Path

__all__ = ["PERMISSIVE_MAX_TASKS", "examples", "generate", "load", "seed_list"]

VECTORS = Path(__file__).resolve().parents[4] / "contracts/job/vectors"
VALID = VECTORS / "valid"

SCHEMA = "urn:wdf:schema:job:1.0"
# The task ceiling of the permissive profile, the largest job a generated document may hold.
PERMISSIVE_MAX_TASKS = 51_200
SHAPES = {"fan-out": lambda n: n + 2, "fan-in": lambda n: 2 * n + 1, "chains": lambda n: 3 * n}

_BUNDLE = "a" * 64
_SOURCE = {
    "uri": f"s3://wdf-sources/sha256/{_BUNDLE[:2]}/{_BUNDLE}",
    "sha256": _BUNDLE,
    "size": 2048,
    "name": "bundle.tar.gz",
    "kind": "source",
}


def examples() -> list[str]:
    """The names of the valid example documents, sorted."""
    return sorted(path.stem for path in VALID.glob("*.json"))


def load(name: str) -> bytes:
    """The bytes of the example document `name`."""
    if name not in examples():
        raise KeyError(f"no example named {name!r}, the examples are {', '.join(examples())}")
    return (VALID / f"{name}.json").read_bytes()


def _dump(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _build() -> dict:
    return {"type": "compile", "params": {"source": _SOURCE, "entry": "sim"}}


def _simulate(build: str, seed: int, retries: int) -> dict:
    return {
        "type": "simulate",
        "inputs": [{"task": build, "output": "component"}],
        "cpu": 0.25,
        "memory_mib": 384,
        "run_timeout_s": 600,
        "retries": retries,
        "params": {"workload": "pi", "args": {"steps": 1000}, "seed": seed},
    }


def _aggregate(sims: list[str]) -> dict:
    return {
        "type": "aggregate",
        "inputs": [{"task": s, "output": "result"} for s in sims],
        "cpu": 1,
        "memory_mib": 512,
        "run_timeout_s": 600,
        "params": {"aggregation": "digest_list"},
    }


def generate(shape: str, chains: int, *, retries: int = 0) -> bytes:
    """A job document of `chains` simulations in the named shape, the same bytes for the same call.

    `fan-out` is one compile task feeding `chains` simulate tasks and one aggregate over them,
    `fan-in` gives each simulate task its own compile task before the one aggregate, and `chains`
    is `chains` independent compile, simulate and aggregate chains.
    Simulate seeds are the chain numbers, so every simulation of a job differs.
    """
    if shape not in SHAPES:
        raise ValueError(f"unknown shape {shape!r}, the shapes are {', '.join(SHAPES)}")
    if chains < 1:
        raise ValueError(f"a job needs at least 1 chain, got {chains}")
    count = SHAPES[shape](chains)
    if count > PERMISSIVE_MAX_TASKS:
        raise ValueError(
            f"{shape} with {chains} chains is {count} tasks, "
            f"more than the permissive limit of {PERMISSIVE_MAX_TASKS:,} tasks"
        )
    width = len(str(chains - 1))
    names = [f"{i:0{width}d}" for i in range(chains)]
    tasks = {}
    if shape == "fan-out":
        tasks["build"] = _build()
    for i, n in enumerate(names):
        build = "build" if shape == "fan-out" else f"build-{n}"
        if shape != "fan-out":
            tasks[build] = _build()
        tasks[f"sim-{n}"] = _simulate(build, i, retries)
        if shape == "chains":
            tasks[f"agg-{n}"] = _aggregate([f"sim-{n}"])
    if shape != "chains":
        tasks["agg"] = _aggregate([f"sim-{n}" for n in names])
    return _dump({"schema": SCHEMA, "tasks": tasks})


def seed_list(count: int, start: int = 0) -> bytes:
    """`count` parameter blocks for `batch-template`, one per line, from seed `start`."""
    if count < 0:
        raise ValueError(f"a seed list has a count of 0 or more, got {count}")
    return b"".join(
        _dump({"seed": seed, "parameters": {"steps": 1000 + seed}})
        for seed in range(start, start + count)
    )
