"""The library surface of the package, raising until the owning lane implements each function."""

OWNER = "Q"
# Functions that are not implemented yet, each emptied by the lane that delivers it.
STUBBED = ("collect_inputs",)


def _not_implemented() -> NotImplementedError:
    return NotImplementedError(f"not implemented: owned by lane {OWNER}")


def collect_inputs(*args, **kwargs):
    raise _not_implemented()
