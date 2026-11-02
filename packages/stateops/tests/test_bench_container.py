"""Without a DSN the bench starts and removes its own container."""

import pytest
from wdf_stateops import bench


def test_bench_without_a_dsn_runs_in_its_own_container(capsys):
    assert bench.main(["--jobs", "50", "--twice"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "rows_inserted: 50" in out and "events_inserted: 700" in out
    assert "rows_inserted: 0" in out and "events_inserted: 0" in out


pytestmark = pytest.mark.integration
