"""The heartbeats of a guest's stderr, counted from the lines that match the guest contract."""

from wdf_runtime.heartbeat import beats

LINES = [
    "starting\n",
    'HEARTBEAT {"progress":0.5,"step":1,"steps":2}\n',
    'HEARTBEAT {"progress":1.5,"step":2,"steps":2}\n',
    'HEARTBEAT {"step":2,"steps":2,"progress":1}\n',
    "HEARTBEAT\n",
    'HEARTBEAT {"progress":1,"step":2,"steps":2}\r\n',
    'note: HEARTBEAT {"progress":1,"step":2,"steps":2}\n',
]


def test_only_well_formed_heartbeats_count():
    assert list(beats(LINES)) == [
        {"progress": 0.5, "step": 1, "steps": 2},
        {"progress": 1, "step": 2, "steps": 2},
    ]


def test_no_heartbeat_in_free_output():
    assert list(beats(["", "Error: failed\n", "HEARTBEATS 3\n"])) == []
