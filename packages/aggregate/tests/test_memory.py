"""The peak memory of one aggregate over a result at the staging cap stays inside the aggregate
slot, measured in its own process so the test runner's memory is not counted."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from wdf_aggregate.read import max_bytes

ROOT = Path(__file__).resolve().parents[3]
SLOTS = ROOT / "deploy/presets/slots/ci.yaml"
HEAD = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<results schema="wdf.sim.v1" seed="3">\n'
    b'<block id="b" kind="scalar">\n'
)
# The smallest valid row, so the cap holds the most rows a result can carry.
ROW = b'<row name="" value="0"/>\n'
TAIL = b"</block>\n</results>\n"


def slot_mib() -> float:
    memory = yaml.safe_load(SLOTS.read_text())["pools"]["aggregate"]["limits"]["memory"]
    units = {"Mi": 1, "Gi": 1024}
    return float(memory[:-2]) * units[memory[-2:]]


@pytest.fixture(scope="module")
def cap_sized(tmp_path_factory) -> tuple[Path, int]:
    count = (max_bytes() - len(HEAD) - len(TAIL)) // len(ROW)
    path = tmp_path_factory.mktemp("cap") / "results.xml"
    with path.open("wb") as sink:
        sink.write(HEAD)
        sink.write(ROW * count)
        sink.write(TAIL)
    return path, count


@pytest.mark.slow
@pytest.mark.parametrize("aggregation", ["summary_stats", "digest_list"])
def test_cap_sized_result_peak(tmp_path, cap_sized, aggregation):
    path, count = cap_sized
    size = path.stat().st_size
    assert max_bytes() - len(ROW) < size <= max_bytes()

    result = subprocess.run(
        [
            sys.executable, "-m", "wdf_aggregate", "files",
            "--aggregation", aggregation,
            "--args", "{}",
            "--out", str(tmp_path / "out"),
            f"t1={path}",
        ],
        capture_output=True,
        text=True,
    )  # fmt: skip

    assert result.returncode == 0, result.stderr
    peak = float(result.stdout.split("peak_rss_mib: ")[1].split()[0])
    print(f"rows: {count}, bytes: {size}, peak_rss_mib: {peak}, slot_mib: {slot_mib()}")
    assert json.loads((tmp_path / "out/summary.json").read_text())["rows"] == count
    assert peak < slot_mib()
