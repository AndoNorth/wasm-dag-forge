"""Whether a data schema change is compatible within a major."""

from typing import Any

# Constraints where a smaller (or larger) new value rejects data the old schema accepted.
UPPER_BOUNDS = ("maxLength", "maximum", "maxItems", "maxProperties")
LOWER_BOUNDS = ("minLength", "minimum", "minItems", "minProperties")


def _resolve(schema: Any, defs: dict[str, Any]) -> Any:
    while isinstance(schema, dict) and "$ref" in schema:
        schema = defs[schema["$ref"].rpartition("/")[2]]
    return schema


def _types(schema: dict[str, Any], defs: dict[str, Any]) -> set[str]:
    members = [_resolve(m, defs) for m in schema.get("anyOf", schema.get("oneOf", []))]
    found = {t for m in members for t in _types(m, defs)}
    declared = schema.get("type", [])
    return found | set([declared] if isinstance(declared, str) else declared)


def _values(schema: dict[str, Any]) -> set[str] | None:
    if "const" in schema:
        return {repr(schema["const"])}
    if "enum" in schema:
        return {repr(v) for v in schema["enum"]}
    return None


def _walk(old: Any, new: Any, path: str, old_defs: dict, new_defs: dict, found: list[str]) -> None:
    old, new = _resolve(old, old_defs), _resolve(new, new_defs)
    if not isinstance(old, dict) or not isinstance(new, dict):
        return
    if old_types := _types(old, old_defs):
        if narrowed := old_types - _types(new, new_defs):
            found.append(f"{path}: type narrowed, {', '.join(sorted(narrowed))} no longer accepted")
    if (old_values := _values(old)) is not None:
        new_values = _values(new)
        if new_values is not None and (gone := old_values - new_values):
            found.append(f"{path}: enum narrowed, {', '.join(sorted(gone))} no longer accepted")
    for key in UPPER_BOUNDS:
        if key in new and (key not in old or new[key] < old[key]):
            found.append(f"{path}: {key} tightened")
    for key in LOWER_BOUNDS:
        if key in new and (key not in old or new[key] > old[key]):
            found.append(f"{path}: {key} tightened")
    if old.get("pattern") != new.get("pattern") and "pattern" in new:
        found.append(f"{path}: pattern changed")
    old_props, new_props = old.get("properties", {}), new.get("properties", {})
    for field in old_props:
        if field not in new_props:
            found.append(f"{path}.{field}: field removed or renamed")
        else:
            _walk(old_props[field], new_props[field], f"{path}.{field}", old_defs, new_defs, found)
    for field in set(new.get("required", [])) - set(old.get("required", [])):
        found.append(f"{path}.{field}: new required field")
    if "items" in old and "items" in new:
        _walk(old["items"], new["items"], f"{path}[]", old_defs, new_defs, found)


def breaking_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """The changes from `old` to `new` that need a new major, as one message each, sorted.

    A minor may add an optional field and loosen a constraint, and nothing else.
    An empty list means that readers of `old` still accept everything written under `new`.
    """
    found: list[str] = []
    _walk(old, new, "data", old.get("$defs", {}), new.get("$defs", {}), found)
    return sorted(found)
