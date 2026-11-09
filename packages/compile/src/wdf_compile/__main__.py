"""Usage: python -m wdf_compile build --bundle B.tar --entry MODULE --out DIR [--find-links URL]
                                    [--timeout S]
python -m wdf_compile info B.tar

Exit codes from the compile pool of contracts/workload/exit-classes.yaml.
"""

import argparse
import sys
from pathlib import Path

from wdf_runtime.contract import canonical

from .build import CompileError, build
from .bundle import Refused, info

STUBBED = ()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m wdf_compile")
    commands = parser.add_subparsers(dest="command", required=True)
    build_ = commands.add_parser("build")
    build_.add_argument("--bundle", type=Path, required=True)
    build_.add_argument("--entry", required=True)
    build_.add_argument("--out", type=Path, required=True)
    build_.add_argument("--find-links")
    build_.add_argument("--timeout", type=float)
    info_ = commands.add_parser("info")
    info_.add_argument("bundle", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "info":
            found = info(args.bundle)
            print(canonical({**vars(found), "modules": sorted(found.modules)}), end="")
        else:
            built = build(args.bundle, args.entry, args.out, args.find_links, args.timeout)
            print(f"cache_key: {built.cache_key}")
    except Refused as refused:
        error = CompileError("compile_failed", str(refused))
        print(f"reason: {error}", file=sys.stderr)
        return error.exit_code
    except CompileError as error:
        print(f"reason: {error}", file=sys.stderr)
        return error.exit_code
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
