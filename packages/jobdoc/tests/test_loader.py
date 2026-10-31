"""The strict loader: what it refuses before the models run, and that none ends in a traceback."""

import json

import pytest
from wdf_jobdoc import DocumentInvalid, parse_document

OK = b'{"schema": "urn:wdf:schema:job:1.0"}'


def test_a_valid_document_loads(load, good):
    assert load(good).tasks["sim"].params.workload == "pi"


def test_duplicate_keys_are_refused_with_the_path_of_the_repeated_key(issues, good):
    text = json.dumps(good).replace('"agg": {', '"build": {}, "agg": {', 1)
    assert issues(text.encode()) == [("duplicate-key", "/tasks/build")]


def test_every_repeat_is_reported(issues):
    assert issues(b'{"a": 1, "a": 2, "b": {"c": 1, "c": 2}}') == [
        ("duplicate-key", "/a"),
        ("duplicate-key", "/b/c"),
    ]


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_numbers_are_refused_where_they_stand(issues, good, literal):
    text = json.dumps(good).replace('"n": 1000', f'"n": {literal}')
    assert issues(text.encode()) == [("non-finite-number", "/tasks/sim/params/args/n")]


def test_a_float_outside_cpu_is_refused(issues, good):
    good["tasks"]["sim"]["memory_mib"] = 384.5
    good["tasks"]["sim"]["params"]["args"]["rate"] = 0.5
    assert issues(good) == [
        ("float-not-allowed", "/tasks/sim/memory_mib"),
        ("float-not-allowed", "/tasks/sim/params/args/rate"),
    ]


def test_an_exponent_is_a_float(issues, good):
    good["tasks"]["sim"]["memory_mib"] = 1
    text = json.dumps(good).replace('"memory_mib": 1,', '"memory_mib": 1e3,')
    assert issues(text.encode()) == [("float-not-allowed", "/tasks/sim/memory_mib")]


@pytest.mark.parametrize(
    ("text", "cores"),
    [("0.25", 250), ("1", 1000), ("1.000", 1000), ("0.1", 100), ("2.5e-1", 250), ("0.001", 1)],
)
def test_cpu_is_an_exact_decimal_in_millicores(load, good, text, cores):
    data = json.dumps(good).replace('"cpu": 0.25', f'"cpu": {text}')
    assert load(data.encode()).tasks["sim"].cpu_milli == cores


@pytest.mark.parametrize("text", ["0.2501", "0.0001", "0", "-1", "1e400", "1000001"])
def test_cpu_outside_three_decimals_or_the_bound_is_refused(issues, good, text):
    data = json.dumps(good).replace('"cpu": 0.25', f'"cpu": {text}')
    assert issues(data.encode()) == [("bad-value", "/tasks/sim/cpu")]


def test_cpu_has_no_float_rounding(load, good):
    # 0.29 as an IEEE double is 0.28999999999999998, which would give 289 millicores.
    data = json.dumps(good).replace('"cpu": 0.25', '"cpu": 0.29')
    assert load(data.encode()).tasks["sim"].cpu_milli == 290


def test_the_size_limit_is_checked_before_parsing(issues):
    junk = b"{" * 100
    assert issues(junk, max_bytes=99) == [("document-too-large", "")]
    assert issues(junk, max_bytes=100) == [("not-json", "")]


def test_a_document_of_exactly_the_limit_loads(load, good):
    data = json.dumps(good).encode()
    assert load(data, max_bytes=len(data))
    assert [i.code for i in _refused(data, len(data) - 1)] == ["document-too-large"]


def _refused(data, max_bytes):
    with pytest.raises(DocumentInvalid) as info:
        parse_document(data, max_bytes)
    return info.value.issues


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"{",
        b'{"a": }',
        b'{"a": 1,}',
        b"\xff\xfe",
        b"\xef\xbb\xbf" + OK,
        OK.decode().encode("utf-16"),
        b"[" * 100_000,
        b'{"a":' * 100_000,
        b"9" * 10_000,
        b'{"a": ' + b"9" * 10_000 + b"}",
        b"'single'",
        b"{'a': 1}",
        b"1 2",
    ],
)
def test_bytes_that_are_not_json_never_raise_anything_but_document_invalid(data):
    with pytest.raises(DocumentInvalid) as info:
        parse_document(data, 1024 * 1024)
    assert [i.code for i in info.value.issues] == ["not-json"]


@pytest.mark.parametrize("data", [b"[]", b"1", b'"x"', b"null", b"true"])
def test_a_value_that_is_not_an_object_is_a_type_error(issues, data):
    assert issues(data) == [("bad-type", "")]


def test_a_string_not_in_nfc_is_refused_with_its_path(issues, good):
    good["tasks"]["sim"]["params"]["workload"] = "café"
    assert issues(good) == [("not-nfc", "/tasks/sim/params/workload")]


def test_a_key_not_in_nfc_is_refused(issues, good):
    good["tasks"]["sim"]["params"]["args"]["é"] = 1
    assert issues(good) == [("not-nfc", "/tasks/sim/params/args/é")]


def test_nfc_text_is_accepted(load, good):
    good["tasks"]["sim"]["params"]["workload"] = "café"
    assert load(good).tasks["sim"].params.workload == "café"


def test_a_lone_surrogate_is_refused(issues, good):
    text = json.dumps(good).replace('"pi"', '"\\ud800"', 1)
    assert issues(text.encode()) == [("lone-surrogate", "/tasks/sim/params/workload")]


def test_a_surrogate_pair_is_a_character(load, good):
    good["tasks"]["sim"]["params"]["workload"] = "\U0001f600"
    assert load(json.dumps(good, ensure_ascii=True).encode())


def test_pointers_escape_tilde_and_slash(issues, good):
    good["a/b"] = 1
    good["c~d"] = 1
    assert issues(good) == [("unknown-field", "/a~1b"), ("unknown-field", "/c~0d")]


def test_the_same_bytes_give_the_same_issues(good):
    good["x"] = 1
    good["tasks"]["sim"]["retries"] = "1"
    data = json.dumps(good).encode()
    first = _refused(data, 1 << 20)
    assert _refused(data, 1 << 20) == first
    assert list(first) == sorted(first, key=lambda i: (i.path, i.code))


def test_issues_are_capped(good):
    good.update({f"field{n:03}": 1 for n in range(300)})
    assert len(_refused(json.dumps(good).encode(), 1 << 20)) == 100


def test_a_loader_issue_stops_the_models(issues, good):
    good["x"] = 1
    good["tasks"]["sim"]["memory_mib"] = 1.5
    assert issues(good) == [("float-not-allowed", "/tasks/sim/memory_mib")]
