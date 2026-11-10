"""A real component, built by the compile library and run by the runtime library on wasmtime."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from wdf_artifactclient.staging import collect_outputs, contract
from wdf_artifactclient.testing import make_scratch
from wdf_compile import bundle
from wdf_compile.build import build
from wdf_kernel.reader import read
from wdf_runtime.command import MIB, PAGE, warm_up
from wdf_runtime.contract import canonical
from wdf_runtime.contract import check as check_contract
from wdf_runtime.pins import GUEST_MIN_PAGES, GUEST_OVERHEAD_MIB
from wdf_runtime.run import copy_input, input_digest, run, warm, warmed

# A guest that writes a wdf.sim.v1 document listing the environment and /in it sees, and, by its
# workload name, writes a results.xml just above args.mib MiB, changes its input, or fails.
GUEST = """\
import json
import os
import sys
from wit_world import exports


def attribute(text):
    return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def row(key, value):
    value = json.dumps(value, separators=(",", ":"))
    return f'<row key="{key}" value="{attribute(value)}"/>'


class Run(exports.Run):
    def run(self) -> None:
        with open("/in/input.json") as f:
            params = json.load(f)
        if params["workload"] == "big":
            with open("/out/results.xml", "wb") as f:
                for _ in range(params["args"]["mib"]):
                    f.write(b" " * (1 << 20))
                f.write(b" ")
            return
        if params["workload"] == "hang":
            while True:
                pass
        if params["workload"] == "grow":
            held = []
            while True:
                held.append(bytearray(1 << 20))
        if params["workload"] == "raise":
            raise RuntimeError("boom")
        if params["workload"] == "refuse":
            with open("/out/error.json", "w") as f:
                json.dump({"code": "bad-arg", "message": "no", "deterministic": True}, f)
            sys.exit(1)
        if params["workload"] == "touch":
            with open("/in/input.json", "a") as f:
                f.write(" ")
        print('HEARTBEAT {"progress":1.0,"step":1,"steps":1}', file=sys.stderr)
        rows = row("environ", sorted(os.environ)) + row("in", sorted(os.listdir("/in")))
        with open("/out/results.xml", "w") as f:
            f.write(
                f'<results schema="wdf.sim.v1" seed="{params["seed"]}">'
                f'<block id="seen" kind="echo">{rows}</block></results>\\n'
            )
"""
MEMORY_MIB = 384


@pytest.fixture(scope="module")
def component(tmp_path_factory) -> Path:
    work = tmp_path_factory.mktemp("guest")
    tree = work / "tree"
    tree.mkdir()
    (tree / "pyproject.toml").write_text(
        '[project]\nname = "app"\nversion = "0"\nrequires-python = ">=3.13"\ndependencies = []\n'
    )
    (tree / "app.py").write_text(GUEST)
    subprocess.run(["uv", "lock", "--no-config", "-q", "--offline"], cwd=tree, check=True)
    return build(bundle.write(tree, work), "app", work / "built").component


def write_input(path: Path, workload="probe", args=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical({"args": args or {}, "seed": 7, "workload": workload}))
    return path


def seen(results: Path) -> dict:
    (block,) = read(results.open("rb"))["blocks"]
    return {r["key"]: json.loads(r["value"]) for r in block["rows"]}


def test_run_minimal_guest(component, tmp_path, monkeypatch):
    monkeypatch.setenv("WDF_UNSET_IN_GUEST", "visible-to-the-host")
    out = tmp_path / "out"
    argv = ["run", "--component", str(component), "--input", str(write_input(tmp_path / "i.json"))]
    done = subprocess.run(
        [sys.executable, "-m", "wdf_runtime", *argv, "--out", str(out)],
        capture_output=True,
        text=True,
    )
    print(done.stdout, done.stderr, sep="")
    assert (done.returncode, done.stdout) == (0, "class: ok\nheartbeats: 1\n")
    check_contract(out / "results.xml")
    assert seen(out / "results.xml") == {"environ": [], "in": ["input.json"]}


def test_input_copy_rehashed(component, tmp_path):
    source = write_input(tmp_path / "input.json")
    digest = copy_input(source, tmp_path / "copy")
    assert digest == input_digest(tmp_path / "copy")
    (tmp_path / "copy/guest-input/extra").write_text("")
    assert input_digest(tmp_path / "copy") != digest

    kept = run(component, source, tmp_path / "kept", MEMORY_MIB, 30)
    touched = run(
        component, write_input(tmp_path / "t.json", "touch"), tmp_path / "touched", MEMORY_MIB, 30
    )
    print(kept.classified, touched.classified, sep="\n")
    assert kept.classified.cls == "ok"
    assert touched.classified == ("guest_failed", True, "guest_failed: input-modified: /in changed")
    assert source.read_text() == canonical({"args": {}, "seed": 7, "workload": "probe"})


def test_cap_below_minimum_refused(component, tmp_path, monkeypatch):
    def no_wasmtime(*args, **kwargs):
        raise AssertionError("wasmtime started")

    monkeypatch.setattr(subprocess, "Popen", no_wasmtime)
    monkeypatch.setattr(subprocess, "run", no_wasmtime)
    below = GUEST_OVERHEAD_MIB + -(-GUEST_MIN_PAGES * PAGE // MIB) - 1
    ran = run(component, write_input(tmp_path / "i.json"), tmp_path / "s", below, 30, tmp_path)
    print(below, ran.classified)
    assert (ran.classified.cls, ran.classified.deterministic) == ("guest_memory_cap", True)
    assert f"below the {GUEST_MIN_PAGES} pages" in ran.classified.reason
    assert not (tmp_path / "s").exists()


def entries(cache: Path) -> set[Path]:
    modules = (cache / "wasmtime/modules").rglob("*")
    return {p for p in modules if p.is_file() and p.suffix != ".stats"}


def test_warm_up_fills_module_cache(component, tmp_path):
    cache = tmp_path / "cache"
    argv = warm_up(component, tmp_path / "w", cache)
    assert "timeout=31536000s" in " ".join(argv) and "max-memory-size" not in " ".join(argv)
    assert json.loads((tmp_path / "w/guest-input/input.json").read_text())["workload"] == "noop"
    assert not warmed(component, cache)
    warm(component, cache)
    filled = entries(cache)
    print(sorted(p.relative_to(cache) for p in filled))
    assert warmed(component, cache) and len(filled) == 1

    ran = run(component, write_input(tmp_path / "i.json"), tmp_path / "s", MEMORY_MIB, 30, cache)
    print(ran.classified)
    assert ran.classified.cls == "ok"
    assert entries(cache) == filled


def test_real_guest_outputs_meet_staging(component, tmp_path):
    scratch = make_scratch(tmp_path / "scratch")
    staging = contract()
    source = write_input(scratch / staging.directories.input / staging.input_file)
    ran = run(component, source, scratch, MEMORY_MIB, 30)
    assert ran.classified.cls == "ok"

    class Client:
        def put_content(self, path, kind, name, *args, **kwargs):
            return (kind, name, path.stat().st_size)

        put_attempt = put_content

    collected = collect_outputs(Client(), scratch, "simulate", {}, "tenant", "batch", seed=7)
    print(collected)
    declared = staging.task_types["simulate"].outputs
    assert [(r[0], r[1]) for r in collected.references] == [(o.kind, o.file) for o in declared]
    assert all(r[2] <= o.cap_bytes for r, o in zip(collected.references, declared, strict=True))
    assert collected.undeclared == []
    check_contract(scratch / staging.directories.output / "results.xml")


def test_over_cap_result_is_guest_failed(component, tmp_path):
    cap = contract().task_types["simulate"].outputs[0].cap_bytes
    source = write_input(tmp_path / "i.json", "big", {"mib": cap // MIB})
    ran = run(component, source, tmp_path / "s", MEMORY_MIB, 60)
    print(ran.classified)
    assert ran.classified == ("guest_failed", True, f"guest_failed: output-over-cap: {cap + 1}")


@pytest.mark.parametrize(
    "workload,expected",
    [
        ("hang", ("timeout", False, "timeout: 2: wasm trap: interrupt")),
        ("grow", ("guest_memory_cap", True, "guest_memory_cap: object type name: MemoryError")),
        ("raise", ("guest_failed", False, "guest_failed: exit 134: 2: wasm trap: wasm")),
        ("refuse", ("guest_failed", True, "guest_failed: bad-arg: no")),
    ],
)
def test_guest_failures_classified(component, tmp_path, workload, expected):
    ran = run(component, write_input(tmp_path / "i.json", workload), tmp_path / "s", 192, 2)
    print(ran.classified)
    assert ran.classified[:2] == expected[:2]
    assert ran.classified.reason.startswith(expected[2])
