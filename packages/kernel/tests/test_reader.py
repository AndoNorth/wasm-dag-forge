"""The streaming reader: every result vector of the contract, the cap, and the value comparison."""

import io
from pathlib import Path

import pytest
from wdf_kernel.format import FormatError
from wdf_kernel.reader import read, rows, same_blocks
from wdf_kernel.writer import write

VECTORS = Path(__file__).resolve().parents[3] / "contracts/workload/vectors/results"
INVALID = sorted((VECTORS / "invalid").glob("*/*.xml"))

EXPECTED = {
    "every-kind.xml": [
        {"id": "echo", "kind": "echo", "rows": [{"key": "name", "value": '"a <b> & c\nd"'}]},
        {
            "id": "total",
            "kind": "scalar",
            "rows": [{"name": "sum", "value": -12}, {"name": "zero", "value": 0}],
        },
        {
            "id": "digest",
            "kind": "digest",
            "rows": [
                {
                    "name": "sha256",
                    "value": bytes.fromhex(
                        "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"
                    ),
                }
            ],
        },
        {
            "id": "series.1",
            "kind": "series",
            "rows": [
                {"step": 0, "value": 0.1},
                {"step": 1, "value": 1e-05},
                {"step": 2, "value": -0.0},
                {"step": 3, "value": 1.0},
            ],
        },
        {
            "id": "payload",
            "kind": "payload",
            "rows": [{"step": 0, "data": b"\x00\xff\x10"}, {"step": 1, "data": b""}],
        },
    ],
    "no-blocks.xml": [],
    "no-declaration.xml": [{"id": "b", "kind": "scalar", "rows": []}],
    "with-comment.xml": [{"id": "b", "kind": "scalar", "rows": []}],
}


def test_every_valid_vector_has_its_expected_values():
    assert sorted(p.name for p in (VECTORS / "valid").glob("*.xml")) == sorted(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_reader_vectors_valid(name):
    with open(VECTORS / "valid" / name, "rb") as source:
        document = read(source)
    assert document == {"schema": "wdf.sim.v1", "seed": 7, "blocks": EXPECTED[name]}
    assert same_blocks(EXPECTED[name], document["blocks"]) is None


@pytest.mark.parametrize("path", INVALID, ids=[f"{p.parent.name}/{p.name}" for p in INVALID])
def test_reader_vectors_invalid(path):
    with open(path, "rb") as source, pytest.raises(FormatError) as refused:
        list(rows(source))
    assert refused.value.reason == path.parent.name


def test_rows_carry_their_block_id():
    with open(VECTORS / "valid/every-kind.xml", "rb") as source:
        flat = list(rows(source))
    assert [(block, kind) for block, kind, _ in flat] == [
        ("echo", "echo"),
        ("total", "scalar"),
        ("total", "scalar"),
        ("digest", "digest"),
        *[("series.1", "series")] * 4,
        *[("payload", "payload")] * 2,
    ]
    assert flat[2][2] == {"name": "zero", "value": 0}


def test_a_document_above_max_bytes_is_refused_before_it_is_parsed():
    source = io.BytesIO(b"<results " + b" " * 100)
    with pytest.raises(FormatError) as refused:
        list(rows(source, max_bytes=50))
    assert refused.value.reason == "output-over-cap"
    with open(VECTORS / "valid/no-blocks.xml", "rb") as source:
        size = len(source.read())
        assert list(rows(source, max_bytes=size)) == []


def test_a_bad_row_far_into_the_document_refuses_before_any_row_is_yielded():
    good = ({"step": i, "value": 0.5} for i in range(20_000))
    sink = io.BytesIO()
    write(sink, 1, [{"id": "s", "kind": "series", "rows": good}])
    text = sink.getvalue().replace(b'step="19999"', b'step="019999"')
    produced = []
    with pytest.raises(FormatError, match="bad-number-format"):
        for row in rows(io.BytesIO(text)):
            produced.append(row)
    assert produced == []


def test_compares_values_not_bytes():
    one = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<results schema="wdf.sim.v1" seed="7">'
        b'<block id="b" kind="scalar"><row name="&#97;&#10;" value="12"/></block>'
        b'<block id="s" kind="series"><row step="1" value="0.5"/></block>'
        b'<block id="e" kind="echo"></block>'
        b"</results>"
    )
    other = (
        b"<results seed='7'   schema='wdf.sim.v1'>\n"
        b"  <block kind='scalar' id='b'>\n    <row value='12' name='a&#xA;'></row>\n  </block>\n"
        b"  <!-- a comment -->\n"
        b'  <block kind="series" id="s"><row value="0.5" step="1" /></block>\n'
        b'  <block kind="echo" id="e"/>\n'
        b"</results>\n"
    )
    assert one != other
    first, second = read(io.BytesIO(one)), read(io.BytesIO(other))
    assert first == second
    assert same_blocks(first["blocks"], second["blocks"]) is None


@pytest.mark.parametrize(
    "change,difference",
    [
        (lambda b: b[1]["rows"][1].update(value=1), "block total row 1"),
        (lambda b: b[3]["rows"][2].update(value=0.0), "block series.1 row 2"),
        (lambda b: b.pop(), "4 blocks, expected 5"),
        (lambda b: b[4]["rows"].pop(), "block payload has 1 rows, expected 2"),
        (lambda b: b[1].update(id="other"), "block other of kind scalar, expected block total"),
    ],
)
def test_a_changed_value_is_a_difference(change, difference):
    with open(VECTORS / "valid/every-kind.xml", "rb") as source:
        actual = read(source)["blocks"]
    actual = [dict(block, rows=[dict(row) for row in block["rows"]]) for block in actual]
    change(actual)
    assert same_blocks(EXPECTED["every-kind.xml"], actual).startswith(difference)


def test_hex_given_as_text_or_bytes_compares_equal():
    as_text = [{"id": "d", "kind": "digest", "rows": [{"name": "n", "value": "00ff"}]}]
    as_bytes = [{"id": "d", "kind": "digest", "rows": [{"name": "n", "value": b"\x00\xff"}]}]
    assert same_blocks(as_text, as_bytes) is None


@pytest.mark.slow
def test_million_rows_round_trip(tmp_path):
    count = 1_000_000
    path = tmp_path / "results.xml"

    def series():
        return ({"step": i, "value": i / 7} for i in range(count))

    with open(path, "wb") as sink:
        write(sink, 3, [{"id": "s", "kind": "series", "rows": series()}])
    with open(path, "rb") as source:
        read_back = rows(source)
        for (block, kind, values), want in zip(read_back, series(), strict=True):
            assert (block, kind, values) == ("s", "series", want)
    print(f"rows: {count}, bytes: {path.stat().st_size}")
