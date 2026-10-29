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
HOST_MEM_KIB = 8133348
HOST_CPUS = 6


def render(preset, out):
    run = subprocess.run(
        [ROOT / "scripts/cluster/render.sh", preset],
        cwd=ROOT,
        env={
            "PATH": os.environ["PATH"],
            "HOME": os.environ["HOME"],
            "WDF_RENDER_DIR": str(out),
            "WDF_HOST_MEM_KIB": str(HOST_MEM_KIB),
            "WDF_HOST_CPUS": str(HOST_CPUS),
        },
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    return Path(run.stdout.strip())


def tree(path):
    # The mounts name the output directory, which differs between two renders into different places.
    return {
        str(p.relative_to(path)): p.read_text().replace(str(path), "@OUT@")
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


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
    assert len(list(out.glob("patches/*/kubeletconfiguration+merge.yaml"))) == len(want)
    mounts = re.findall(r"hostPath: (\S+)\n          containerPath: /etc/wdf-patches\n", cluster)
    assert len(mounts) == len(want)
    for patch, (role, _, _) in zip(patches, want, strict=True):
        assert "apiVersion: kubeadm.k8s.io/v1beta3\n" in patch
        assert "patches:\n            directory: /etc/wdf-patches\n" in patch
        if role == "control-plane":
            assert "kind: InitConfiguration" in patch
        else:
            assert f"node-labels: wdf.dev/pool={role}\n" in patch
            assert f"value: {role}\n                effect: NoSchedule" in patch


@pytest.mark.parametrize("preset", PRESETS)
def test_allocatable_is_node_size_minus_the_eviction_threshold(preset, tmp_path):
    out = render(preset, tmp_path)
    want = sorted(preset_nodes(preset), key=lambda n: n[0] != "control-plane")
    kind_names = ["control-plane", "worker"] + [f"worker{i}" for i in range(2, len(want))]
    for kind_name, (_, mem, cpus) in zip(kind_names, want, strict=True):
        patch = (out / "patches" / kind_name / "kubeletconfiguration+merge.yaml").read_text()
        evict = 256 + -(-mem // 16)
        assert f"memory.available: {evict}Mi\n" in patch
        reserved_kib = int(re.search(r"memory: (\d+)Ki", patch).group(1))
        reserved_milli = int(re.search(r"cpu: (\d+)m", patch).group(1))
        if mem * 1024 <= HOST_MEM_KIB:
            assert HOST_MEM_KIB - reserved_kib - evict * 1024 == (mem - evict) * 1024
        else:
            assert reserved_kib == 0
        if float(cpus) <= HOST_CPUS:
            assert HOST_CPUS * 1000 - reserved_milli == float(cpus) * 1000 - 100
        else:
            assert reserved_milli == 100


def test_the_canary_verdict_never_depends_on_the_oomkilled_reason():
    lines = (ROOT / "scripts/cluster/canary.sh").read_text().splitlines()
    code = [line for line in lines if not line.lstrip().startswith("#")]
    assert [line for line in code if "OOMKilled" in line] == []


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


@pytest.mark.parametrize("preset", PRESETS)
def test_only_the_infra_node_mounts_the_data_directory(preset, tmp_path):
    out = render(preset, tmp_path)
    preset_text = (ROOT / f"deploy/presets/{preset}.yaml").read_text()
    data_dir = re.search(r"data_dir: (\S+)", preset_text).group(1)
    data_dir = data_dir.replace("~", os.environ["HOME"], 1)
    cluster = (out / "cluster.yaml").read_text()
    mounts = re.findall(r"hostPath: (\S+)\n\s+containerPath: /var/local-path-provisioner", cluster)
    assert mounts == [data_dir]
