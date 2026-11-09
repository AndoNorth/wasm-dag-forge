"""FNV-1a 64 gives the published values, the digest reads parsed values only, and it does not
depend on the interpreter's hash seed."""

import json
import os
import subprocess
import sys

import pytest
from wdf_kernel.digest import digest, fnv1a64
from wdf_kernel.expected import expected_digest

# Published test values of FNV-1a 64.
PUBLISHED = {b"": 0xCBF29CE484222325, b"a": 0xAF63DC4C8601EC8C, b"foobar": 0x85944171F73967E8}
CASES = [
    ("echo", 42, {"b": [True, 1, "x"], "a": "é"}),
    ("cpu_burn", 3, {"units": 2, "heartbeat_every": 1}),
    ("mem_hold", 3, {"mib": 1, "units": 1, "heartbeat_every": 1}),
    ("io_churn", 3, {"rows": 50, "row_bytes": 9, "heartbeat_every": 10}),
]
PROGRAM = """
import json, sys
from wdf_kernel.expected import expected_digest
print(json.dumps([expected_digest(w, s, a) for w, s, a in json.loads(sys.argv[1])]))
"""


@pytest.mark.parametrize("data", PUBLISHED)
def test_fnv1a64_gives_the_published_value(data):
    assert fnv1a64(data) == PUBLISHED[data]


def test_fnv1a64_continues_from_a_value():
    assert fnv1a64(b"bar", fnv1a64(b"foo")) == fnv1a64(b"foobar")


def test_digest_reads_rows_in_column_name_order():
    one = [{"id": "b", "kind": "series", "rows": [{"step": 0, "value": 0.5}]}]
    other = [{"id": "b", "kind": "series", "rows": [{"value": 0.5, "step": 0}]}]
    assert digest(1, one) == digest(1, other)


@pytest.mark.parametrize(
    "changed",
    [
        [{"id": "b", "kind": "scalar", "rows": [{"name": "x", "value": 1}]}],
        [{"id": "b", "kind": "series", "rows": [{"step": 1, "value": 0.5}]}],
        [{"id": "b", "kind": "series", "rows": [{"step": 0, "value": 0.25}]}],
        [{"id": "c", "kind": "series", "rows": [{"step": 0, "value": 0.5}]}],
        [{"id": "b", "kind": "series", "rows": []}],
    ],
)
def test_digest_changes_with_any_part_of_the_result(changed):
    base = [{"id": "b", "kind": "series", "rows": [{"step": 0, "value": 0.5}]}]
    assert digest(1, base) != digest(1, changed)
    assert digest(1, base) != digest(2, base)


def test_digest_tells_the_value_types_apart():
    def one(value):
        return digest(0, [{"id": "b", "kind": "k", "rows": [{"v": value}]}])

    assert len({one(1), one(1.0), one("1"), one(b"1")}) == 4


def test_digest_does_not_collide_by_concatenation():
    def rows(*values):
        return digest(0, [{"id": "b", "kind": "echo", "rows": [{"key": v} for v in values]}])

    assert rows("ab", "c") != rows("a", "bc")


@pytest.mark.parametrize("value", [True, None, [1]])
def test_digest_refuses_a_value_no_column_type_parses_to(value):
    with pytest.raises(TypeError):
        digest(0, [{"id": "b", "kind": "k", "rows": [{"v": value}]}])


def test_digest_stable_across_interpreters():
    """Two interpreters with different hash seeds compute the digests of this one."""
    here = [expected_digest(w, s, a) for w, s, a in CASES]
    seen = []
    for hash_seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        run = subprocess.run(
            [sys.executable, "-c", PROGRAM, json.dumps(CASES)],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        seen.append(json.loads(run.stdout))
    assert seen == [here, here]
