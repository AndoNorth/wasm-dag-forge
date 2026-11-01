"""The library surface of the package, raising until the owning lane implements each function."""

OWNER = "0"
# Functions that are not implemented yet, each emptied by the lane that delivers it.
STUBBED = ("apply", "status", "check")


def _not_implemented() -> NotImplementedError:
    return NotImplementedError(f"not implemented: owned by lane {OWNER}")


def apply(*args, **kwargs):
    raise _not_implemented()


def status(*args, **kwargs):
    raise _not_implemented()


def check(*args, **kwargs):
    raise _not_implemented()
