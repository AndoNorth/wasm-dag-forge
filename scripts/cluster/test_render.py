"""Renderer tests, run by `just ci`: determinism and the preset-to-output mapping.

The preset is read with a regex because the test suite has no YAML parser and the files are regular.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PRESETS = ["ci", "dev", "soak"]


def render(preset, out):
    run = subprocess.run(
        [ROOT / "scripts/cluster/render.sh", preset],
        cwd=ROOT,
        env={"PATH": os.environ["PATH"], "WDF_RENDER_DIR": str(out)},
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    return Path(run.stdout.strip())


def tree(path):
    return {p.name: p.read_text() for p in sorted(path.iterdir())}


def preset_nodes(preset):
    text = (ROOT / f"deploy/presets/{preset}.yaml").read_text()
    found = re.findall(r"role: (\S+)\n\s+count: (\d+)\n\s+cpus: (\S+)\n\s+memory_mib: (\d+)", text)
    return [(role, int(mem), cpus) for role, count, cpus, mem in found for _ in range(int(count))]


@pytest.mark.parametrize("preset", PRESETS)
def test_two_renders_are_byte_identical(preset, tmp_path):
    assert tree(render(preset, tmp_path / "a")) == tree(render(preset, tmp_path / "b"))


@pytest.mark.parametrize("preset", PRESETS)
def test_every_node_is_rendered_from_the_preset(preset, tmp_path):
    out = render(preset, tmp_path)
    want = sorted(preset_nodes(preset), key=lambda n: n[0] != "control-plane")
    cluster = (out / "cluster.yaml").read_text()
    assert re.findall(r"^    - role: (\S+)$", cluster, re.M) == [
        "control-plane" if role == "control-plane" else "worker" for role, _, _ in want
    ]

    patches = re.findall(r"kubeadmConfigPatches:\n        - \|\n((?:          .*\n)+)", cluster)
    assert len(patches) == len(want)
    assert len(list(out.glob("kubelet-*.yaml"))) == len(want)
    for patch, (role, mem, _) in zip(patches, want, strict=True):
        assert f"memory.available: {256 + -(-mem // 16)}Mi\n" in patch
        assert "systemReserved:\n            cpu:" in patch
        if role == "control-plane":
            assert "kind: InitConfiguration" in patch
        else:
            assert f"node-labels: wdf.dev/pool={role}\n" in patch
            assert "apiVersion: kubeadm.k8s.io/v1beta3\n" in patch
            assert f"value: {role}\n                effect: NoSchedule" in patch


@pytest.mark.parametrize("preset", PRESETS)
def test_every_node_has_a_hard_limit(preset, tmp_path):
    out = render(preset, tmp_path)
    want = sorted(preset_nodes(preset), key=lambda n: n[0] != "control-plane")
    entries = re.findall(
        r"- container: (\S+)\n    role: (\S+)\n    cpus: (\S+)\n"
        r"    memory_mib: (\d+)\n    memory_swap_mib: (\d+)\n",
        (out / "limits.yaml").read_text(),
    )
    assert len(entries) == len(want)
    names = [e[0] for e in entries]
    assert len(set(names)) == len(names)
    kind = ["control-plane"] + ["worker"] + [f"worker{i}" for i in range(2, len(want))]
    assert names == [f"wdf-{k}" for k in kind]
    for (_, *got), (role, mem, cpus) in zip(entries, want, strict=True):
        assert got == [role, cpus, str(mem), str(mem)]


@pytest.mark.parametrize("preset", PRESETS)
def test_cluster_and_registry_are_named_for_the_wdf_context(preset, tmp_path):
    out = render(preset, tmp_path)
    cluster = (out / "cluster.yaml").read_text()
    assert re.search(r"^name: kind-wdf$", cluster, re.M)
    assert re.search(r"^  name: wdf$", cluster, re.M)
    registry = (out / "registry.yaml").read_text()
    assert re.search(r"^name: (\S+)$", registry, re.M).group(1) in cluster
    assert "listenAddress: 127.0.0.1\n" in registry
    assert re.search(r"^image: registry:\S+@sha256:[0-9a-f]{64}$", registry, re.M)
