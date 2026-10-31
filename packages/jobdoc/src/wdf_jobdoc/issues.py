"""The finding type shared by every pass of the validator, and the error that carries findings."""

from dataclasses import dataclass

# A report holds at most this many issues, so the answer to a hostile document stays small.
MAX_ISSUES = 100


@dataclass(frozen=True)
class Issue:
    """One finding: a stable code from contracts/job/errors.yaml, a JSON Pointer and a message."""

    code: str
    path: str
    message: str


def ordered(issues) -> tuple[Issue, ...]:
    """Issues sorted by path then code, without repeats, cut at MAX_ISSUES."""
    unique = {(i.path, i.code, i.message): i for i in issues}
    return tuple(unique[k] for k in sorted(unique))[:MAX_ISSUES]


class DocumentInvalid(Exception):
    """The bytes are not a valid job document; `issues` says why."""

    def __init__(self, issues):
        self.issues = ordered(issues)
        first = self.issues[0]
        super().__init__(f"{len(self.issues)} issue(s), first: {first.code} at {first.path or '/'}")


def pointer(tokens) -> str:
    """The JSON Pointer of a path of keys and indexes, with the escapes of RFC 6901."""
    return "".join("/" + str(t).replace("~", "~0").replace("/", "~1") for t in tokens)
