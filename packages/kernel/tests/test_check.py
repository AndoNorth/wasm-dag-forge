"""python -m wdf_kernel.check results compares parsed values with the blocks of the input."""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from wdf_kernel import check, expected
from wdf_kernel.writer import KINDS, write

LEVEL0 = Path(__file__).resolve().parents[3] / "contracts/workload/vectors/level0"
DIGEST = "0123456789abcdef"


def golden(workload: str) -> tuple[Path, dict]:
    """The input of a level 0 vector and its expected values, hex as bytes as the writer takes."""
    document = json.loads((LEVEL0 / workload / "expected.json").read_text())
    for block in document["blocks"]:
        for row in block["rows"]:
            for name, column_type in KINDS[block["kind"]].items():
                if column_type == "hex":
                    row[name] = bytes.fromhex(row[name])
    return LEVEL0 / workload / "input.json", document


@pytest.fixture
def kernel(monkeypatch):
    """Stands in the level 0 golden values for the kernel's generators, which this check calls."""

    def blocks(workload, seed, args):
        input_path, document = golden(workload)
        params = json.loads(input_path.read_text())
        assert (seed, args) == (params["seed"], params["args"])
        return document["blocks"]

    monkeypatch.setattr(expected, "expected_blocks", blocks)
    monkeypatch.setattr(expected, "expected_digest", lambda workload, seed, args: DIGEST)


def results_file(tmp_path: Path, seed: int, blocks: list) -> Path:
    sink = io.BytesIO()
    write(sink, seed, blocks)
    path = tmp_path / "results.xml"
    path.write_bytes(sink.getvalue())
    return path


@pytest.mark.parametrize("workload", ["echo", "hash_chain", "sum_squares"])
def test_check_results_equal_prints_the_digest(kernel, tmp_path, capsys, workload):
    input_path, document = golden(workload)
    results = results_file(tmp_path, document["seed"], document["blocks"])
    assert check.main(["results", "--input", str(input_path), "--results", str(results)]) == 0
    assert capsys.readouterr().out == f"digest: {DIGEST}\n"


def test_check_results_reads_values_not_bytes(kernel, tmp_path):
    input_path, _ = golden("sum_squares")
    results = tmp_path / "results.xml"
    results.write_text(
        "<results seed='3' schema='wdf.sim.v1'><!-- by hand -->\n"
        "<block kind='scalar' id='result'><row value='338350' name='sum_&#x73;quares'></row>"
        "</block></results>"
    )
    assert check.main(["results", "--input", str(input_path), "--results", str(results)]) == 0


@pytest.mark.parametrize(
    "seed,value,message",
    [
        (3, 338351, "differs: block result row 0"),
        (4, 338350, "differs: the seed is 4, expected 3"),
    ],
)
def test_check_results_not_equal(kernel, tmp_path, capsys, seed, value, message):
    input_path, _ = golden("sum_squares")
    blocks = [{"id": "result", "kind": "scalar", "rows": [{"name": "sum_squares", "value": value}]}]
    results = results_file(tmp_path, seed, blocks)
    assert check.main(["results", "--input", str(input_path), "--results", str(results)]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith(message)
    assert captured.out == ""


def test_check_results_refuses_a_broken_document_with_its_reason(kernel, tmp_path, capsys):
    input_path, _ = golden("sum_squares")
    results = tmp_path / "results.xml"
    results.write_text('<!DOCTYPE results><results schema="wdf.sim.v1" seed="3"/>')
    assert check.main(["results", "--input", str(input_path), "--results", str(results)]) == 1
    assert capsys.readouterr().err.startswith("reason: doctype-present: ")


def test_check_results_max_rows_refuses_before_generating(tmp_path, capsys):
    input_path, document = golden("echo")
    results = results_file(tmp_path, document["seed"], document["blocks"])
    argv = ["results", "--input", str(input_path), "--results", str(results), "--max-rows", "3"]
    assert check.main(argv) == 2
    assert "4 rows, above --max-rows 3" in capsys.readouterr().err


def test_check_results_missing_file_is_usage(tmp_path, capsys):
    argv = ["results", "--input", str(tmp_path / "none.json"), "--results", str(tmp_path / "r.xml")]
    assert check.main(argv) == 2
    assert capsys.readouterr().err.startswith("usage: FileNotFoundError")


def test_check_summary_is_dispatched_to_the_summary_module(monkeypatch):
    seen = []
    monkeypatch.setattr(check.summary, "main", lambda argv: seen.append(argv) or 0)
    assert check.main(["summary", "--summary", "s.json", "--inputs", "t=a.json"]) == 0
    assert seen == [["--summary", "s.json", "--inputs", "t=a.json"]]


@pytest.mark.parametrize("argv", [[], ["results"], ["other"]])
def test_check_usage_exits_2(argv):
    run = subprocess.run(
        [sys.executable, "-m", "wdf_kernel.check", *argv], capture_output=True, text=True
    )
    assert run.returncode == 2, run.stderr
