"""The aggregate over level 1 results written by the kernel writer, as local files and through a
store."""

import hashlib
import json
from pathlib import Path

import pytest
import wdf_aggregate.__main__ as cli
from wdf_aggregate.run import run
from wdf_artifactclient.aggregate import put_index
from wdf_artifactclient.bench import ListCounter
from wdf_artifactclient.client import ArtifactClient, RetryPolicy
from wdf_artifactclient.models import IndexEntry
from wdf_events.models import ProducerRef
from wdf_kernel.expected import expected_digest
from wdf_kernel.rows import blocks
from wdf_kernel.writer import write
from wdf_runtime.contract import check

JOB = "123e4567-e89b-42d3-a456-426614174000"
TENANT = "acme"
INPUTS = {
    "t1": {"workload": "cpu_burn", "seed": 7, "args": {"units": 40}},
    "t2": {"workload": "mem_hold", "seed": 11, "args": {"mib": 2, "units": 25}},
    "t3": {"workload": "noop", "seed": 3, "args": {}},
}


def results(directory: Path) -> dict[str, Path]:
    paths = {}
    for task, params in INPUTS.items():
        path = directory / f"{task}.xml"
        with path.open("wb") as sink:
            write(sink, params["seed"], blocks(params["workload"], params["seed"], params["args"]))
        paths[task] = path
    return paths


def files(out: Path, aggregation: str, args: dict, pairs: list[str], *extra: str) -> Path:
    argv = ["files", "--aggregation", aggregation, "--args", json.dumps(args), "--out", str(out)]
    assert cli.main([*argv, *extra, *pairs]) == 0
    summary = out / "summary.json"
    check(summary)
    return summary


@pytest.mark.parametrize("aggregation", ["summary_stats", "digest_list"])
def test_output_bytes_independent_of_arrival_order(tmp_path, aggregation):
    pairs = [f"{task}={path}" for task, path in results(tmp_path).items()]
    first = files(tmp_path / "a", aggregation, {}, pairs)
    second = files(tmp_path / "b", aggregation, {}, pairs[::-1])
    assert first.read_bytes() == second.read_bytes()
    assert hashlib.sha256(first.read_bytes()).hexdigest()


def test_digest_list_gives_the_kernel_digest_of_each_input_in_task_order(tmp_path):
    pairs = [f"{task}={path}" for task, path in results(tmp_path).items()]
    summary = json.loads(files(tmp_path / "s", "digest_list", {}, pairs[::-1]).read_text())
    assert summary["digests"] == [
        {"task": task, "seed": p["seed"], "digest": expected_digest(**p)}
        for task, p in sorted(INPUTS.items())
    ]
    assert summary["rows"] == 40 + 1 + 25


def test_summary_stats_selects_blocks_and_lists_missing_inputs(tmp_path):
    paths = results(tmp_path)
    pairs = [f"{task}={paths[task]}" for task in ("t1", "t2")]
    out = files(tmp_path / "s", "summary_stats", {"blocks": ["series"]}, pairs, "--missing", "t0")
    summary = json.loads(out.read_text())
    assert summary["inputs"] == 3 and summary["missing"] == ["t0"]
    assert list(summary["blocks"]) == ["series"]
    assert summary["blocks"]["series"]["step"]["count"] == 40 + 25
    assert summary["blocks"]["series"]["step"]["max"] == 39


def test_a_malformed_result_is_a_parse_failure_naming_its_task(tmp_path, capsys):
    paths = results(tmp_path)
    paths["t2"].write_bytes(paths["t2"].read_bytes()[:-20])
    pairs = [f"{task}={path}" for task, path in paths.items()]
    argv = ["files", "--aggregation", "summary_stats", "--args", "{}", "--out", str(tmp_path / "s")]
    assert cli.main([*argv, *pairs]) == cli.EXIT_CODES["aggregate_parse_failed"]
    last = capsys.readouterr().err.splitlines()[-1]
    assert last.startswith("reason: aggregate_parse_failed: t2: ")


@pytest.mark.integration
def test_run_through_store_index(store_session, tmp_path):
    store_session.store().create_buckets()
    policy = RetryPolicy(sleep=lambda seconds: None)

    def role(name: str) -> ArtifactClient:
        return ArtifactClient(store_session.endpoint, store_session.credentials[name], policy)

    simulate, local = role("simulate"), results(tmp_path)
    entries = []
    for i, task in enumerate(sorted(INPUTS)):
        who = ProducerRef(job=JOB, task=task, attempt="r0a1")
        reference = simulate.put_attempt(
            local[task], "result", "results.xml", who, TENANT, "aggregate-store", i
        )
        entries.append(IndexEntry(task=task, status="succeeded", reference=reference))
    header = {"job": JOB, "tenant": TENANT, "task": "aggregate-0", "partial": "refuse"}
    index = put_index(role("indexer"), entries, header)

    aggregate = role("aggregate")
    counter = ListCounter(aggregate)
    stored = run(aggregate, index, TENANT, "summary_stats", {}, tmp_path / "store")

    pairs = [f"{task}={path}" for task, path in local.items()]
    assert stored.read_bytes() == files(tmp_path / "local", "summary_stats", {}, pairs).read_bytes()
    assert json.loads(stored.read_text())["inputs"] == len(INPUTS)
    assert counter.calls == 0
