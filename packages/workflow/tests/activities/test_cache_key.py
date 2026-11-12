"""The compile cache key of the workload bundle equals the key that the compile library builds it
under."""

import json
import subprocess
import sys

import pytest
from wdf_workflow.activities.cache import bundle_key
from wdf_workloads import bundle

ENTRY = "wdf_workloads.main"


@pytest.mark.slow
def test_cache_key_matches_build_info(tmp_path):
    path = bundle.write(tmp_path)
    key = bundle_key(path, ENTRY)
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "wdf_compile",
            "build",
            "--bundle",
            str(path),
            "--entry",
            ENTRY,
            "--out",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    print(built.stdout, end="")
    assert built.stdout == f"cache_key: {key}\n"
    assert json.loads((tmp_path / "out/build-info.json").read_text())["cache_key"] == key
