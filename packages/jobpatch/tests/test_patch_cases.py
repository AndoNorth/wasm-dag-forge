"""Every patch vector is compiled, bound and checked, and the codes and lists must match."""

from pathlib import Path

import pytest
import yaml
from wdf_jobpatch import patch_check

ROOT = Path(__file__).resolve().parents[3]
VECTORS = ROOT / "contracts/job/vectors/patch"
CASES = sorted(p for p in VECTORS.glob("*/*") if p.is_dir())

REQUIRED_OK = {
    "bundle-changed",
    "args-changed",
    "aggregate-changed",
    "checkpoint-args-changed",
    "depends-only-compile-changed",
}


def patch_codes():
    errors = yaml.safe_load((ROOT / "contracts/job/errors.yaml").read_text())["codes"]
    return {code for code, entry in errors.items() if entry["class"] == "patch"}


@pytest.mark.parametrize("case", CASES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_case(case, load_case):
    old, new, completed, expected = load_case(case)
    report = patch_check(old, new, completed)
    assert sorted({i.code for i in report.issues}) == expected["codes"]
    assert list(report.recompile) == expected["recompile"]
    assert list(report.changed) == expected["changed"]
    assert report.compatible == (case.parent.name == "ok")
    if case.parent.name != "ok":
        assert case.parent.name in expected["codes"]


def test_every_row_has_a_case():
    verdicts = {p.parent.name for p in CASES}
    assert verdicts >= patch_codes() | {"ok"}
    assert {p.name for p in CASES if p.parent.name == "ok"} >= REQUIRED_OK
    frozen = [p for p in CASES if p.parent.name == "frozen-field-changed"]
    assert {"task-added", "dependency-changed"} <= {p.name for p in frozen}


def test_a_directory_names_a_known_verdict():
    assert {p.parent.name for p in CASES} <= patch_codes() | {"ok"}
