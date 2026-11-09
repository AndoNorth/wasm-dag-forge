"""The build procedure: unpack, install the locked dependencies, componentize and check."""

import hashlib
import re
import subprocess
import sysconfig
import tempfile
import time
from dataclasses import dataclass
from importlib import metadata, resources
from pathlib import Path

import yaml
from wdf_keys import compile_cache_key
from wdf_runtime.contract import canonical
from wdf_runtime.models import CONTRACT, BuildInfo
from wdf_runtime.pins import COMPONENTIZE_PY_VERSION, WIT_VERSION

from .bundle import LOCKFILE, Refused, unpack
from .bundle import info as bundle_info

STUBBED = ()

# The CPython that componentize-py embeds in every component, fixed by its version.
GUEST_PYTHON = "3.14.0"
WORLD = f"wdf:task/task@{WIT_VERSION}"
COMPONENT = "component.wasm"
BUILD_INFO = "build-info.json"
CLASSES = yaml.safe_load((CONTRACT / "exit-classes.yaml").read_text())["pools"]["compile"]
EXIT_CODES = {c["class"]: c["exit_code"] for c in CLASSES["classes"] if "exit_code" in c}
DETAIL_CHARS = 400
# The top-level imports and exports of the world `wasm-tools component wit` prints for a component.
WORLD_ITEM = re.compile(r"^  (import|export) ([^\s;]+?)(?:;|: )", re.MULTILINE)


class CompileError(Exception):
    """A build that fails with a class of the compile pool."""

    def __init__(self, cls: str, detail: str):
        super().__init__(f"{cls}: {detail}")
        self.cls = cls
        self.exit_code = EXIT_CODES[cls]


@dataclass(frozen=True)
class Built:
    component: Path
    build_info: Path
    cache_key: str


def build(
    bundle: Path,
    entry: str,
    out: Path,
    find_links: str | None = None,
    timeout_s: float | None = None,
) -> Built:
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    timings = {}
    clock = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="wdf-compile-") as work:
        src, site = Path(work) / "src", Path(work) / "site"
        try:
            info = bundle_info(bundle)
            if info.lockfile_sha256 is None:
                raise Refused(f"the bundle has no {LOCKFILE}")
            if entry not in info.modules:
                raise Refused(f"{entry} is not a module of the bundle")
            unpack(bundle, src)
        except Refused as refused:
            raise CompileError("compile_failed", str(refused)) from refused
        timings["unpack"], clock = _lap(clock)

        _install(src, site, Path(work) / "requirements.txt", find_links, deadline)
        timings["install"], clock = _lap(clock)

        out.mkdir(parents=True, exist_ok=True)
        component = out / COMPONENT
        with resources.as_file(CONTRACT / "wit") as wit:
            _componentize(wit, src, site, entry, component, deadline)
            timings["componentize"], clock = _lap(clock)
            imports, exports = _check(wit, component, deadline)
            timings["check"], clock = _lap(clock)

    inputs = {
        "source_hash": info.source_sha256,
        "lockfile_hash": info.lockfile_sha256,
        "componentize_py_version": COMPONENTIZE_PY_VERSION,
        "wit_version": WIT_VERSION,
        "entry": entry,
    }
    cache_key = compile_cache_key(**inputs)
    with open(component, "rb") as f:
        sha256 = hashlib.file_digest(f, "sha256").hexdigest()
    document = BuildInfo.model_validate(
        {
            "schema": "wdf.build-info/1",
            "cache_key": cache_key,
            "inputs": inputs,
            "tools": {
                "componentize_py": metadata.version("componentize-py"),
                "wasm_tools": _version("wasm-tools", deadline),
                "uv": _version("uv", deadline),
                "guest_python": GUEST_PYTHON,
            },
            "component": {"sha256": sha256, "size": component.stat().st_size},
            "imports": imports,
            "exports": exports,
            "timings_s": timings,
        }
    ).model_dump(mode="json", by_alias=True)
    build_info = out / BUILD_INFO
    build_info.write_text(canonical(document))
    return Built(component, build_info, cache_key)


def _install(src: Path, site: Path, requirements: Path, find_links, deadline) -> None:
    export = ["uv", "export", "--frozen", "--no-config", "--no-dev", "--no-emit-project"]
    _run([*export, "-q", "-o", str(requirements)], "compile_failed", deadline, cwd=src)
    install = [
        "uv",
        "pip",
        "install",
        "-q",
        "--no-config",
        "--no-cache",
        "--target",
        str(site),
        "--no-index",
    ]
    if find_links is not None:
        install += ["--find-links", find_links]
    install += ["--require-hashes", "--no-build", "--python-version", "3.14"]
    _run([*install, "-r", str(requirements)], "compile_dependency_unavailable", deadline)


def _componentize(wit: Path, src: Path, site: Path, entry: str, component: Path, deadline):
    componentize_py = str(Path(sysconfig.get_path("scripts")) / "componentize-py")
    argv = [componentize_py, "-q", "-d", str(wit), "-w", WORLD, "componentize"]
    argv += ["-p", str(src), "-p", str(site), entry, "-o", str(component)]
    _run(argv, "compile_failed", deadline, cwd=src)


def _check(wit: Path, component: Path, deadline) -> tuple[list[str], list[str]]:
    """Refuses a component that imports anything beyond the world or lacks one of its exports."""
    _run(
        ["wasm-tools", "component", "targets", "-w", WORLD, str(wit), str(component)],
        "compile_failed",
        deadline,
    )
    world = _run(["wasm-tools", "component", "wit", str(component)], "compile_failed", deadline)
    items = WORLD_ITEM.findall(world.split("\n}\n", 1)[0])
    return [n for k, n in items if k == "import"], [n for k, n in items if k == "export"]


def _version(tool: str, deadline) -> str:
    return _run([tool, "--version"], "compile_failed", deadline).split()[1]


def _run(argv: list[str], cls: str, deadline: float | None, **kwargs) -> str:
    timeout = None if deadline is None else max(deadline - time.monotonic(), 0)
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired:
        raise CompileError(
            "compile_timeout", f"{Path(argv[0]).name} ran past the deadline"
        ) from None
    if result.returncode != 0:
        # The tail of stderr on one line, since the cause comes last and the reason is one line.
        detail = " ".join(result.stderr.split())[-DETAIL_CHARS:] or f"exit {result.returncode}"
        raise CompileError(cls, f"{Path(argv[0]).name}: {detail}")
    return result.stdout


def _lap(start: float) -> tuple[float, float]:
    now = time.monotonic()
    return round(now - start, 3), now
