"""The patch compatibility check of a paused job."""

from dataclasses import dataclass

__all__ = ["PatchReport", "patch_check"]


@dataclass(frozen=True)
class PatchReport:
    compatible: bool
    issues: tuple
    changed: tuple[str, ...]
    recompile: tuple[str, ...]


def patch_check(old, new, completed):
    raise NotImplementedError("not implemented: owned by lane H")
