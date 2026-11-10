"""Usage: python -m wdf_compile.heavy --size-mib N --out DIR

Writes DIR/bundle.tar, whose entry module app imports one generated module of about N MiB of source.
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from . import bundle

STUBBED = ()

ENTRY = "app"
MIB = 1024 * 1024
# The sizes measured in the ci compile slot with componentize-py 0.25.1: the largest generated
# module that builds, which still runs past a short --timeout, and the smallest whose build
# snapshot exhausts the hostcall fuel of componentize-py, a deterministic compile_failed.
LARGEST_BUILDING_MIB = 2.25
FUEL_EXHAUSTED_MIB = 2.5

APP = """\
import generated
from wit_world import exports


class Run(exports.Run):
    def run(self) -> None:
        print(generated.f0(1))
"""
# Kernel-like code: 64-bit arithmetic, a loop, a branch and a table lookup, as the sizes were
# measured with.
FUNCTION = '''\
def f{i}(x):
    """Mixes x with the constants of step {i}."""
    acc = (x ^ 0x{a:016x}) & 0xFFFFFFFFFFFFFFFF
    for k in range(3):
        acc = (acc * 0x{b:016x} + k) & 0xFFFFFFFFFFFFFFFF
        if acc & 1:
            acc ^= acc >> 31
        else:
            acc = (acc << 7 | acc >> 57) & 0xFFFFFFFFFFFFFFFF
    table = ({c}, {d}, {e}, {f})
    return acc ^ table[acc & 3]


'''
MASK = 0xFFFFFFFFFFFFFFFF


def write(size_mib: float, out: Path) -> Path:
    with tempfile.TemporaryDirectory(prefix="wdf-heavy-") as work:
        tree = Path(work)
        (tree / "pyproject.toml").write_text(
            '[project]\nname = "heavy"\nversion = "0"\nrequires-python = ">=3.13"\n'
            "dependencies = []\n"
        )
        (tree / f"{ENTRY}.py").write_text(APP)
        (tree / "generated.py").write_text(generated(int(size_mib * MIB)))
        subprocess.run(
            ["uv", "lock", "--no-config", "--offline", "-q"], cwd=tree, check=True, timeout=60
        )
        return bundle.write(tree, out)


def generated(size: int) -> str:
    """Functions from a fixed seed until the source holds at least `size` bytes."""
    chunks, total, state, i = [], 0, 1, 0
    while total < size:
        values = []
        for _ in range(6):
            state, value = _splitmix(state)
            values.append(value)
        a, b, c, d, e, f = values
        chunks.append(FUNCTION.format(i=i, a=a, b=b | 1, c=c, d=d, e=e, f=f))
        total += len(chunks[-1])
        i += 1
    return "".join(chunks)


def _splitmix(state: int) -> tuple[int, int]:
    state = (state + 0x9E3779B97F4A7C15) & MASK
    z = ((state ^ (state >> 30)) * 0xBF58476D1CE4E5B9) & MASK
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK
    return state, z ^ (z >> 31)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m wdf_compile.heavy")
    parser.add_argument("--size-mib", type=float, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    write(args.size_mib, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
