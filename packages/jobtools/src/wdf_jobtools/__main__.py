"""python -m wdf_jobtools validate|plan|patch-check."""

import sys

from .cli import main

sys.exit(main(sys.argv[1:]))
