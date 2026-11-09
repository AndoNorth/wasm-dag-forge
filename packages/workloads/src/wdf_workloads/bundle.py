"""Usage: python -m wdf_workloads.bundle --out DIR

Writes DIR/bundle.tar with wdf_kernel, wdf_workloads, a pyproject.toml and a uv.lock with no
dependencies.
"""

import sys

from ._stub import not_implemented

STUBBED = ("main",)


def main(argv: list[str]) -> int:
    raise not_implemented("W")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
