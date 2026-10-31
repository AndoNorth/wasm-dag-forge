"""The commands run as a user runs them: a subprocess per call, checked by exit code and output."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
VECTORS = ROOT / "contracts/job/vectors"
RESOLVER = VECTORS / "resolver.json"
OK_CASE = VECTORS / "patch/ok/bundle-changed"
TEMPLATE_CASE = VECTORS / "patch/ok/seed-changed"
CYCLE = VECTORS / "invalid/graph/dependency-cycle/three-task-cycle.json"
BUNDLE_VECTORS = sorted(VECTORS.glob("invalid/bundle/*/*.json"))


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "wdf_jobtools", *map(str, args)], capture_output=True, text=True
    )


def plan_args(*extra):
    return ["plan", TEMPLATE_CASE / "old.json", "--params", TEMPLATE_CASE / "params.json", *extra]


def patch_args(case, *extra):
    return [
        "patch-check",
        case / "old.json",
        case / "new.json",
        "--params",
        case / "params.json",
        "--completed",
        case / "completed.json",
        *extra,
    ]


def test_validate_accepts_a_valid_document():
    result = run("validate", OK_CASE / "old.json")
    assert result.returncode == 0
    assert result.stdout.startswith("ok ")


def test_validate_reports_a_cycle_with_a_pointer_path():
    result = run("validate", CYCLE, "--json")
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["ok"] is False
    (issue,) = [i for i in report["issues"] if i["code"] == "dependency-cycle"]
    assert issue["path"].startswith("/tasks/")


@pytest.mark.parametrize("path", BUNDLE_VECTORS, ids=lambda p: p.parent.name)
def test_resolver_finds_the_bundle_fault_of_each_vector(path):
    with_resolver = run("validate", path, "--resolver", RESOLVER, "--json")
    assert with_resolver.returncode == 1
    assert json.loads(with_resolver.stdout)["issues"][0]["code"] == path.parent.name
    without = run("validate", path)
    assert without.returncode == 0
    assert "bundle and index checks were skipped" in without.stderr


def test_plan_prints_identity_and_is_repeatable(tmp_path):
    first = run(*plan_args("--out", tmp_path / "a"))
    second = run(*plan_args("--out", tmp_path / "b"))
    assert first.returncode == 0
    assert first.stdout == second.stdout
    for key in ("manifest_hash", "plan_hash", "stats.task_count", "class_hint"):
        assert f"{key}:" in first.stdout
    manifest = (tmp_path / "a/manifest.json").read_bytes()
    assert manifest == (tmp_path / "b/manifest.json").read_bytes()
    assert json.loads(manifest)["tasks"]


def test_plan_without_params():
    result = run("plan", OK_CASE / "old.json", "--json")
    assert result.returncode == 0
    body = json.loads(result.stdout)
    assert body["plan_hash"] is None
    assert body["manifest_hash"]
    assert body["class_hint"] in ("short", "long")


def test_plan_hash_changes_with_the_job_values():
    other = TEMPLATE_CASE / "new-params.json"
    one = json.loads(run(*plan_args("--json")).stdout)
    two = json.loads(run("plan", TEMPLATE_CASE / "old.json", "--params", other, "--json").stdout)
    assert one["manifest_hash"] == two["manifest_hash"]
    assert one["plan_hash"] != two["plan_hash"]


def test_plan_reports_params_that_do_not_fit(tmp_path):
    params = tmp_path / "params.json"
    params.write_text('{"seed": -1}')
    result = run("plan", TEMPLATE_CASE / "old.json", "--params", params, "--json")
    assert result.returncode == 1
    assert {i["code"] for i in json.loads(result.stdout)["issues"]} >= {"bad-seed"}


def test_patch_check_compatible_lists_recompile():
    result = run(*patch_args(OK_CASE))
    assert result.returncode == 0
    assert "recompile: build" in result.stdout
    assert json.loads(run(*patch_args(OK_CASE, "--json")).stdout)["recompile"] == ["build"]


def test_patch_check_incompatible_names_the_code():
    case = VECTORS / "patch/completed-task-changed/simulate-args"
    result = run(*patch_args(case))
    assert result.returncode == 1
    assert "completed-task-changed" in result.stdout


def test_patch_check_new_params_are_applied():
    case = TEMPLATE_CASE
    result = run(*patch_args(case, "--new-params", case / "new-params.json", "--json"))
    assert json.loads(result.stdout)["ok"] is True


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["nope"],
        ["validate"],
        ["validate", "missing.json"],
        ["validate", OK_CASE / "old.json", "--profile", "nope"],
        ["validate", OK_CASE / "old.json", "--bad-flag"],
        ["validate", OK_CASE / "old.json", "--resolver", OK_CASE / "old.json", "extra"],
        ["plan", OK_CASE / "old.json", "--params", "missing.json"],
        ["patch-check", OK_CASE / "old.json", OK_CASE / "new.json"],
    ],
    ids=str,
)
def test_usage_errors_are_one_line(args):
    result = run(*args)
    assert result.returncode == 2
    assert result.stdout == ""
    assert len(result.stderr.strip().splitlines()) == 1


def test_a_slots_dir_without_the_profile_file_is_a_usage_error(tmp_path):
    result = run("validate", OK_CASE / "old.json", "--slots-dir", tmp_path)
    assert result.returncode == 2
    assert len(result.stderr.strip().splitlines()) == 1
