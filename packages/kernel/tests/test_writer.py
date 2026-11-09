"""The streaming writer writes only documents the format accepts, with the kinds of the contract."""

import io
import json
from pathlib import Path

import pytest
from wdf_kernel.format import FormatError, check_document
from wdf_kernel.reader import read, same_blocks
from wdf_kernel.writer import KINDS, write
from wdf_runtime.contract import kinds

LEVEL0 = Path(__file__).resolve().parents[3] / "contracts/workload/vectors/level0"


def written(seed, blocks) -> bytes:
    sink = io.BytesIO()
    write(sink, seed, blocks)
    return sink.getvalue()


def test_kinds_equal_the_contract():
    assert KINDS == kinds()


@pytest.mark.parametrize("workload", sorted(p.name for p in LEVEL0.iterdir()))
def test_a_level0_expected_document_round_trips(workload):
    expected = json.loads((LEVEL0 / workload / "expected.json").read_text())
    for block in expected["blocks"]:
        for row in block["rows"]:
            for name, column_type in KINDS[block["kind"]].items():
                if column_type == "hex":
                    row[name] = bytes.fromhex(row[name])
    text = written(expected["seed"], expected["blocks"])
    check_document(io.BytesIO(text), KINDS)
    document = read(io.BytesIO(text))
    assert document["seed"] == expected["seed"]
    assert same_blocks(expected["blocks"], document["blocks"]) is None


def test_every_column_type_and_escape_reads_back():
    text = '"a <b> & c\nd\te\r"'
    blocks = [
        {"id": "e", "kind": "echo", "rows": [{"key": "k", "value": text}]},
        {
            "id": "s",
            "kind": "series",
            "rows": [{"step": 0, "value": -0.0}, {"step": 1, "value": 2}],
        },
        {"id": "p", "kind": "payload", "rows": [{"step": 0, "data": b""}]},
    ]
    document = read(io.BytesIO(written(0, blocks)))
    assert document["blocks"][0]["rows"] == [{"key": "k", "value": text}]
    assert document["blocks"][1]["rows"] == [{"step": 0, "value": -0.0}, {"step": 1, "value": 2.0}]
    assert repr(document["blocks"][1]["rows"][0]["value"]) == "-0.0"
    assert document["blocks"][2]["rows"] == [{"step": 0, "data": b""}]


def test_rows_are_taken_from_a_generator_and_written_in_column_order():
    rows = ({"value": i, "name": f"n{i}"} for i in range(3))
    text = written(1, [{"id": "b", "kind": "scalar", "rows": rows}]).decode()
    assert '<row name="n2" value="2"/>' in text
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n')


@pytest.mark.parametrize(
    "seed,blocks,reason",
    [
        (-1, [], "bad-number-format"),
        (True, [], "bad-number-format"),
        (1, [{"id": "a b", "kind": "scalar", "rows": []}], "schema-violation"),
        (1, [{"id": "a", "kind": "scalar", "rows": []}] * 2, "duplicate-block-id"),
        (1, [{"id": "a", "kind": "histogram", "rows": []}], "unknown-kind"),
        (1, [{"id": "a", "kind": "scalar", "rows": [{"name": "n"}]}], "schema-violation"),
        (
            1,
            [{"id": "a", "kind": "scalar", "rows": [{"name": "n", "value": 1.5}]}],
            "bad-number-format",
        ),
        (
            1,
            [{"id": "a", "kind": "series", "rows": [{"step": 0, "value": float("nan")}]}],
            "bad-number-format",
        ),
        (
            1,
            [{"id": "a", "kind": "digest", "rows": [{"name": "n", "value": "00"}]}],
            "bad-number-format",
        ),
    ],
)
def test_a_value_the_reader_would_refuse_is_not_written(seed, blocks, reason):
    with pytest.raises(FormatError) as refused:
        written(seed, blocks)
    assert refused.value.reason == reason
