"""Render tests of the temporal module through `tilt alpha tiltfile-result`, run by `just test`.

No cluster is needed.
A wrapper Tiltfile stands in for temporaldb, enables only the module's own resource as the root
Tiltfile does, and loads the module from a copy of the files it reads, so a test can change an
input without touching the tree.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "deploy/temporal"
INPUTS = ["deploy/versions.yaml", "contracts/topology/topology.yaml"]
SERVICES = ["frontend", "history", "matching", "worker"]
# The limits the chart passed under on the ci infra node, as (memory, cpu).
S10_LIMITS = {
    "frontend": ("192Mi", "200m"),
    "history": ("576Mi", "450m"),
    "matching": ("128Mi", "200m"),
    "worker": ("128Mi", "150m"),
}
SCHEMA_LIMITS = ("128Mi", "50m")
KEYS = {"history.hostLevelCacheMaxSize": 1000, "history.eventsCacheMaxSizeBytes": 32768}
CHECKSUM = "checksum/dynamic-config"
INFRA_TOLERATION = {
    "key": "wdf.dev/pool",
    "operator": "Equal",
    "value": "infra",
    "effect": "NoSchedule",
}

pytestmark = pytest.mark.skipif(
    shutil.which("tilt") is None or shutil.which("helm") is None, reason="tilt or helm not on PATH"
)


def tiltfile_result(root, values=None, versions=None):
    """The tilt run over a copy of the module under root, after values(data) and versions(text)."""
    shutil.copytree(
        MODULE, root / "deploy/temporal", ignore=shutil.ignore_patterns("*.py", "__pycache__")
    )
    for f in INPUTS:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / f, root / f)
    if values:
        path = root / "deploy/temporal/values.json"
        data = json.loads(path.read_text())
        values(data)
        path.write_text(json.dumps(data))
    if versions:
        path = root / "deploy/versions.yaml"
        path.write_text(versions(path.read_text()))
    (root / "Tiltfile").write_text(
        "local_resource('temporaldb', cmd='true')\n"
        "load_dynamic('deploy/temporal/Tiltfile.module')\n"
        "config.set_enabled_resources(['temporaldb', 'temporal'])\n"
    )
    return subprocess.run(
        ["tilt", "alpha", "tiltfile-result", "-f", str(root / "Tiltfile")],
        cwd=root,
        env={**os.environ, "KUBECONFIG": str(ROOT / "scripts/tilt/fixtures/kubeconfig-wdf.yaml")},
        capture_output=True,
        text=True,
    )


def render(root, values=None):
    """Objects, resource dependencies and enabled resources of the module rendered under root."""
    run = tiltfile_result(root, values)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    manifests = result["Manifests"]
    objects = [
        o for m in manifests for o in yaml.safe_load_all(m["DeployTarget"].get("yaml", "")) if o
    ]
    return (
        {(o["kind"], o["metadata"]["name"]): o for o in objects},
        {m["Name"]: m["ResourceDependencies"] for m in manifests},
        set(result["EnabledManifests"]),
    )


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    return render(tmp_path_factory.mktemp("render"))


def deployments(objects):
    return {s: objects["Deployment", "temporal-" + s] for s in SERVICES}


def schema_job(objects):
    (job,) = [o for (kind, _), o in objects.items() if kind == "Job"]
    return job


def sized(memory, cpu):
    return {"requests": {"cpu": cpu, "memory": memory}, "limits": {"cpu": cpu, "memory": memory}}


def test_dynamic_config_keys(rendered):
    objects, _, _ = rendered
    config = objects["ConfigMap", "temporal-dynamic-config"]["data"]["dynamic_config.yaml"]
    assert yaml.safe_load(config) == {k: [{"value": v}] for k, v in KEYS.items()}
    server = objects["ConfigMap", "temporal-config"]["data"]["config_template.yaml"]
    assert 'filepath: "/etc/temporal/dynamic_config/dynamic_config.yaml"' in server
    for d in deployments(objects).values():
        volumes = {v["name"]: v for v in d["spec"]["template"]["spec"]["volumes"]}
        assert volumes["dynamic-config"]["configMap"]["name"] == "temporal-dynamic-config"


def test_limits_match_s10(rendered):
    objects, _, _ = rendered
    assert sorted(n for k, n in objects if k == "Deployment") == ["temporal-" + s for s in SERVICES]
    assert {o["metadata"]["namespace"] for o in objects.values()} == {"wdf-infra"}
    for s, d in deployments(objects).items():
        assert d["spec"]["replicas"] == 1
        (container,) = d["spec"]["template"]["spec"]["containers"]
        assert container["resources"] == sized(*S10_LIMITS[s])
    job = schema_job(objects)["spec"]["template"]["spec"]
    for c in job["initContainers"] + job["containers"]:
        assert c["resources"] == sized(*SCHEMA_LIMITS)
    for pod in [d["spec"]["template"]["spec"] for d in deployments(objects).values()] + [job]:
        assert pod["nodeSelector"] == {"wdf.dev/pool": "infra"}
        assert pod["tolerations"] == [INFRA_TOLERATION]
    assert sum(int(m[:-2]) for m, _ in S10_LIMITS.values()) == 1024
    assert sum(int(c[:-1]) for _, c in S10_LIMITS.values()) == 1000
    server = objects["ConfigMap", "temporal-config"]["data"]["config_template.yaml"]
    assert "numHistoryShards: 512" in server


@pytest.mark.parametrize("key", KEYS)
def test_config_change_rolls_pods(rendered, tmp_path, key):
    objects, _, _ = rendered

    def checksums(objs):
        return {
            s: d["spec"]["template"]["metadata"]["annotations"][CHECKSUM]
            for s, d in deployments(objs).items()
        }

    def double(values):
        values["server"]["dynamicConfig"][key] = [{"value": KEYS[key] * 2}]

    before = checksums(objects)
    after = checksums(render(tmp_path, double)[0])
    assert len(set(before.values())) == 1
    assert len(set(after.values())) == 1
    assert before != after
    # A surge pod does not fit the quota of the infra namespace, so a roll replaces each pod.
    for d in deployments(objects).values():
        assert d["spec"]["strategy"] == {"type": "Recreate"}


def test_schema_job_is_kept_after_it_completes(rendered):
    objects, _, _ = rendered
    assert "ttlSecondsAfterFinished" not in schema_job(objects)["spec"]


def test_module_resource_waits_for_the_schema_and_every_service(rendered):
    _, deps, enabled = rendered
    assert deps["temporal-schema"] == ["temporaldb"]
    for s in SERVICES[1:]:
        assert deps["temporal-" + s] == ["temporal-schema"]
    assert deps["temporal"] == ["temporal-" + s for s in SERVICES[1:]]
    assert enabled == set(deps)


def test_images_are_the_pinned_digests(rendered):
    objects, _, _ = rendered
    images = yaml.safe_load((ROOT / "deploy/versions.yaml").read_text())["images"]
    pinned = {
        f"{k}:{images[k]['version']}@{images[k]['digest']}"
        for k in ["temporalio/server", "temporalio/admin-tools"]
    }
    used = {
        c["image"]
        for o in objects.values()
        if o["kind"] in ("Deployment", "Job")
        for c in o["spec"]["template"]["spec"].get("initContainers", [])
        + o["spec"]["template"]["spec"]["containers"]
    }
    assert used == pinned


def test_chart_with_another_digest_is_refused(tmp_path):
    pin = yaml.safe_load((ROOT / "deploy/versions.yaml").read_text())["charts"]["temporal"]
    run = tiltfile_result(
        tmp_path, versions=lambda t: t.replace(pin["digest"], "sha256:" + "0" * 64)
    )
    assert run.returncode != 0
    assert "deploy/versions.yaml pins sha256:" + "0" * 64 in run.stderr
