"""Usage: python -m wdf_runtime run --component C --input input.json --out DIR [--memory-mib N]
                                 [--timeout-s S] [--cache-dir D]

Prints "class: <class>" and "heartbeats: <n>". Exit 0 for ok, 1 for any other class, 2 usage.
The guest's output goes to stderr, and the last stderr line of a failed run is its reason.
"""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from .command import OUTPUT
from .run import run

STUBBED = ()

# The memory of a pod of the simulate pool in the ci preset.
DEFAULT_MEMORY_MIB = 384


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m wdf_runtime")
    commands = parser.add_subparsers(dest="command", required=True)
    run_ = commands.add_parser("run")
    run_.add_argument("--component", type=Path, required=True)
    run_.add_argument("--input", type=Path, required=True)
    run_.add_argument("--out", type=Path, required=True)
    run_.add_argument("--memory-mib", type=int, default=DEFAULT_MEMORY_MIB)
    run_.add_argument("--timeout-s", type=float)
    run_.add_argument("--cache-dir", type=Path)
    args = parser.parse_args(argv)
    if not args.input.is_file():
        parser.error(f"--input {args.input} is not a file")
    with tempfile.TemporaryDirectory(prefix="wdf-run-") as scratch:
        ran = run(
            args.component,
            args.input,
            Path(scratch),
            args.memory_mib,
            args.timeout_s,
            args.cache_dir,
        )
        args.out.mkdir(parents=True, exist_ok=True)
        output = Path(scratch) / OUTPUT
        if output.is_dir():
            shutil.copytree(output, args.out, symlinks=True, dirs_exist_ok=True)
    sys.stderr.write(ran.tail.decode(errors="replace"))
    print(f"class: {ran.classified.cls}")
    print(f"heartbeats: {ran.heartbeats}")
    if ran.classified.cls != "ok":
        print(f"reason: {ran.classified.reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
