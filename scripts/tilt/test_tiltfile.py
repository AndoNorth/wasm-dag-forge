"""Offline Tiltfile checks through `tilt alpha tiltfile-result`, run by `just test`.

No cluster is needed.
The context comes from a fixture kubeconfig and the modules from deploy/tilt/fixtures.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "scripts/tilt/fixtures"
MODULES = ROOT / "deploy/tilt/fixtures/modules"
PROFILES = yaml.safe_load((ROOT / "deploy/tilt/profiles.yaml").read_text())["profiles"]

pytestmark = pytest.mark.skipif(shutil.which("tilt") is None, reason="tilt not on PATH")


def tiltfile_result(profile="ci", kubeconfig="kubeconfig-wdf.yaml", modules=MODULES):
    return subprocess.run(
        [
            "tilt", "alpha", "tiltfile-result", "--",
            "--profile", profile, "--modules_dir", str(modules),
        ],
        cwd=ROOT,
        env={**os.environ, "KUBECONFIG": str(FIXTURES / kubeconfig)},
        capture_output=True,
        text=True,
    )  # fmt: skip


def selection(profile):
    run = tiltfile_result(profile)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    labels = {m["Name"]: next(iter(m["Labels"])) for m in result["Manifests"]}
    return result["EnabledManifests"], labels


@pytest.mark.parametrize("profile", PROFILES)
def test_enabled_groups_match_the_profile_table(profile):
    enabled, labels = selection(profile)
    assert {labels[name] for name in enabled} == set(PROFILES[profile]["groups"])


def test_obs_tier_and_mirror_differ_per_profile():
    assert set(selection("minimal")[0]) == {"store"}
    assert set(selection("dev")[0]) == {"store", "control-plane", "worker"}
    assert "vector" in selection("full")[0] and "vector-lite" not in selection("full")[0]
    assert "vector-lite" in selection("ci")[0] and "mirror" in selection("ci")[0]
    assert "mirror" not in selection("full")[0]


def test_guard_refuses_a_second_kind_context():
    run = tiltfile_result(kubeconfig="kubeconfig-other.yaml")
    assert run.returncode != 0
    assert "refusing context kind-other, only kind-wdf is allowed" in run.stderr


def test_unknown_profile_is_refused():
    run = tiltfile_result("nope")
    assert run.returncode != 0
    assert "unknown profile nope" in run.stderr


def test_module_failing_the_contract_fails_the_load_with_its_reason(tmp_path):
    shutil.copytree(MODULES, tmp_path / "modules")
    bad = tmp_path / "modules/store/module.yaml"
    bad.write_text(bad.read_text().replace("group: infra", "group: nonsense"))
    run = tiltfile_result(modules=tmp_path / "modules")
    assert run.returncode != 0
    assert "reason: schema-violation" in run.stderr


def test_dependency_on_a_disabled_module_is_refused(tmp_path):
    shutil.copytree(MODULES, tmp_path / "modules")
    worker = tmp_path / "modules/worker/module.yaml"
    worker.write_text(worker.read_text().replace("[infra/store]", "[infra/store, infra/mirror]"))
    assert tiltfile_result("ci", modules=tmp_path / "modules").returncode == 0
    run = tiltfile_result("dev", modules=tmp_path / "modules")
    assert run.returncode != 0
    assert "module worker depends on" in run.stderr
