import hashlib
import json

from wdf_plan import canonical_bytes, plan_hash


def outside_strings(data: bytes) -> str:
    """The canonical bytes with every string literal removed."""
    out, in_string, escaped = [], False, False
    for ch in data.decode("ascii"):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        else:
            out.append(ch)
    return "".join(out)


def test_the_bytes_are_compact_sorted_ascii_json(good, plan_of):
    data = canonical_bytes(plan_of(good))
    assert data.isascii()
    assert data == json.dumps(json.loads(data), sort_keys=True, separators=(",", ":")).encode()


def test_the_hash_is_the_sha256_of_the_bytes(good, plan_of):
    plan = plan_of(good)
    assert plan_hash(plan) == hashlib.sha256(canonical_bytes(plan)).hexdigest()


def test_non_ascii_text_is_escaped(good, plan_of):
    good["tasks"]["sim"]["params"]["workload"] = "café"
    assert b"caf\\u00e9" in canonical_bytes(plan_of(good))


def test_a_change_of_any_value_changes_the_hash(good, plan_of):
    before = plan_hash(plan_of(good))
    good["tasks"]["sim"]["retries"] = 2
    assert plan_hash(plan_of(good)) != before


def test_the_bytes_read_back_as_the_same_plan(good, plan_of):
    plan = plan_of(good)
    assert type(plan).model_validate_json(canonical_bytes(plan)) == plan


def test_plan_has_no_floats(valid_documents, plan_of):
    for name, data in valid_documents.items():
        assert "." not in outside_strings(canonical_bytes(plan_of(data))), name


def test_the_float_scan_sees_a_decimal_point_outside_a_string():
    assert "." in outside_strings(b'{"cpu":0.25}')
    assert "." not in outside_strings(b'{"name":"a.b \\" 1.5"}')
