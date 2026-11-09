"""The per-file reduce and its combination, on parsed rows and without any file."""

import math
import statistics

import pytest
from hypothesis import given
from hypothesis import strategies as st
from wdf_aggregate.reduce import (
    UsageError,
    check_args,
    combine,
    reduce_stats,
    stats_summary,
    write_summary,
)
from wdf_runtime.contract import check

FLOATS = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False)


def series(values) -> list:
    return [("s", {"step": step, "value": value}) for step, value in enumerate(values)]


@given(st.lists(st.lists(FLOATS, min_size=1, max_size=30), min_size=1, max_size=5))
def test_combined_partials_match_one_pass_over_every_row(files):
    total = combine(reduce_stats(series(values), {}) for values in files)
    every = [v for values in files for v in values]
    stats = total.stats[("s", "value")].summary()
    assert stats["count"] == len(every) == total.rows
    assert stats["min"] == min(every) and stats["max"] == max(every)
    assert math.isclose(stats["mean"], statistics.fmean(every), rel_tol=1e-9, abs_tol=1e-9)
    if len(every) == 1:
        assert stats["std"] is None
    else:
        assert math.isclose(stats["std"], statistics.stdev(every), rel_tol=1e-9, abs_tol=1e-6)


def test_an_int_column_keeps_int_min_and_max_and_strings_are_not_summarised():
    rows = [("totals", {"name": "a", "value": 7}), ("totals", {"name": "b", "value": -2})]
    stats = reduce_stats(rows, {}).stats
    assert list(stats) == [("totals", "value")]
    assert (stats[("totals", "value")].min, stats[("totals", "value")].max) == (-2, 7)
    assert type(stats[("totals", "value")].min) is int


def test_blocks_and_columns_select_and_every_row_is_counted():
    rows = series([0.5, 1.5]) + [("other", {"name": "x", "value": 3})]
    partial = reduce_stats(rows, {"blocks": ["s"], "columns": ["value"]})
    assert partial.rows == 3
    assert list(partial.stats) == [("s", "value")]


def test_summary_meets_the_contract(tmp_path):
    total = combine([reduce_stats(series([0.25]), {}), reduce_stats(series([0.75, -0.5]), {})])
    check(write_summary(stats_summary({"blocks": ["s"]}, 3, ["t3"], total), tmp_path))


@pytest.mark.parametrize(
    "aggregation,args",
    [
        ("histogram", {}),
        ("digest_list", {"blocks": ["s"]}),
        ("summary_stats", {"rows": 10}),
        ("summary_stats", {"blocks": "s"}),
        ("summary_stats", {"columns": [1]}),
    ],
)
def test_args_outside_the_registry_are_refused(aggregation, args):
    with pytest.raises(UsageError):
        check_args(aggregation, args)
