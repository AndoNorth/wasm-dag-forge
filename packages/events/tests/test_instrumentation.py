"""The instrumentation contract: its vectors through the loader, and the pinned metrics file."""

import json
import re
import subprocess
from pathlib import Path

import yaml
from jsonschema import validate

ROOT = Path(__file__).resolve().parents[3]
DIR = ROOT / "contracts/instrumentation"
SCHEMA = json.loads((DIR / "metrics.schema.json").read_text())
METRICS = yaml.safe_load((DIR / "metrics.yaml").read_text())["metrics"]
NAME = re.compile(r"^wdf_[a-z][a-z0-9]*(_[a-z0-9]+)+$")


def check(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run([DIR / "check.sh", path], capture_output=True, text=True)


def test_vectors_keep_their_result():
    vectors = ROOT / "scripts/verify/cluster/lib/vectors.sh"
    run = subprocess.run(
        [vectors, "contracts/instrumentation", "contracts/instrumentation/metrics.yaml"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_the_file_validates_against_its_schema():
    validate({"metrics": METRICS}, SCHEMA)


def test_the_file_defines_the_metrics_of_the_event_contract():
    labels = {name: set(metric["labels"]) for name, metric in METRICS.items()}
    assert labels == {
        "wdf_event_bytes": set(),
        "wdf_event_size_warnings_total": set(),
        "wdf_event_rejected_total": {"reason"},
        "wdf_event_upgraded_total": {"type", "from_major"},
    }


def test_each_name_follows_the_rule():
    for name, metric in METRICS.items():
        assert NAME.match(name), name
        assert name.endswith("_total") == (metric["type"] == "counter"), name
        if "unit" in metric:
            assert name.removesuffix("_total").endswith("_" + metric["unit"]), name


def test_each_label_declares_its_values_and_is_not_denied():
    denied = set(SCHEMA["x-label-denylist"])
    for name, metric in METRICS.items():
        for label, declared in metric["labels"].items():
            assert label not in denied, (name, label)
            assert "values" in declared or "values_from" in declared, (name, label)


def test_a_copy_with_a_jobid_label_fails(tmp_path):
    copy = yaml.safe_load((DIR / "metrics.yaml").read_text())
    copy["metrics"]["wdf_event_rejected_total"]["labels"]["jobid"] = {"values": ["x"]}
    path = tmp_path / "metrics.yaml"
    path.write_text(yaml.safe_dump(copy))
    run = check(path)
    assert run.returncode == 1
    assert "reason: denied-label:" in run.stderr
