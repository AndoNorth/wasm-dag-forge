"""Holds a session of two containers for a while and prints when, and is run by test_containers.py.

Environment: WDF_RUN_ID and WDF_T1_LOCK_DIR as for any session, HOLD_SECONDS, and
HOLD_CONTAINERS for the number of canaries to run while holding.
"""

import os
import sys
import time
from contextlib import ExitStack

from wdf_itest.session import Session


def main() -> None:
    hold = float(os.environ["HOLD_SECONDS"])
    count = int(os.environ["HOLD_CONTAINERS"])
    with Session(containers=2) as session, ExitStack() as stack:
        for i in range(count):
            stack.enter_context(
                session.container("canary", command=["sleep", "600"], name=f"canary-{i}")
            )
        print(f"held {time.time()}", flush=True)
        time.sleep(hold)
        print(f"released {time.time()}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise
