"""The benchmark validates a template once however many jobs it binds."""

import subprocess
import sys
from pathlib import Path

from wdf_jobdoc.profile_files import load_profile
from wdf_jobtools import bench

ROOT = Path(__file__).resolve().parents[3]


def test_template_is_validated_once(monkeypatch):
    calls = []
    real = bench.validate
    monkeypatch.setattr(bench, "validate", lambda *a, **k: calls.append(1) or real(*a, **k))
    result = bench.run(bench.DEFAULT_TASKS, 2_000, load_profile("ci", ROOT))
    assert len(calls) == 1
    assert (result.template_validations, result.jobs) == (1, 2_000)


def test_template_has_the_requested_size():
    for tasks in (bench.DEFAULT_TASKS, 512):
        assert len(bench.json.loads(bench.template(tasks))["tasks"]) == tasks


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "wdf_jobtools.bench", *args], capture_output=True, text=True
    )


def test_default_run_prints_the_fields_and_the_pass_line():
    result = run("--jobs", "200")
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert lines[0] == "template_validations: 1"
    assert "jobs: 200" in lines
    assert any(line.startswith("seconds: ") for line in lines)
    assert lines[-1].startswith("pass:")


def test_task_count_run_has_no_pass_line():
    result = run("--tasks", "512", "--jobs", "5")
    assert result.returncode == 0
    assert "tasks: 512" in result.stdout
    assert "pass" not in result.stdout


def test_bad_counts_exit_2():
    assert run("--jobs", "0").returncode == 2
