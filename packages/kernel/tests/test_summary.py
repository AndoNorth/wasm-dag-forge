"""expected_summary over inputs keyed by task id, python -m wdf_kernel.check summary, and the
level 1 fixtures that the summaries and the calibration run."""

import hashlib
import json
import statistics
import subprocess
import sys
from importlib.resources import files

import pytest
from wdf_kernel import check
from wdf_kernel.expected import expected_digest
from wdf_kernel.summary import expected_summary

LEVEL1 = files("wdf_kernel") / "fixtures/level1"
WORKLOADS = ("noop", "cpu_burn", "mem_hold", "io_churn")


def canonical(document) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def fixture(workload: str) -> dict:
    return json.loads((LEVEL1 / workload / "input.json").read_text())


def sum_squares(n: int) -> dict:
    return {"workload": "sum_squares", "seed": 1, "args": {"n": n}}


def write_inputs(tmp_path, inputs: dict) -> list[str]:
    """Writes each input.json and returns the TASK=PATH arguments of the check."""
    pairs = []
    for task, params in inputs.items():
        path = tmp_path / f"{task}.json"
        path.write_text(canonical(params))
        pairs.append(f"{task}={path}")
    return pairs


def run_check(tmp_path, summary: dict, pairs: list[str], missing=()) -> int:
    path = tmp_path / "summary.json"
    path.write_text(canonical(summary))
    argv = ["summary", "--summary", str(path), "--inputs", *pairs]
    if missing:
        argv += ["--missing", *missing]
    return check.main(argv)


def test_summary_stats_of_scalars_by_hand():
    """Three sum_squares inputs give the values 1, 5 and 14, and an echo input adds rows only."""
    inputs = {
        "t1": sum_squares(1),
        "t2": sum_squares(2),
        "t3": sum_squares(3),
        "t4": {"workload": "echo", "seed": 0, "args": {"a": 1, "b": "x"}},
    }
    summary = expected_summary("summary_stats", {}, inputs)
    assert summary == {
        "schema": "wdf.summary/1",
        "aggregation": "summary_stats",
        "args": {},
        "inputs": 4,
        "missing": [],
        "rows": 5,
        "blocks": {
            "result": {
                "value": {
                    "count": 3,
                    "mean": 20 / 3,
                    "std": statistics.stdev([1, 5, 14]),
                    "min": 1,
                    "max": 14,
                }
            }
        },
    }


def test_summary_std_is_null_for_one_value():
    summary = expected_summary("summary_stats", {}, {"t1": sum_squares(2)})
    assert summary["blocks"]["result"]["value"] == {
        "count": 1,
        "mean": 5.0,
        "std": None,
        "min": 5,
        "max": 5,
    }


def test_summary_stats_blocks_and_columns_select():
    inputs = {"t1": fixture("mem_hold"), "t2": fixture("cpu_burn")}
    every = expected_summary("summary_stats", {}, inputs)
    assert set(every["blocks"]) == {"held", "series"}
    assert set(every["blocks"]["series"]) == {"step", "value"}
    held = expected_summary("summary_stats", {"blocks": ["held"]}, inputs)
    assert held["blocks"] == {"held": every["blocks"]["held"]}
    assert held["rows"] == every["rows"] == 1 + 50 + 100
    values = expected_summary("summary_stats", {"columns": ["value"]}, inputs)
    assert values["blocks"] == {
        "held": every["blocks"]["held"],
        "series": {"value": every["blocks"]["series"]["value"]},
    }


def test_summary_digest_list_in_task_id_order():
    inputs = {"t2": fixture("io_churn"), "t1": fixture("cpu_burn"), "t0": fixture("noop")}
    summary = expected_summary("digest_list", {}, inputs)
    assert summary["digests"] == [
        {"task": task, "seed": inputs[task]["seed"], "digest": expected_digest(**inputs[task])}
        for task in ("t0", "t1", "t2")
    ]
    assert summary["rows"] == 100 + 1024
    assert "blocks" not in summary


@pytest.mark.parametrize(
    "aggregation,args",
    [("histogram", {}), ("digest_list", {"blocks": ["a"]}), ("summary_stats", {"blocks": "a"})],
)
def test_summary_refuses_an_unknown_aggregation_or_argument(aggregation, args):
    with pytest.raises(ValueError):
        expected_summary(aggregation, args, {})


def test_missing_inputs_listed_when_partial(tmp_path):
    inputs = {"t1": fixture("cpu_burn"), "t2": None, "t0": None}
    summary = expected_summary("summary_stats", {}, inputs)
    assert (summary["inputs"], summary["missing"], summary["rows"]) == (3, ["t0", "t2"], 100)
    alone = expected_summary("summary_stats", {}, {"t1": inputs["t1"]})
    assert summary["blocks"] == alone["blocks"]
    pairs = write_inputs(tmp_path, {"t1": inputs["t1"]})
    assert run_check(tmp_path, summary, pairs, ["t2", "t0"]) == 0
    assert run_check(tmp_path, summary, pairs, ["t2"]) == 1
    assert run_check(tmp_path, summary, pairs) == 1


def test_summary_tolerance_is_relative_1e9(tmp_path, capsys):
    """mean and std pass within a relative 1e-9 and fail beyond it, and count, min and max are
    exact. A mean near 1e9 shows the tolerance is relative: an absolute 1e-9 would refuse it."""
    inputs = {"t1": fixture("cpu_burn"), "t2": fixture("io_churn"), "t3": sum_squares(1500)}
    pairs = write_inputs(tmp_path, inputs)
    summary = expected_summary("summary_stats", {}, inputs)
    assert summary["blocks"]["result"]["value"]["mean"] > 1e9

    def nudged(block: str, column: str, stat: str, factor: float) -> dict:
        changed = json.loads(json.dumps(summary))
        changed["blocks"][block][column][stat] *= factor
        return changed

    assert run_check(tmp_path, summary, pairs) == 0
    for block, column in (("series", "value"), ("payload", "step"), ("result", "value")):
        for stat in ("mean", "std") if block != "result" else ("mean",):
            assert run_check(tmp_path, nudged(block, column, stat, 1 + 5e-10), pairs) == 0
            assert run_check(tmp_path, nudged(block, column, stat, 1 + 2e-9), pairs) == 1
    assert run_check(tmp_path, nudged("series", "value", "min", 1 + 1e-15), pairs) == 1
    assert run_check(tmp_path, nudged("series", "value", "max", 1 - 1e-15), pairs) == 1
    counted = json.loads(json.dumps(summary))
    counted["blocks"]["series"]["step"]["count"] += 1
    assert run_check(tmp_path, counted, pairs) == 1
    assert "differs: block series column step count" in capsys.readouterr().err


def test_check_summary_prints_the_digest_of_the_summary(tmp_path):
    inputs = {"t1": fixture("cpu_burn"), "t2": fixture("io_churn")}
    pairs = write_inputs(tmp_path, inputs)
    summary = tmp_path / "summary.json"
    summary.write_text(canonical(expected_summary("summary_stats", {}, inputs)))
    argv = [sys.executable, "-m", "wdf_kernel.check", "summary", "--summary", str(summary)]
    result = subprocess.run([*argv, "--inputs", *pairs], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"digest: {hashlib.sha256(summary.read_bytes()).hexdigest()}\n"


@pytest.mark.parametrize(
    "extra",
    [["--inputs", "t1"], ["--inputs", "t1=nowhere.json"], ["--missing", "t1"]],
    ids=["no-path", "no-file", "input-and-missing"],
)
def test_check_summary_usage_exits_2(tmp_path, extra):
    inputs = {"t1": sum_squares(2)}
    pairs = write_inputs(tmp_path, inputs)
    summary = tmp_path / "summary.json"
    summary.write_text(canonical(expected_summary("summary_stats", {}, inputs)))
    argv = ["summary", "--summary", str(summary)]
    argv += extra if extra[0] == "--inputs" else ["--inputs", *pairs, *extra]
    assert check.main(argv) == 2


def test_summary_level1_fixtures_cover_every_level1_workload():
    assert sorted(entry.name for entry in LEVEL1.iterdir()) == sorted(WORKLOADS)


@pytest.mark.parametrize("workload", WORKLOADS)
def test_summary_level1_fixture_digest(workload):
    """Each input is canonical and names its workload, and its expected digest is the kernel's."""
    text = (LEVEL1 / workload / "input.json").read_text()
    params = json.loads(text)
    assert text == canonical(params)
    assert params["workload"] == workload
    assert set(params) == {"args", "seed", "workload"}
    assert "heartbeat_every" in params["args"]
    expected = json.loads((LEVEL1 / workload / "expected.json").read_text())
    assert expected == {"digest": expected_digest(**params)}
