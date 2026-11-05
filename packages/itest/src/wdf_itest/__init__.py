"""The one helper that starts the containers of T1 tests.

A session takes the host-wide lock files it declares (one, or two when it holds two containers) for
its whole life, all or none, and runs as many containers at a time as files it holds, so the host
never runs more than two T1 containers whatever the number of workers.
Every container carries the run label, and closing one container removes only that container,
while closing the session removes every container that carries its own label.

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
