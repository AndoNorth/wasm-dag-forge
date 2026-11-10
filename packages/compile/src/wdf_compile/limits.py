"""Usage: python -m wdf_compile.limits fetch --bundle B.tar --find-links URL --out DIR [--timeout S]

The two steps of a compile with no network for componentize-py: `fetch` is the only step that
reaches the mirror, and runs no author code, then `python -m wdf_compile build --find-links DIR`
runs with the container network switched off. Exit codes are those of the build.
"""

import argparse
import shutil
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

from wdf_runtime.classify import Classified

from .build import CLASSES, EXIT_CODES, CompileError, _run
from .bundle import LOCKFILE, Refused, unpack
from .bundle import info as bundle_info

STUBBED = ()

# A process killed at the container memory limit, which the runner confirms with node events.
OOM_EXIT = 137
OOM_CLASS = "compile_oom"
REASON = "reason: "


def fetch(bundle: Path, find_links: str, dest: Path, timeout_s: float | None = None) -> list[Path]:
    """Downloads into dest every wheel of the locked dependencies that the mirror serves.

    The build installs from dest and checks every hash, so a wheel missing here fails there as
    compile_dependency_unavailable, and a dependency with only a source archive is never fetched.
    """
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    with tempfile.TemporaryDirectory(prefix="wdf-fetch-") as work:
        try:
            if bundle_info(bundle).lockfile_sha256 is None:
                raise Refused(f"the bundle has no {LOCKFILE}")
            unpack(bundle, Path(work))
        except Refused as refused:
            raise CompileError("compile_failed", str(refused)) from refused
        export = ["uv", "export", "--frozen", "--no-config", "--no-dev", "--no-emit-project", "-q"]
        hashes = {
            word.removeprefix("--hash=")
            for word in _run(export, "compile_failed", deadline, cwd=work).split()
            if word.startswith("--hash=")
        }
        lock = tomllib.loads((Path(work) / LOCKFILE).read_text())
    # Only the file name of a locked wheel is kept, so the step reaches the mirror and no other.
    names = sorted(
        PurePosixPath(wheel.get("url") or wheel["path"]).name
        for package in lock.get("package", [])
        for wheel in package.get("wheels", [])
        if wheel.get("hash") in hashes
    )
    dest.mkdir(parents=True, exist_ok=True)
    fetched = []
    for name in names:
        url = f"{find_links.rstrip('/')}/{name}"
        # ponytail: the deadline bounds each socket wait, so a mirror that trickles bytes can run
        # past it; copy in chunks against the deadline if a slow mirror matters.
        timeout = None if deadline is None else max(deadline - time.monotonic(), 0.001)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                with open(dest / name, "wb") as f:
                    shutil.copyfileobj(response, f)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                continue
            raise CompileError("compile_dependency_unavailable", f"{url}: {error}") from None
        except OSError as error:
            reason = getattr(error, "reason", error)
            if isinstance(reason, TimeoutError):
                raise CompileError("compile_timeout", f"{url} ran past the deadline") from None
            raise CompileError("compile_dependency_unavailable", f"{url}: {reason}") from None
        fetched.append(dest / name)
    return fetched


def classify(exit_code: int, stderr_tail: str = "") -> Classified:
    """The class of a failed fetch or build process, from the compile pool of the exit class table.

    The reason is the process's last `reason:` line, or the exit code when it wrote none.
    """
    by_code = {code: cls for cls, code in EXIT_CODES.items()}
    cls = OOM_CLASS if exit_code == OOM_EXIT else by_code.get(exit_code)
    if cls is None:
        raise ValueError(f"exit {exit_code} has no class in the compile pool")
    deterministic = next(c["deterministic"] for c in CLASSES["classes"] if c["class"] == cls)
    lines = [line for line in stderr_tail.splitlines() if line.startswith(REASON)]
    reason = lines[-1].removeprefix(REASON) if lines else f"{cls}: exit {exit_code}"
    return Classified(cls, deterministic, reason)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m wdf_compile.limits")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch_ = commands.add_parser("fetch")
    fetch_.add_argument("--bundle", type=Path, required=True)
    fetch_.add_argument("--find-links", required=True)
    fetch_.add_argument("--out", type=Path, required=True)
    fetch_.add_argument("--timeout", type=float)
    args = parser.parse_args(argv)
    try:
        fetched = fetch(args.bundle, args.find_links, args.out, args.timeout)
    except CompileError as error:
        print(f"{REASON}{error}", file=sys.stderr)
        return error.exit_code
    print(f"fetched: {len(fetched)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
