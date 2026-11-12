"""Render tests of the workflow-worker module through `tilt alpha tiltfile-result`, run by
`just test`.

No cluster and no image build are needed.
A wrapper Tiltfile stands in for the modules it depends on, enables only the module's own resource
as the root Tiltfile does, and loads the module from a copy of the files it reads, outside any git
checkout, with a state directory of its own.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from wdf_storecheck.s3 import Creds, dump_credentials

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "deploy/workflow"
INPUTS = [
    "deploy/versions.yaml",
    "contracts/topology/topology.yaml",
    "contracts/temporal/naming.yaml",
    "scripts/storage/store/identities.sh",
]
DEPENDS = ["appdb-migrate", "objectstore-bootstrap", "temporal-bootstrap"]
ROLES = ("admin", "submit", "workflow", "indexer", "reaper", "compile")

pytestmark = pytest.mark.skipif(shutil.which("tilt") is None, reason="tilt not on PATH")


def tiltfile_result(root: Path, build_id: str | None):
    shutil.copytree(
        MODULE, root / "deploy/workflow", ignore=shutil.ignore_patterns("*.py", "__pycache__")
    )
    shutil.copy(MODULE / "logins.py", root / "deploy/workflow/logins.py")
    for f in INPUTS:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, root / f)
    (root / "packages").mkdir()
    (root / "uv.lock").touch()
    state = root / "state"
    state.mkdir()
    dump_credentials(
        state / "objectstore.credentials.json",
        {role: Creds(f"{role}-id", f"{role}-key") for role in ROLES},
    )
    (root / "Tiltfile").write_text(
        "".join(f"local_resource('{d}', cmd='true')\n" for d in DEPENDS)
        + "load_dynamic('deploy/workflow/Tiltfile.module')\n"
        + f"config.set_enabled_resources({DEPENDS + ['workflow-worker']})\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "WDF_BUILD_ID"}
    env |= {
        "KUBECONFIG": str(ROOT / "scripts/tilt/fixtures/kubeconfig-wdf.yaml"),
        "WDF_STATE_DIR": str(state),
        # logins.py runs in the environment of the tree, which the copy does not hold.
        "UV_PROJECT": str(ROOT),
    }
    if build_id is not None:
        env["WDF_BUILD_ID"] = build_id
    return subprocess.run(
        ["tilt", "alpha", "tiltfile-result", "-f", str(root / "Tiltfile")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    root = tmp_path_factory.mktemp("render")
    run = tiltfile_result(root, "exit-a")
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    manifests = {m["Name"]: m for m in result["Manifests"]}
    objects = {
        (o["kind"], o["metadata"]["name"]): o
        for m in manifests.values()
        for o in yaml.safe_load_all(m["DeployTarget"].get("yaml", ""))
        if o
    }
    return manifests, objects, root


def test_one_deployment_per_build_id(rendered):
    manifests, objects, _ = rendered
    deployment = objects["Deployment", "workflow-worker-exit-a"]
    assert deployment["metadata"]["namespace"] == "wdf-control"
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}
    labels = deployment["spec"]["template"]["metadata"]["labels"]
    assert labels == {"app.kubernetes.io/name": "workflow-worker", "wdf.dev/build-id": "exit-a"}
    [image] = manifests["workflow-worker"]["ImageTargets"]
    assert image["BuildDetails"]["env"] == ["WDF_BUILD_ID=exit-a"]
    assert image["BuildDetails"]["args"][-1].endswith("deploy/workflow/image.sh")


def test_requests_equal_limits_in_memory_on_the_infra_pool(rendered):
    _, objects, _ = rendered
    pod = objects["Deployment", "workflow-worker-exit-a"]["spec"]["template"]["spec"]
    [container] = pod["containers"]
    resources = container["resources"]
    assert resources["requests"]["memory"] == resources["limits"]["memory"] == "256Mi"
    assert pod["nodeSelector"] == {"wdf.dev/pool": "infra"}
    assert container["readinessProbe"]["exec"]["command"] == ["test", "-e", "/tmp/wdf-ready"]


def test_service_configuration_from_env_and_secrets(rendered):
    _, objects, _ = rendered
    [container] = objects["Deployment", "workflow-worker-exit-a"]["spec"]["template"]["spec"][
        "containers"
    ]
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env["WDF_TEMPORAL_NAMESPACE"] == "wdf"
    assert env["WDF_TEMPORAL_ADDRESS"] == "temporal-frontend.wdf-infra.svc.cluster.local:7233"
    assert "WDF_BUILD_ID" not in env, "the image carries the build id"
    assert container["envFrom"] == [{"secretRef": {"name": "workflow-worker-logins"}}]
    assert set(objects["Secret", "workflow-worker-logins"]["stringData"]) == {
        "WDF_WORKFLOW_DSN",
        "WDF_ARTIFACTS_DSN",
        "WDF_REAPER_DSN",
    }


def test_store_secret_holds_the_three_service_identities_only(rendered):
    _, objects, root = rendered
    held = json.loads(objects["Secret", "workflow-worker-store"]["stringData"]["credentials.json"])
    every = json.loads((root / "state/objectstore.credentials.json").read_text())
    assert held == {role: every[role] for role in ("workflow", "indexer", "reaper")}


def test_waits_for_logins_and_its_dependencies(rendered):
    manifests, _, _ = rendered
    assert sorted(manifests["workflow-worker"]["ResourceDependencies"]) == sorted(
        DEPENDS + ["workflow-worker-logins"]
    )
    assert manifests["workflow-worker-logins"]["ResourceDependencies"] == ["appdb-migrate"]


def test_no_build_id_outside_a_checkout_refused(tmp_path):
    run = tiltfile_result(tmp_path, None)
    assert run.returncode != 0
    assert "no build id: set WDF_BUILD_ID outside a git checkout" in run.stderr


def test_build_id_not_a_name_refused(tmp_path):
    run = tiltfile_result(tmp_path, "Exit_A")
    assert run.returncode != 0
    assert "is not a lower-case name" in run.stderr
