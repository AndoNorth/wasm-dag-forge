"""SplitMix64 gives the reference stream, and one seed always gives one stream."""

import json
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st
from wdf_kernel.rng import MASK, SplitMix64

VECTORS = json.loads((Path(__file__).parent / "vectors" / "splitmix64.json").read_text())
# The first values of seed 0 published with the reference implementation of SplitMix64.
REFERENCE = [0xE220A8397B1DCDAF, 0x6E789E6AA1B965F4, 0x06C45D188009454F]


def test_seed_zero_gives_the_reference_stream():
    rng = SplitMix64(0)
    assert [rng.next() for _ in REFERENCE] == REFERENCE


def test_golden_vectors_of_splitmix64():
    for vector in VECTORS:
        rng = SplitMix64(vector["seed"])
        assert [f"{rng.next():016x}" for _ in vector["values"]] == vector["values"], vector["seed"]


def test_bytes_are_the_next_values_big_endian_cut_to_size():
    a, b = SplitMix64(5), SplitMix64(5)
    assert a.bytes(12) == (b.next().to_bytes(8, "big") + b.next().to_bytes(8, "big"))[:12]
    assert a.bytes(0) == b""


def test_float_is_the_top_53_bits():
    a, b = SplitMix64(9), SplitMix64(9)
    assert a.float() == (b.next() >> 11) / 2**53


class TestProperty:
    @given(st.integers(min_value=0, max_value=MASK), st.integers(min_value=0, max_value=64))
    def test_same_seed_same_stream(self, seed, size):
        a, b = SplitMix64(seed), SplitMix64(seed)
        assert a.bytes(size) == b.bytes(size)
        assert 0.0 <= a.float() < 1.0
