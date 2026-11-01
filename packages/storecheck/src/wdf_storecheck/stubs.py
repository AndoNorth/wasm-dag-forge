"""The library surface of the package, raising until the owning lane implements each function."""

OWNER = "C"
# Functions that are not implemented yet, each emptied by the lane that delivers it.
STUBBED = ("run",)


def _not_implemented() -> NotImplementedError:
    return NotImplementedError(f"not implemented: owned by lane {OWNER}")


def run(*args, **kwargs):
    raise _not_implemented()
