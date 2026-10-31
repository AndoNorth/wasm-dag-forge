"""validate composes the passes: what runs together, what is gated, and that answers are stable."""

import copy
import json
from pathlib import Path

import pytest
from wdf_jobcheck import StaticResolver, validate
from wdf_jobdoc.profile_files import load_profile

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "contracts/job"
PROFILE = load_profile("ci", ROOT, CONTRACT / "vectors/slots-fixture")
RESOLVER = StaticResolver(json.loads((CONTRACT / "vectors/resolver.json").read_text()))


def run(raw, resolver=RESOLVER):
    return validate(json.dumps(raw).encode(), PROFILE, resolver)


def codes(report):
    return [i.code for i in report.issues]


def test_a_valid_document_is_ok_and_carries_the_document(good):
    report = run(good)
    assert report.ok and report.issues == ()
    assert set(report.document.tasks) == {"build", "sim", "agg"}


def test_a_bad_field_reports_only_the_schema_pass(good):
    good["tasks"]["sim"]["memory_mib"] = 0
    good["tasks"]["agg"]["depends_on"] = ["ghost"]
    good["tasks"]["agg"]["cpu"] = 64
    report = run(good)
    assert codes(report) == ["bad-value"]
    assert report.document is None


def test_a_cycle_an_unknown_output_and_an_oversized_task_are_reported_in_one_call(good):
    good["tasks"]["build"]["depends_on"] = ["agg"]
    good["tasks"]["sim"]["inputs"] = [{"task": "build", "output": "wasm"}]
    good["tasks"]["agg"]["cpu"] = 64
    report = run(good)
    assert {"dependency-cycle", "undeclared-output", "cpu-exceeds-slot"} <= set(codes(report))
    keys = [(i.path, i.code) for i in report.issues]
    assert keys == sorted(keys)


def test_pass_gating(good):
    """The order of the passes and what each needs, in one place."""
    # A cycle and an unresolved bundle are both reported: bundles do not wait for the graph.
    cyclic = copy.deepcopy(good)
    cyclic["tasks"]["build"]["depends_on"] = ["agg"]
    cyclic["tasks"]["build"]["params"]["source"]["sha256"] = "e" * 64
    cyclic["tasks"]["build"]["params"]["source"]["uri"] = f"s3://wdf-sources/sha256/ee/{'e' * 64}"
    assert {"dependency-cycle", "bundle-unresolved"} <= set(codes(run(cyclic)))
    # Counting limits run beside a broken graph, and plan limits wait for it to be sound.
    wide = copy.deepcopy(good)
    wide["max_parallel"] = 33
    wide["tasks"]["build"]["depends_on"] = ["agg"]
    wide["tasks"]["sim"]["params"]["args"] = {"names": ["x" * 1000] * 300}
    assert set(codes(run(wide))) == {"dependency-cycle", "max-parallel-exceeded"}
    # With the graph and the references sound, the plan limits run.
    sound = copy.deepcopy(wide)
    sound["tasks"]["build"]["depends_on"] = []
    assert set(codes(run(sound))) == {"plan-too-large", "max-parallel-exceeded"}
    # A reference fault gates the plan limits as a graph fault does.
    broken = copy.deepcopy(sound)
    broken["tasks"]["agg"]["inputs"] = [{"task": "ghost", "output": "result"}]
    assert set(codes(run(broken))) == {"unknown-producer", "max-parallel-exceeded"}


def test_without_a_resolver_the_bundle_checks_are_skipped(good):
    good["tasks"]["build"]["params"]["entry"] = "ghost_module"
    assert codes(run(good)) == ["entry-not-in-bundle"]
    assert run(good, resolver=None).ok


def test_validate_is_deterministic(good):
    good["tasks"]["build"]["depends_on"] = ["agg"]
    good["tasks"]["agg"]["cpu"] = 64
    data = json.dumps(good).encode()
    first = validate(data, PROFILE, RESOLVER)
    second = validate(data, PROFILE, RESOLVER)
    assert first == second


def test_the_report_is_cut_at_the_issue_cap(good):
    for i in range(150):
        good["tasks"][f"s{i:03d}"] = {**good["tasks"]["sim"], "depends_on": [f"ghost{i}"]}
    report = run(good)
    assert not report.ok
    assert len(report.issues) == 100


def test_every_code_reported_is_in_the_error_table(good):
    import yaml

    table = set(yaml.safe_load((CONTRACT / "errors.yaml").read_text())["codes"])
    for path in sorted((CONTRACT / "vectors/invalid").glob("*/*/*.json")):
        report = validate(path.read_bytes(), PROFILE, RESOLVER)
        assert {i.code for i in report.issues} <= table, path


@pytest.mark.parametrize("data", [b"", b"[]", b"\xef\xbb\xbf{}", b"{" * 100000])
def test_hostile_bytes_end_in_a_report_and_not_a_traceback(data):
    assert not validate(data, PROFILE, RESOLVER).ok
