"""A hostile or broken result is refused as aggregate_parse_failed naming its task, before any
summary is written, whichever aggregation reads it."""

import json
from pathlib import Path

import pytest
import wdf_aggregate.__main__ as cli
from wdf_aggregate.read import max_bytes
from wdf_kernel.rows import blocks
from wdf_kernel.writer import write

ROOT = Path(__file__).resolve().parents[3]
VECTORS = ROOT / "contracts/workload/vectors/results/invalid"
HEAD = b'<?xml version="1.0" encoding="UTF-8"?>\n'

# Ten entities of ten references each: about 30 GB of text if the parser expanded it.
LAUGHS = HEAD + (
    b"<!DOCTYPE results [\n"
    b'  <!ENTITY l0 "lol">\n'
    + b"".join(f'  <!ENTITY l{i} "{f"&l{i - 1};" * 10}">\n'.encode() for i in range(1, 11))
    + b"]>\n"
    b'<results schema="wdf.sim.v1" seed="7">\n'
    b'  <block id="b" kind="echo">\n'
    b'    <row key="k" value="&l10;"/>\n'
    b"  </block>\n"
    b"</results>\n"
)


def entity_expansion(path: Path) -> None:
    path.write_bytes(LAUGHS)


def doctype(path: Path) -> None:
    path.write_bytes((VECTORS / "doctype-present/plain.xml").read_bytes())


def malformed_block(path: Path) -> None:
    path.write_bytes((VECTORS / "schema-violation/malformed-block.xml").read_bytes())


def oversize(path: Path) -> None:
    """A well formed start padded one byte past the cap, so only the size check refuses it."""
    with path.open("wb") as sink:
        write(sink, 3, blocks("noop", 3, {}))
    with path.open("r+b") as sink:
        sink.truncate(max_bytes() + 1)


@pytest.mark.parametrize(
    "make,reason",
    [
        (entity_expansion, "doctype-present"),
        (doctype, "doctype-present"),
        (malformed_block, "schema-violation"),
        (oversize, "output-over-cap"),
    ],
    ids=["entity_expansion", "doctype", "malformed_block", "oversize"],
)
@pytest.mark.parametrize("aggregation", ["summary_stats", "digest_list"])
def test_hostile_result_is_a_parse_failure(tmp_path, capsys, make, reason, aggregation):
    good, bad = tmp_path / "t1.xml", tmp_path / "t2.xml"
    with good.open("wb") as sink:
        write(sink, 3, blocks("noop", 3, {}))
    make(bad)
    out = tmp_path / "out"
    argv = ["files", "--aggregation", aggregation, "--args", json.dumps({}), "--out", str(out)]

    code = cli.main([*argv, f"t1={good}", f"t2={bad}"])

    last = capsys.readouterr().err.splitlines()[-1]
    assert code == cli.EXIT_CODES["aggregate_parse_failed"]
    assert last.startswith(f"reason: aggregate_parse_failed: t2: {reason}: "), last
    assert not (out / "summary.json").exists()
