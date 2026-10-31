"""Poison vectors: each file makes read_event raise the error its sidecar names."""

import json

import pytest
import wdf_events
from wdf_compat import read_event
from wdf_compat.vectors import POISON, poison_files

CASES = sorted(p for p in POISON.glob("*.json") if not p.name.endswith(".expect.json"))
WANTED = {
    "wrong-major", "schema-invalid-data", "missing-time", "id-with-hash", "not-json",
    "truncated-json", "datacontenttype-other", "over-64kib",
}  # fmt: skip


def test_the_poison_set_is_the_documented_failures():
    assert {p.stem for p in CASES} == WANTED


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_a_poison_file_raises_the_error_its_sidecar_names(path):
    expect = json.loads(path.with_suffix(".expect.json").read_text())
    with pytest.raises(getattr(wdf_events, expect["error"])) as raised:
        read_event(path.read_bytes())
    assert type(raised.value).__name__ == expect["error"]
    assert raised.value.reason == expect["reason"]


def test_the_committed_files_are_what_the_builders_write():
    for name, (data, _) in poison_files().items():
        assert (POISON / f"{name}.json").read_bytes() == data
