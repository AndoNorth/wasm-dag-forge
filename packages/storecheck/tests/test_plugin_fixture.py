"""The store fixtures come from the pytest11 entry point and not from a conftest.py."""

import subprocess
import sys
from importlib.metadata import entry_points


def test_plugin_fixture_is_registered_under_pytest11():
    points = {e.name: e.value for e in entry_points(group="pytest11")}
    assert points["wdf_storecheck"] == "wdf_storecheck.fixtures"


def test_plugin_fixture_resolves_store_and_store_session_with_no_conftest(tmp_path):
    listing = subprocess.run(
        [sys.executable, "-m", "pytest", "--fixtures", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout
    assert not list(tmp_path.rglob("conftest.py"))
    for name, scope in (("store", "module"), ("store_session", "session")):
        line = next(line for line in listing.splitlines() if line.startswith(f"{name} "))
        assert f"[{scope} scope]" in line and "wdf_storecheck/fixtures.py" in line, line
