"""The strict loader: bytes in, a Document or a DocumentInvalid out, and never a traceback.

Everything JSON allows and a job document must not hold is refused before the models see it:
duplicate keys, NaN and infinities, floats outside `cpu`, strings that are not in NFC or that hold
a lone surrogate, and bytes beyond the size the caller allows.
"""

import json
import unicodedata
from decimal import Decimal

from pydantic import ValidationError

from .issues import DocumentInvalid, Issue, pointer
from .models import TASK_ID_PATTERN, Document

_TASK_TYPES = ("compile", "simulate", "aggregate")
_PLACEHOLDER_MESSAGES = {
    "placeholder-in-compile": "a compile task holds no placeholder, so a batch shares one build",
    "placeholder-misplaced": "a placeholder is only the whole value of an arg or a simulate seed",
}


class _NonFinite:
    """What `NaN` and the infinities parse to, so that the walk can report where they stand."""

    def __init__(self, literal: str):
        self.literal = literal


def parse_document(data: bytes, max_bytes: int) -> Document:
    """The document in `data`, or DocumentInvalid carrying every issue found.

    `max_bytes` is checked before anything is parsed.
    """
    if len(data) > max_bytes:
        raise DocumentInvalid(
            [
                Issue(
                    "document-too-large", "", f"{len(data)} bytes, at most {max_bytes} are accepted"
                )
            ]
        )
    try:
        raw, duplicates = _parse(data)
    except (ValueError, RecursionError) as exc:
        # JSONDecodeError and the digit limit of int() are both ValueErrors.
        raise DocumentInvalid([Issue("not-json", "", _first_line(exc))]) from None
    issues = _walk(raw, duplicates)
    if issues:
        raise DocumentInvalid(issues)
    try:
        return Document.model_validate(raw)
    except ValidationError as exc:
        raise DocumentInvalid(_map_errors(exc, raw)) from None


def _first_line(exc: Exception) -> str:
    return (str(exc).splitlines() or [type(exc).__name__])[0]


def _parse(data: bytes):
    duplicates: dict[int, list[str]] = {}

    def pairs(items):
        obj = dict(items)
        if len(obj) != len(items):
            seen = set()
            for key, _ in items:
                if key in seen:
                    duplicates.setdefault(id(obj), []).append(key)
                seen.add(key)
        return obj

    # Decoding here and not in json.loads keeps a byte order mark and UTF-16 out.
    text = data.decode("utf-8")
    raw = json.loads(text, object_pairs_hook=pairs, parse_float=Decimal, parse_constant=_NonFinite)
    return raw, duplicates


def _walk(raw, duplicates) -> list[Issue]:
    """Issues in the content of every string, key and number, found without recursion."""
    issues = []
    stack = [(raw, ())]
    while stack:
        node, path = stack.pop()
        if isinstance(node, dict):
            for key in duplicates.get(id(node), ()):
                issues.append(
                    Issue("duplicate-key", pointer((*path, key)), f"the key {key!r} appears twice")
                )
            for key, child in node.items():
                issues += _string_issues(key, (*path, key), "key")
                stack.append((child, (*path, key)))
        elif isinstance(node, list):
            stack.extend((child, (*path, index)) for index, child in enumerate(node))
        elif isinstance(node, str):
            issues += _string_issues(node, path, "string")
        elif isinstance(node, _NonFinite):
            issues.append(
                Issue("non-finite-number", pointer(path), f"{node.literal} is not a number")
            )
        elif isinstance(node, Decimal) and not _is_cpu(path):
            issues.append(
                Issue("float-not-allowed", pointer(path), "only cpu may hold a decimal number")
            )
    return issues


def _is_cpu(path) -> bool:
    return len(path) == 3 and path[0] == "tasks" and path[2] == "cpu"


def _string_issues(text: str, path, what: str) -> list[Issue]:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return [Issue("lone-surrogate", pointer(path), f"a {what} holds a lone surrogate")]
    if not unicodedata.is_normalized("NFC", text):
        return [
            Issue("not-nfc", pointer(path), f"a {what} is not in Unicode normalization form NFC")
        ]
    return []


def _map_errors(exc: ValidationError, raw) -> list[Issue]:
    issues = []
    for error in exc.errors(include_url=False):
        loc = _clean_loc(error["loc"])
        kind = error["type"]
        if kind in _OWN_CODES:
            issues.append(Issue(kind, pointer(loc), error["msg"]))
        elif loc and loc[0] == "parameters":
            issues.append(Issue("bad-parameter-declaration", pointer(loc), error["msg"]))
        elif (site := _placeholder_site(loc, raw)) is not None:
            code = "placeholder-in-compile" if _in_compile(site, raw) else "placeholder-misplaced"
            issues.append(Issue(code, pointer(site), _PLACEHOLDER_MESSAGES[code]))
        else:
            if kind in ("union_tag_not_found", "union_tag_invalid"):
                loc = (*loc, "type")
            issues.append(Issue(_code(error, loc), pointer(loc), error["msg"]))
    return issues


def _placeholder_site(loc, raw):
    """The outermost value on the path to a fault that has the look of a placeholder, if any.

    The fault is then that a placeholder stands where none may, whatever the models made of it.
    A task is never itself a placeholder, so the search starts inside it.
    """
    node = raw
    for depth, key in enumerate(loc, start=1):
        try:
            node = node[key]
        except (KeyError, IndexError, TypeError):
            return None
        if isinstance(node, dict) and "param" in node and depth >= (3 if loc[0] == "tasks" else 1):
            return loc[:depth]
    return None


def _clean_loc(loc) -> tuple:
    """The location without the tag a tagged union adds after the task id, and without `[key]`."""
    parts = [p for p in loc if p != "[key]"]
    if len(parts) >= 3 and parts[0] == "tasks" and parts[2] in _TASK_TYPES:
        del parts[2]
    return tuple(parts)


def _code(error, loc) -> str:
    kind = error["type"]
    if kind in ("missing", "union_tag_not_found"):
        return "missing-field"
    if kind == "extra_forbidden":
        return "unknown-field"
    if kind == "union_tag_invalid":
        return "unknown-task-type"
    if kind == "literal_error" and loc == ("schema",):
        return "unsupported-schema-version"
    if kind == "string_pattern_mismatch" and error["ctx"]["pattern"] == TASK_ID_PATTERN:
        return "bad-task-id"
    if kind.endswith("_type") or kind.endswith("_parsing"):
        return "bad-type"
    return "bad-value"


def _in_compile(loc, raw) -> bool:
    try:
        return raw["tasks"][loc[1]]["type"] == "compile"
    except (KeyError, IndexError, TypeError):
        return False


# Codes that the models raise themselves as custom errors.
_OWN_CODES = frozenset(
    {
        "bad-parameter-declaration",
        "bad-type",
        "bad-value",
        "placeholder-malformed",
        "placeholder-misplaced",
    }
)
