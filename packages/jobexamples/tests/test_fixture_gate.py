from pathlib import Path

import yaml

CONTRACT = Path(__file__).resolve().parents[3] / "contracts/job"
VECTORS = CONTRACT / "vectors"


def _directory(code, kind):
    if kind == "params":
        return VECTORS / "job-params/invalid" / code
    if kind == "patch":
        return VECTORS / "patch" / code
    return VECTORS / "invalid" / kind / code


def missing_fixtures(codes):
    """The codes of `codes` (a code to class map) whose vector directory holds no file."""
    return sorted(
        code
        for code, kind in codes.items()
        if not any(p.is_file() for p in _directory(code, kind).rglob("*"))
    )


def _codes():
    table = yaml.safe_load((CONTRACT / "errors.yaml").read_text())["codes"]
    return {code: entry["class"] for code, entry in table.items()}


def test_every_code_has_a_fixture():
    assert missing_fixtures(_codes()) == []


def test_the_gate_reports_a_code_without_a_fixture():
    codes = _codes() | {"no-such-code": "graph", "no-such-patch": "patch"}
    assert missing_fixtures(codes) == ["no-such-code", "no-such-patch"]


def test_every_class_is_one_the_gate_knows():
    assert set(_codes().values()) == {
        "schema",
        "graph",
        "reference",
        "bundle",
        "resource",
        "limit",
        "params",
        "patch",
    }
