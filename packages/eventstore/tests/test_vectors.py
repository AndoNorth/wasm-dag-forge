"""Every function of the package is implemented, so the list of stubbed names is empty."""

import inspect

import wdf_eventstore
from wdf_eventstore import STUBBED

FUNCTIONS = [
    getattr(wdf_eventstore, name)
    for name in wdf_eventstore.__all__
    if inspect.isfunction(getattr(wdf_eventstore, name))
]


def test_no_name_is_stubbed():
    assert STUBBED == ()


def test_no_function_raises_the_lane_error():
    assert {f.__name__ for f in FUNCTIONS} == {"chunk_count", "events_for_job", "insert_events"}
    assert all("not implemented" not in inspect.getsource(f) for f in FUNCTIONS)
