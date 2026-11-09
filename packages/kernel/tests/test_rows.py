"""Each workload's blocks follow its formula, are a pure function of (seed, args), and are made
lazily."""

import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st
from wdf_kernel import rows
from wdf_kernel.expected import expected_blocks, expected_digest
from wdf_kernel.rng import GAMMA, MASK, SplitMix64

SEEDS = st.integers(min_value=0, max_value=2**53 - 1)
SMALL = st.integers(min_value=0, max_value=20)
ARG = st.one_of(st.booleans(), st.integers(), st.text(), st.lists(st.integers() | st.text()))
WORKLOADS = st.one_of(
    st.tuples(
        st.just("echo"), st.dictionaries(st.from_regex(r"[a-z][a-z0-9_]{0,8}", fullmatch=True), ARG)
    ),
    st.tuples(st.just("sum_squares"), st.fixed_dictionaries({"n": st.integers(0, 10**6)})),
    st.tuples(st.just("hash_chain"), st.fixed_dictionaries({"rounds": SMALL})),
    st.tuples(st.just("noop"), st.fixed_dictionaries({"heartbeat_every": SMALL})),
    st.tuples(st.just("cpu_burn"), st.fixed_dictionaries({"units": st.integers(0, 3)})),
    st.tuples(
        st.just("mem_hold"), st.fixed_dictionaries({"mib": SMALL, "units": st.integers(0, 3)})
    ),
    st.tuples(st.just("io_churn"), st.fixed_dictionaries({"rows": SMALL, "row_bytes": SMALL})),
)


class TestProperty:
    @given(SEEDS, WORKLOADS)
    def test_same_inputs_same_blocks(self, seed, case):
        workload, args = case
        assert expected_blocks(workload, seed, args) == expected_blocks(workload, seed, dict(args))
        assert expected_digest(workload, seed, args) == expected_digest(workload, seed, args)

    @given(SEEDS, st.integers(1, 5))
    def test_another_seed_other_rows(self, seed, units):
        args = {"units": units}
        assert expected_blocks("cpu_burn", seed, args) != expected_blocks(
            "cpu_burn", seed + 1, args
        )


def test_echo_writes_each_argument_in_key_order_as_canonical_json():
    [block] = expected_blocks("echo", 0, {"b": ["x", 1], "a": "é"})
    assert block["rows"] == [{"key": "a", "value": '"é"'}, {"key": "b", "value": '["x",1]'}]


def test_sum_squares_is_the_closed_form_of_the_loop():
    for n in (0, 1, 2, 37):
        [block] = expected_blocks("sum_squares", 0, {"n": n})
        assert block["rows"] == [{"name": "sum_squares", "value": sum(i * i for i in range(n + 1))}]


def test_hash_chain_hashes_the_seed_as_eight_bytes_big_endian():
    [block] = expected_blocks("hash_chain", 258, {"rounds": 2})
    once = hashlib.sha256(b"\x00\x00\x00\x00\x00\x00\x01\x02").digest()
    assert block["rows"] == [{"name": "hash_chain", "value": hashlib.sha256(once).digest()}]


def test_cpu_burn_writes_one_row_per_unit():
    [block] = expected_blocks("cpu_burn", 1, {"units": 4, "heartbeat_every": 2})
    assert (block["id"], block["kind"]) == ("series", "series")
    assert [row["step"] for row in block["rows"]] == [0, 1, 2, 3]
    assert all(0.0 <= row["value"] < 1.0 for row in block["rows"])


def test_a_work_unit_folds_unit_rounds_of_the_generator():
    rng = SplitMix64(1)
    assert rows.work_unit(rng, 0) != 0
    assert rng.state == (1 + rows.UNIT_ROUNDS * GAMMA) & MASK


def test_mem_hold_writes_the_bytes_it_holds_and_the_series_of_cpu_burn():
    held, series = expected_blocks("mem_hold", 1, {"mib": 3, "units": 2})
    assert held["rows"] == [{"name": "held_bytes", "value": 3 * 2**20}]
    assert series == expected_blocks("cpu_burn", 1, {"units": 2})[0]


def test_io_churn_writes_rows_of_row_bytes():
    [block] = expected_blocks("io_churn", 1, {"rows": 3, "row_bytes": 5})
    assert [len(row["data"]) for row in block["rows"]] == [5, 5, 5]
    assert len({row["data"] for row in block["rows"]}) == 3


def test_noop_writes_no_block():
    assert expected_blocks("noop", 1, {"heartbeat_every": 1}) == []


def test_rows_are_made_lazily():
    [block] = rows.blocks("io_churn", 1, {"rows": 10**12, "row_bytes": 8})
    assert next(block["rows"])["step"] == 0


@pytest.mark.parametrize(
    "workload,seed,args",
    [
        ("fault", 1, {"mode": "trap"}),
        ("cpu_burn", -1, {"units": 1}),
        ("cpu_burn", True, {"units": 1}),
        ("cpu_burn", 1, {}),
        ("cpu_burn", 1, {"units": True}),
        ("io_churn", 1, {"rows": -1, "row_bytes": 8}),
        ("sum_squares", 1, {"n": "3"}),
    ],
)
def test_unknown_workload_or_bad_argument_is_refused(workload, seed, args):
    with pytest.raises(ValueError):
        expected_blocks(workload, seed, args)
