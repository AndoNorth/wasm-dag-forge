"""The build procedure against the real componentize-py, uv and wasm-tools: a bundle builds into a
component that targets world task, and every refusal exits with its class of the compile pool."""

import base64
import functools
import hashlib
import http.server
import importlib
import json
import platform
import shutil
import subprocess
import sys
import tarfile
import threading
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from wdf_compile import bundle
from wdf_compile.__main__ import main

# The package exports the build function under the name of its module.
building = importlib.import_module("wdf_compile.build")
ROOT = Path(__file__).resolve().parents[3]
WIT = ROOT / "contracts/workload/wit"

ENTRY = """\
import json
from wit_world import exports


class Run(exports.Run):
    def run(self) -> None:
        with open("/in/input.json") as f:
            seed = json.load(f)["seed"]
        with open("/out/results.xml", "w") as f:
            f.write(f'<results schema="wdf.sim.v1" seed="{seed}"/>\\n')
"""
NO_RUN = "import json\n\nVALUE = json.dumps(1)\n"


def project(root: Path, entry: str, dependencies=(), index_url=None) -> Path:
    """A bundle tree with app.py as its entry and a uv.lock resolved by the real uv."""
    root.mkdir(parents=True)
    deps = ", ".join(f'"{d}"' for d in dependencies)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "app"\nversion = "0"\nrequires-python = ">=3.13"\n'
        f"dependencies = [{deps}]\n"
    )
    (root / "app.py").write_text(entry)
    source = ["--no-index", "--find-links", index_url] if index_url else ["--offline"]
    run("uv", "lock", "--no-config", "-q", *source, cwd=root)
    return root


def run(*argv, **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(argv, capture_output=True, text=True, **kwargs)
    assert result.returncode == 0, f"{argv}: {result.stdout}{result.stderr}"
    return result


def wheel(links: Path, name: str, files: dict[str, bytes], tag="py3-none-any") -> Path:
    """A hand-made wheel, and a find-links page that carries the SHA-256 of every wheel."""
    dist = f"{name}-1.0.dist-info"
    files = {
        **files,
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n".encode(),
        f"{dist}/WHEEL": (
            f"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: {tag}\n"
        ).encode(),
    }

    def digest(data: bytes) -> str:
        return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()

    record = "".join(f"{p},sha256={digest(d)},{len(d)}\n" for p, d in files.items())
    path = links / f"{name}-1.0-{tag}.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for member, data in files.items():
            archive.writestr(member, data)
        archive.writestr(f"{dist}/RECORD", record + f"{dist}/RECORD,,\n")
    page = "".join(
        f'<a href="{w.name}#sha256={hashlib.sha256(w.read_bytes()).hexdigest()}">{w.name}</a>\n'
        for w in sorted(links.glob("*.whl"))
    )
    (links / "index.html").write_text(page)
    return path


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@contextmanager
def served(directory: Path):
    """The directory over HTTP on loopback, so uv lock records the hash of each wheel."""
    handler = functools.partial(Quiet, directory=directory)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


def compile_(capsys, bundle_path: Path, out: Path, *extra: str) -> tuple[int, str, str]:
    code = main(
        ["build", "--bundle", str(bundle_path), "--entry", "app", "--out", str(out), *extra]
    )
    captured = capsys.readouterr()
    return code, captured.out, (captured.err.strip().splitlines() or [""])[-1]


@pytest.fixture(scope="module")
def minimal(tmp_path_factory):
    """One bundle of a ten-line entry, built twice."""
    work = tmp_path_factory.mktemp("minimal")
    path = bundle.write(project(work / "tree", ENTRY), work)
    builds = [building.build(path, "app", work / f"c{n}") for n in (1, 2)]
    return path, builds


def test_build_minimal_bundle(minimal, capsys, tmp_path):
    path, (built, _) = minimal
    assert len(ENTRY.splitlines()) == 10
    code, out, reason = compile_(capsys, path, tmp_path / "c")
    assert (code, reason) == (0, "")
    assert out == f"cache_key: {built.cache_key}\n"
    targets = subprocess.run(
        ["wasm-tools", "component", "targets", str(WIT), str(built.component)],
        capture_output=True,
        text=True,
    )
    assert targets.returncode == 0, targets.stderr
    loader = subprocess.run(
        [sys.executable, "-m", "wdf_runtime.contract", "--check", str(built.build_info)],
        capture_output=True,
        text=True,
    )
    assert loader.returncode == 0, loader.stderr
    info = json.loads(built.build_info.read_text())
    assert info["cache_key"] == built.cache_key
    assert info["inputs"]["source_hash"] == bundle.info(path).source_sha256
    assert info["component"]["sha256"] == hashlib.sha256(built.component.read_bytes()).hexdigest()
    assert "wasi:filesystem/preopens@0.2.9" in info["imports"]
    assert "wasi:cli/run@0.2.9" in info["exports"]
    assert f"{info['tools']['guest_python']} (main".encode() in built.component.read_bytes()
    with capsys.disabled():
        print(f"component {info['component']}, timings_s {info['timings_s']}")


def test_rebuild_same_key(minimal):
    _, (first, second) = minimal
    assert first.cache_key == second.cache_key
    hashes = [json.loads(b.build_info.read_text())["component"]["sha256"] for b in minimal[1]]
    print(f"cache_key {first.cache_key}, component sha256 {hashes[0]} and {hashes[1]}")


def extended_wit(root: Path) -> Path:
    """World task with one import beyond the command world."""
    shutil.copytree(WIT, root)
    task = (root / "task.wit").read_text()
    task = task.replace("world task {", "interface probe {\n  ping: func();\n}\n\nworld task {")
    (root / "task.wit").write_text(task.replace("0.2.9;\n}", "0.2.9;\n  import probe;\n}"))
    return root


def tar_with(path: Path, tree: Path, extra: tarfile.TarInfo) -> Path:
    with tarfile.open(path, "w") as tar:
        for member in ("pyproject.toml", "uv.lock", "app.py"):
            tar.add(tree / member, member)
        tar.addfile(extra)
    return path


def link_entry(name: str, kind: bytes) -> tarfile.TarInfo:
    entry = tarfile.TarInfo(name)
    entry.type, entry.linkname = kind, "app.py"
    return entry


def test_compile_failed_cases(tmp_path, capsys, monkeypatch):
    links = tmp_path / "links"
    links.mkdir()
    machine = platform.machine()
    wheel(
        links,
        "nativelib",
        {
            "nativelib/__init__.py": b"from ._native import VALUE\n",
            f"nativelib/_native.cpython-314-{machine}-linux-gnu.so": b"\x7fELF",
        },
        tag=f"cp314-cp314-manylinux_2_17_{machine}",
    )
    plain = project(tmp_path / "plain", ENTRY)
    with served(links) as url:
        native = project(tmp_path / "native", "import nativelib\n" + ENTRY, ["nativelib"], url)
    cases = {
        "missing-run": (bundle.write(project(tmp_path / "no-run", NO_RUN), tmp_path / "a"), ()),
        "native-wheel": (bundle.write(native, tmp_path / "b"), ("--find-links", str(links))),
        "extra-wit-import": (bundle.write(plain, tmp_path / "c"), ()),
        "parent-path": (tar_with(tmp_path / "d.tar", plain, tarfile.TarInfo("../x.py")), ()),
        "symlink": (tar_with(tmp_path / "e.tar", plain, link_entry("s.py", tarfile.SYMTYPE)), ()),
        "hardlink": (tar_with(tmp_path / "f.tar", plain, link_entry("h.py", tarfile.LNKTYPE)), ()),
    }
    componentize = building._componentize
    wider = extended_wit(tmp_path / "wit")
    results = {}
    for case, (path, extra) in cases.items():
        if case == "extra-wit-import":
            monkeypatch.setattr(
                building, "_componentize", lambda _, *rest: componentize(wider, *rest)
            )
        results[case] = compile_(capsys, path, tmp_path / f"out-{case}", *extra)
        monkeypatch.setattr(building, "_componentize", componentize)
    for case, (code, _, reason) in results.items():
        with capsys.disabled():
            print(f"{case}: exit {code}, {reason}")
        assert code == 3, (case, reason)
        assert reason.startswith("reason: compile_failed: "), (case, reason)
    assert "has no attribute 'Run'" in results["missing-run"][2]
    assert "nativelib" in results["native-wheel"][2]
    assert "componentize-py" not in results["extra-wit-import"][2]
    assert "wasm-tools" in results["extra-wit-import"][2]
    assert "leaves the bundle root" in results["parent-path"][2]
    assert "is a link" in results["symlink"][2] and "is a link" in results["hardlink"][2]


def test_locked_dependency_install(tmp_path, capsys):
    links = tmp_path / "links"
    links.mkdir()
    pure = wheel(links, "purelib", {"purelib/__init__.py": b"VALUE = 42\n"})
    with served(links) as url:
        tree = project(tmp_path / "tree", "import purelib\n" + ENTRY, ["purelib"], url)
    assert (
        "--hash=sha256:"
        in run("uv", "export", "--frozen", "--no-config", "--no-emit-project", cwd=tree).stdout
    )
    path = bundle.write(tree, tmp_path / "b")
    code, out, reason = compile_(capsys, path, tmp_path / "ok", "--find-links", str(links))
    with capsys.disabled():
        print(f"locked install: exit {code}, {out.strip()}")
    assert (code, reason) == (0, "")

    with zipfile.ZipFile(pure, "a") as archive:
        archive.writestr("purelib/extra.py", b"TAMPERED = True\n")
    code, _, reason = compile_(capsys, path, tmp_path / "tampered", "--find-links", str(links))
    with capsys.disabled():
        print(f"tampered wheel: exit {code}, {reason}")
    assert code == 4
    assert reason.startswith("reason: compile_dependency_unavailable: uv: ")
    assert "hash" in reason.lower()
