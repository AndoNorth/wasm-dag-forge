"""Finds what breaks the additive-only rule between two versions of a JSON schema.

The walk follows `properties`, `items`, `additionalProperties`, `patternProperties`, `$ref`,
`oneOf` and `anyOf`, so a model held in a map of tasks or a union of variants is compared too.
"""

from typing import Any

Defs = tuple[dict[str, Any], dict[str, Any]]


def _deref(node: Any, defs: dict[str, Any]) -> tuple[Any, str | None]:
    name = None
    while isinstance(node, dict) and "$ref" in node:
        name = node["$ref"].rpartition("/")[2]
        node = defs[name]
    return node, name


def _members(schema: dict[str, Any], key: str) -> dict[str, Any]:
    """The variants of `key` by their reference, and by their place among the inline ones."""
    found, inline = {}, 0
    for member in schema.get(key, []):
        if "$ref" in member:
            found[member["$ref"]] = member
        else:
            found[f"inline {inline}"] = member
            inline += 1
    return found


def _walk(old: Any, new: Any, path: str, defs: Defs, active: frozenset, found: list[str]) -> None:
    old, old_name = _deref(old, defs[0])
    new, new_name = _deref(new, defs[1])
    if not isinstance(old, dict) or not isinstance(new, dict):
        return
    if old_name or new_name:
        # A model that refers to itself is compared once per pair, so the walk ends.
        if (old_name, new_name) in active:
            return
        active |= {(old_name, new_name)}
        path = f"{path}{{{new_name or old_name}}}"

    for field in sorted(set(new.get("required", [])) - set(old.get("required", []))):
        found.append(f"{path}.{field}: new required field")
    old_props, new_props = old.get("properties", {}), new.get("properties", {})
    for field in sorted(old_props):
        if field in new_props:
            _walk(old_props[field], new_props[field], f"{path}.{field}", defs, active, found)
        else:
            found.append(f"{path}.{field}: field removed or renamed")
    if "items" in old and "items" in new:
        _walk(old["items"], new["items"], f"{path}[]", defs, active, found)
    if isinstance(old.get("additionalProperties"), dict) and isinstance(
        new.get("additionalProperties"), dict
    ):
        _walk(
            old["additionalProperties"],
            new["additionalProperties"],
            f"{path}[*]",
            defs,
            active,
            found,
        )
    old_patterns, new_patterns = old.get("patternProperties", {}), new.get("patternProperties", {})
    for pattern in sorted(old_patterns.keys() & new_patterns.keys()):
        _walk(old_patterns[pattern], new_patterns[pattern], f"{path}[*]", defs, active, found)
    for key in ("oneOf", "anyOf"):
        olds, news = _members(old, key), _members(new, key)
        for member in sorted(olds):
            if member in news:
                _walk(olds[member], news[member], path, defs, active, found)
            else:
                found.append(f"{path}: variant {member} removed")


def violations(old: dict[str, Any], new: dict[str, Any], root: str = "schema") -> list[str]:
    """What changed from `old` to `new` that a reader of `old` cannot take, one message each.

    A new required field or a removed field needs a new version, and an optional field does not.
    The message holds the path to the field, so a failure names it.
    """
    found: list[str] = []
    _walk(old, new, root, (old.get("$defs", {}), new.get("$defs", {})), frozenset(), found)
    return sorted(found)
