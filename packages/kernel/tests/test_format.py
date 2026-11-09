"""The value formats and the document rules of wdf.sim.v1."""

import io
import math
from xml.parsers import expat

import pytest
from hypothesis import given
from hypothesis import strategies as st
from wdf_kernel.format import FormatError, check_document, escape, format_value, parse_value

KINDS = {"scalar": {"name": "str", "value": "int"}, "series": {"step": "int", "value": "float"}}


def document(body: str) -> io.BytesIO:
    return io.BytesIO(f'<results schema="wdf.sim.v1" seed="1">{body}</results>'.encode())


@pytest.mark.parametrize(
    "column_type,value,text",
    [
        ("int", 0, "0"),
        ("int", -12, "-12"),
        ("float", 0.1, "0.1"),
        ("float", 1e-05, "1e-05"),
        ("float", 2, "2.0"),
        ("hex", b"\x00\xff", "00ff"),
        ("hex", b"", ""),
        ("str", 'a "b"', 'a "b"'),
    ],
)
def test_a_value_round_trips_through_its_text(column_type, value, text):
    assert format_value(column_type, value) == text
    assert parse_value(column_type, text) == value


@pytest.mark.parametrize(
    "column_type,text",
    [
        ("int", "01"),
        ("int", "-0"),
        ("int", "+1"),
        ("int", "1.0"),
        ("int", ""),
        ("float", "0.10"),
        ("float", "1E-05"),
        ("float", "inf"),
        ("float", "nan"),
        ("float", "1"),
        ("hex", "ABCD"),
        ("hex", "abc"),
    ],
)
def test_a_spelling_format_value_never_writes_is_refused(column_type, text):
    with pytest.raises(FormatError) as refused:
        parse_value(column_type, text)
    assert refused.value.reason == "bad-number-format"


@pytest.mark.parametrize(
    "column_type,value",
    [("int", True), ("int", 1.0), ("float", math.inf), ("hex", "00"), ("str", 1)],
)
def test_a_value_of_the_wrong_type_is_refused(column_type, value):
    with pytest.raises(FormatError, match="bad-number-format"):
        format_value(column_type, value)


def test_an_unknown_column_type_is_refused():
    with pytest.raises(FormatError, match="unknown-column-type"):
        parse_value("bool", "true")


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_every_finite_float_round_trips(value):
    assert parse_value("float", format_value("float", value)) == value


@given(st.text(st.characters(blacklist_categories=("Cs", "Cc")) | st.sampled_from("\t\n\r")))
def test_an_escaped_string_parses_back_to_itself(text):
    seen = {}
    parser = expat.ParserCreate()
    parser.StartElementHandler = lambda name, attrs: seen.update(attrs)
    parser.Parse(f'<row value="{escape(text)}"/>'.encode(), True)
    assert seen["value"] == text


def test_a_document_larger_than_one_read_is_checked_across_reads():
    rows = "".join(f'<row step="{i}" value="{i / 7!r}"/>' for i in range(20_000))
    source = document(f'<block id="s" kind="series">{rows}</block>')
    assert len(source.getvalue()) > 4 * 65536
    check_document(source, KINDS)


def test_a_bad_value_far_into_the_document_is_found():
    rows = "".join(f'<row step="{i}" value="0.5"/>' for i in range(20_000))
    source = document(f'<block id="s" kind="series">{rows}<row step="x" value="0.5"/></block>')
    with pytest.raises(FormatError, match="bad-number-format"):
        check_document(source, KINDS)


def test_a_doctype_is_refused_before_its_entities_expand():
    entities = ['<!ENTITY e0 "aaaaaaaaaa">'] + [
        f'<!ENTITY e{i} "{f"&e{i - 1};" * 10}">' for i in range(1, 10)
    ]
    text = f'<!DOCTYPE results [{"".join(entities)}]><results schema="wdf.sim.v1" seed="1">&e9;'
    with pytest.raises(FormatError) as refused:
        check_document(io.BytesIO(text.encode() + b"</results>"), KINDS)
    assert refused.value.reason == "doctype-present"


@pytest.mark.parametrize(
    "body,reason",
    [
        ('<row name="n" value="1"/>', "row-outside-block"),
        ('<block id="a" kind="scalar"/><block id="a" kind="scalar"/>', "duplicate-block-id"),
        ('<block id="a" kind="histogram"/>', "unknown-kind"),
        ('<block id="a" kind="scalar"><row name="n"/></block>', "schema-violation"),
        ('<block id="a" kind="scalar"><row name="n" value="1"/>', "schema-violation"),
    ],
)
def test_a_broken_document_names_its_reason(body, reason):
    with pytest.raises(FormatError) as refused:
        check_document(document(body), KINDS)
    assert refused.value.reason == reason
