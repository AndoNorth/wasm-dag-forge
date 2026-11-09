"""Writes a stand-in bundle tree for componentize-py.

The tree holds `wdf_workloads/main.py` with the `Run` export class and generated source of about
N MiB in K modules `wdf_kernel/gen<k>.py`, imported at the top of the entry.

    python3 gen.py --size-mib N [--modules K] [--hold-mib H] --out DIR

`--hold-mib` makes the first module fill H MiB at import time and keep only its length, to probe
the guest linear memory that the build snapshots rather than the source size.
"""

import argparse
from pathlib import Path

MIB = 1048576

ENTRY = """\
import hashlib
import json
import struct
import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

from wit_world import exports

from wdf_kernel import gen0 as gen
{imports}

class Run(exports.Run):
    def run(self) -> None:
        with open("/in/input.json", "rb") as f:
            params = json.load(f)
        seed = params["seed"]
        acc = gen.f0(seed)
        digest = hashlib.sha256(struct.pack(">Q", acc & 0xFFFFFFFFFFFFFFFF)).hexdigest()
        root = ET.Element("results", schema="wdf.sim.v1", seed=str(seed))
        ET.SubElement(ET.SubElement(root, "block", id="d", kind="digest"), "row", value=digest)
        with open("/out/results.xml", "wb") as f:
            f.write(ET.tostring(root, xml_declaration=True, encoding="utf-8"))
        print(quoteattr(digest), time.monotonic() >= 0)
"""

# Each function is kernel-like code: 64-bit arithmetic, a loop, a branch and a table lookup.
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


def splitmix(state):
    state = (state + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
    z = state
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    return state, z ^ (z >> 31)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--size-mib", type=float, required=True)
    parser.add_argument("--modules", type=int, default=1)
    parser.add_argument("--hold-mib", type=int, default=0)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    (out / "wdf_workloads").mkdir(parents=True)
    (out / "wdf_kernel").mkdir()
    (out / "wdf_workloads" / "__init__.py").write_text("")
    (out / "wdf_kernel" / "__init__.py").write_text("")
    imports = "".join(
        f"from wdf_kernel import gen{k}  # noqa: F401\n" for k in range(1, args.modules)
    )
    (out / "wdf_workloads" / "main.py").write_text(ENTRY.replace("{imports}", imports))
    target = int(args.size_mib * MIB / args.modules)
    state, i = 1, 0
    for k in range(args.modules):
        state, i = write_module(out / "wdf_kernel" / f"gen{k}.py", target, state, i)
    if args.hold_mib:
        with open(out / "wdf_kernel" / "gen0.py", "a") as f:
            f.write(f'HELD = len(b"\\x01" * {args.hold_mib * MIB})\n')
    modules = (out / "wdf_kernel").glob("gen*.py")
    print(f"functions {i} source_bytes {sum(p.stat().st_size for p in modules)}")


def write_module(path, target, state, i):
    """Writes functions from index `i` until the module holds `target` bytes.

    Returns the next state and index.
    """
    size = 0
    with open(path, "w") as f:
        while size < target:
            values = []
            for _ in range(6):
                state, v = splitmix(state)
                values.append(v)
            a, b, c, d, e, g = values
            chunk = FUNCTION.format(i=i, a=a, b=b | 1, c=c, d=d, e=e, f=g)
            f.write(chunk)
            size += len(chunk)
            i += 1
    return state, i


if __name__ == "__main__":
    main()
