"""Render tests of the temporal module through `tilt alpha tiltfile-result`, run by `just test`.

No cluster is needed.
A wrapper Tiltfile stands in for temporaldb and loads the module from a copy of the files it reads,
so a test can change a value without touching the tree.
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
# The limits the chart ran under in docs/delivery/spikes/S10/helm-install.md, as (memory, cpu).
S10_LIMITS = {
    "frontend": ("192Mi", "200m"),
    "history": ("576Mi", "450m"),
    "matching": ("128Mi", "200m"),
    "worker": ("128Mi", "150m"),
}
KEYS = {"history.hostLevelCacheMaxSize": 1000, "history.eventsCacheMaxSizeBytes": 32768}
CHECKSUM = "checksum/dynamic-config"

pytestmark = pytest.mark.skipif(
    shutil.which("tilt") is None or shutil.which("helm") is None, reason="tilt or helm not on PATH"
)


def render(root, edit=None):
    """Objects and resource dependencies of the module rendered from a copy under root."""
    shutil.copytree(
        MODULE, root / "deploy/temporal", ignore=shutil.ignore_patterns("*.py", "__pycache__")
    )
    for f in INPUTS:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / f, root / f)
    if edit:
        values = root / "deploy/temporal/values.json"
        data = json.loads(values.read_text())
        edit(data)
        values.write_text(json.dumps(data))
    (root / "Tiltfile").write_text(
        "local_resource('temporaldb', cmd='true')\n"
        "load_dynamic('deploy/temporal/Tiltfile.module')\n"
    )
    run = subprocess.run(
        ["tilt", "alpha", "tiltfile-result", "-f", str(root / "Tiltfile")],
        cwd=root,
        env={**os.environ, "KUBECONFIG": str(ROOT / "scripts/tilt/fixtures/kubeconfig-wdf.yaml")},
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    manifests = json.loads(run.stdout)["Manifests"]
    objects = [
        o for m in manifests for o in yaml.safe_load_all(m["DeployTarget"].get("yaml", "")) if o
    ]
    return {(o["kind"], o["metadata"]["name"]): o for o in objects}, {
        m["Name"]: m["ResourceDependencies"] for m in manifests
    }


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    return render(tmp_path_factory.mktemp("render"))


def deployments(objects):
    return {s: objects["Deployment", "temporal-" + s] for s in SERVICES}


def test_dynamic_config_keys(rendered):
    objects, _ = rendered
    config = objects["ConfigMap", "temporal-dynamic-config"]["data"]["dynamic_config.yaml"]
    assert yaml.safe_load(config) == {k: [{"value": v}] for k, v in KEYS.items()}
    server = objects["ConfigMap", "temporal-config"]["data"]["config_template.yaml"]
    assert 'filepath: "/etc/temporal/dynamic_config/dynamic_config.yaml"' in server
    for d in deployments(objects).values():
        volumes = {v["name"]: v for v in d["spec"]["template"]["spec"]["volumes"]}
        assert volumes["dynamic-config"]["configMap"]["name"] == "temporal-dynamic-config"


def test_limits_match_s10(rendered):
    objects, _ = rendered
    assert sorted(n for k, n in objects if k == "Deployment") == ["temporal-" + s for s in SERVICES]
    for s, d in deployments(objects).items():
        assert d["spec"]["replicas"] == 1
        (container,) = d["spec"]["template"]["spec"]["containers"]
        memory, cpu = S10_LIMITS[s]
        assert container["resources"] == {
            "requests": {"cpu": cpu, "memory": memory},
            "limits": {"cpu": cpu, "memory": memory},
        }
        assert d["spec"]["template"]["spec"]["nodeSelector"] == {"wdf.dev/pool": "infra"}
    assert sum(int(m[:-2]) for m, _ in S10_LIMITS.values()) == 1024
    assert sum(int(c[:-1]) for _, c in S10_LIMITS.values()) == 1000
    assert (
        "numHistoryShards: 512"
        in objects["ConfigMap", "temporal-config"]["data"]["config_template.yaml"]
    )


def test_config_change_rolls_pods(rendered, tmp_path):
    objects, _ = rendered

    def checksums(objs):
        return {
            s: d["spec"]["template"]["metadata"]["annotations"][CHECKSUM]
            for s, d in deployments(objs).items()
        }

    def raise_cache(values):
        values["server"]["dynamicConfig"]["history.hostLevelCacheMaxSize"] = [{"value": 2000}]

    before = checksums(objects)
    after = checksums(render(tmp_path, raise_cache)[0])
    assert len(set(before.values())) == 1
    assert len(set(after.values())) == 1
    assert before != after
    # A surge pod does not fit the quota of the infra namespace, so a roll replaces each pod.
    for d in deployments(objects).values():
        assert d["spec"]["strategy"] == {"type": "Recreate"}


def test_module_resource_waits_for_the_schema_and_every_service(rendered):
    _, deps = rendered
    assert deps["temporal-schema"] == ["temporaldb"]
    for s in SERVICES[1:]:
        assert deps["temporal-" + s] == ["temporal-schema"]
    assert deps["temporal"] == ["temporal-" + s for s in SERVICES[1:]]


def test_images_are_the_pinned_digests(rendered):
    objects, _ = rendered
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
