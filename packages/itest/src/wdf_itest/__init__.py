"""The one helper that starts the containers of T1 tests.

A session takes one of two host-wide lock files for its whole life and runs one container at a time,
so the host never runs more than two T1 containers whatever the number of workers.
Every container carries the run label, and cleanup removes only containers that carry its own.

Environment:
  WDF_SLOT              worker slot 0 to 9, default 0
  WDF_RUN_ID            run id, default <UTC timestamp>-<slot>
  WDF_T1_LOCK_DIR       directory of the two lock files, default /tmp
  WDF_T1_LOCK_TIMEOUT   seconds to wait for a free lock file, default 1800
"""

from .session import (
    DockerUnavailable,
    LockTimeout,
    Session,
    image_ref,
    lock_paths,
)

__all__ = ["DockerUnavailable", "LockTimeout", "Session", "image_ref", "lock_paths"]
