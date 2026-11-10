"""The limits of a compile: the deadline, an unreachable mirror, a refused source build, the fetch
step that lets the build run with no network, and the class of every exit of the compile pool."""

import hashlib
import importlib.util
import io
import os
import shutil
import socket
import subprocess
import tarfile
import time
from pathlib import Path

import pytest
import yaml
from wdf_compile import bundle, heavy, limits
from wdf_itest import image_ref
from wdf_itest.session import RUN_LABEL

# The project, wheel and served helpers of the build tests, loaded by path as the import mode needs.
_spec = importlib.util.spec_from_file_location(
    "build_tests", Path(__file__).with_name("test_build.py")
)
helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helpers)
ROOT = helpers.ROOT
TIMEOUT_S = 1.0
# An address of a documentation range, which no route leads to inside a container with no network.
OUTSIDE = ("192.0.2.1", 80)
PROBE = f"""
import os, socket
print("interfaces:", sorted(os.listdir("/sys/class/net")))
try:
    socket.create_connection({OUTSIDE!r}, timeout=2)
    print("connect: ok")
except OSError as error:
    print("connect:", os.strerror(error.errno))
"""


@pytest.fixture(scope="module")
def locked(tmp_path_factory):
    """A bundle that imports one pure wheel, locked against a loopback mirror, and that mirror."""
    work = tmp_path_factory.mktemp("locked")
    links = work / "links"
    links.mkdir()
    helpers.wheel(links, "purelib", {"purelib/__init__.py": b"VALUE = 42\n"})
    with helpers.served(links) as url:
        tree = helpers.project(work / "tree", "import purelib\n" + helpers.ENTRY, ["purelib"], url)
    return bundle.write(tree, work / "b"), links


def fetch(capsys, *argv: str) -> tuple[int, str, str]:
    code = limits.main(["fetch", *argv])
    captured = capsys.readouterr()
    return code, captured.out, (captured.err.strip().splitlines() or [""])[-1]


def closed_port() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}/"


def test_build_timeout_on_heavy_bundle(tmp_path, capsys):
    path = heavy.write(heavy.LARGEST_BUILDING_MIB, tmp_path / "b")
    start = time.monotonic()
    code, _, reason = helpers.compile_(capsys, path, tmp_path / "out", "--timeout", str(TIMEOUT_S))
    elapsed = time.monotonic() - start
    with capsys.disabled():
        print(f"{heavy.LARGEST_BUILDING_MIB} MiB: exit {code} after {elapsed:.2f} s, {reason}")
    assert code == 5
    assert reason == "reason: compile_timeout: componentize-py ran past the deadline"
    assert elapsed < TIMEOUT_S + 2
    assert not (tmp_path / "out/component.wasm").exists()


def test_fetch_timeout_on_silent_mirror(locked, tmp_path, capsys):
    path, _ = locked
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        url = f"http://127.0.0.1:{silent.getsockname()[1]}/"
        start = time.monotonic()
        code, _, reason = fetch(
            capsys,
            "--bundle",
            str(path),
            "--find-links",
            url,
            "--out",
            str(tmp_path / "w"),
            "--timeout",
            str(TIMEOUT_S),
        )
        elapsed = time.monotonic() - start
    with capsys.disabled():
        print(f"silent mirror: exit {code} after {elapsed:.2f} s, {reason}")
    assert code == 5
    assert reason.startswith("reason: compile_timeout: ") and "purelib-1.0" in reason
    assert elapsed < TIMEOUT_S + 2


def test_dependency_unavailable(locked, tmp_path, capsys):
    path, _ = locked
    url = closed_port()
    fetched = fetch(
        capsys, "--bundle", str(path), "--find-links", url, "--out", str(tmp_path / "w")
    )
    built = helpers.compile_(capsys, path, tmp_path / "out", "--find-links", url)
    for step, (code, _, reason) in {"fetch": fetched, "build": built}.items():
        with capsys.disabled():
            print(f"{step} from {url}: exit {code}, {reason}")
        assert code == 4, (step, reason)
        assert reason.startswith("reason: compile_dependency_unavailable: "), (step, reason)
    assert "Connection refused" in fetched[2]


def sdist(links: Path, name: str, marker: Path) -> Path:
    """A source archive with static metadata, whose build backend writes marker when it runs."""
    files = {
        "pyproject.toml": (
            f'[project]\nname = "{name}"\nversion = "1.0"\n\n[build-system]\nrequires = []\n'
            'build-backend = "backend"\nbackend-path = ["."]\n'
        ),
        "backend.py": f"open({str(marker)!r}, 'w').write('built')\n",
        "PKG-INFO": f"Metadata-Version: 2.2\nName: {name}\nVersion: 1.0\n",
    }
    path = links / f"{name}-1.0.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for member, text in files.items():
            entry = tarfile.TarInfo(f"{name}-1.0/{member}")
            entry.size = len(text.encode())
            tar.addfile(entry, io.BytesIO(text.encode()))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (links / "index.html").write_text(f'<a href="{path.name}#sha256={digest}">{path.name}</a>\n')
    return path


def test_source_build_refused(tmp_path, capsys):
    links, marker = tmp_path / "links", tmp_path / "built"
    links.mkdir()
    sdist(links, "srclib", marker)
    with helpers.served(links) as url:
        tree = helpers.project(
            tmp_path / "tree", "import srclib\n" + helpers.ENTRY, ["srclib"], url
        )
        path = bundle.write(tree, tmp_path / "b")
        fetched = fetch(
            capsys, "--bundle", str(path), "--find-links", url, "--out", str(tmp_path / "w")
        )
    code, _, reason = helpers.compile_(capsys, path, tmp_path / "out", "--find-links", str(links))
    with capsys.disabled():
        print(f"fetch: exit {fetched[0]}, {fetched[1].strip()}")
        print(f"build: exit {code}, {reason}")
    assert fetched[:2] == (0, "fetched: 0\n")
    assert code == 4
    assert reason.startswith("reason: compile_dependency_unavailable: uv: ")
    assert "building from source is disabled" in reason
    assert not marker.exists()

    # With source builds allowed the same install runs the backend, so the refusal kept it out.
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "-q",
            "--no-config",
            "--no-cache",
            "--target",
            str(tmp_path / "t"),
            "--no-index",
            "--find-links",
            str(links),
            "srclib",
        ],
        capture_output=True,
    )
    assert marker.read_text() == "built"


@pytest.mark.integration
def test_no_network_step(t1, locked, tmp_path, capsys):
    """The fetch step reaches the mirror, then the whole build, componentize-py included, runs in a
    container with its network switched off, from the toolchain of this environment."""
    links = locked[1]
    path = Path(shutil.copy(locked[0], tmp_path))
    wheels, out = tmp_path / "wheels", tmp_path / "out"
    with helpers.served(links) as url:
        fetched = fetch(capsys, "--bundle", str(path), "--find-links", url, "--out", str(wheels))
    assert fetched[:2] == (0, "fetched: 1\n"), fetched

    name = f"wdf-{t1.run_id}-compile"
    run = ["docker", "run", "-d", "--rm", "--name", name, "--label", f"{RUN_LABEL}={t1.run_id}"]
    run += ["--network", "none", "--memory", "1g", "--memory-swap", "1g", "--cpus", "1"]
    run += [
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-e",
        "HOME=/tmp",
        "-e",
        f"PATH={os.environ['PATH']}",
    ]
    run += ["--tmpfs", "/tmp:rw,mode=1777,size=256m"]
    for src, writable in (("/nix/store", False), (ROOT, False), (tmp_path, True)):
        run += ["--mount", f"type=bind,src={src},dst={src}" + ("" if writable else ",readonly")]
    helpers.run(*run, image_ref("canary"), "sleep", "600")
    try:
        mode = helpers.run("docker", "inspect", "-f", "{{.HostConfig.NetworkMode}}", name).stdout
        python = str(ROOT / ".venv/bin/python")
        probe = helpers.run("docker", "exec", name, python, "-c", PROBE).stdout
        build = subprocess.run(
            [
                "docker",
                "exec",
                "-w",
                str(tmp_path),
                name,
                python,
                "-m",
                "wdf_compile",
                "build",
                "--bundle",
                str(path),
                "--entry",
                "app",
                "--out",
                str(out),
                "--find-links",
                str(wheels),
            ],
            capture_output=True,
            text=True,
        )
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    with capsys.disabled():
        print(f"fetch: {fetched[1].strip()}, network mode: {mode.strip()}")
        print(probe.strip())
        print(f"build: exit {build.returncode}, {build.stdout.strip()}")
    assert mode.strip() == "none"
    assert "interfaces: ['lo']" in probe
    assert "connect: Network is unreachable" in probe
    assert build.returncode == 0, build.stderr
    assert build.stdout.startswith("cache_key: ")
    assert (out / "component.wasm").stat().st_size > 0


def test_exit_137_classifies_as_compile_oom():
    rows = {row["class"]: row for row in compile_rows()}
    assert "exit_code" not in rows["compile_oom"]
    assert limits.classify(137) == (
        "compile_oom",
        rows["compile_oom"]["deterministic"],
        "compile_oom: exit 137",
    )


def test_every_native_code_classifies():
    for row in compile_rows():
        if "exit_code" in row:
            stderr = f"noise\nreason: {row['class']}: detail\n"
            assert limits.classify(row["exit_code"], stderr) == (
                row["class"],
                row["deterministic"],
                f"{row['class']}: detail",
            )
    for code in (0, 1, 2):
        with pytest.raises(ValueError, match=f"exit {code} has no class"):
            limits.classify(code)


def compile_rows() -> list[dict]:
    table = yaml.safe_load((ROOT / "contracts/workload/exit-classes.yaml").read_text())
    return table["pools"]["compile"]["classes"]
