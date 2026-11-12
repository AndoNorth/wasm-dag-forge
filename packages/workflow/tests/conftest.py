import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ACTIVITY = ROOT / "contracts/activity"
TEMPORAL = ROOT / "contracts/temporal"


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture
def task_input():
    """A valid task input of a task type, read from the vectors of the activity contract."""

    def read(task_type: str) -> dict:
        return json.loads((ACTIVITY / f"vectors/task-input/valid/{task_type}.json").read_text())

    return read


@pytest.fixture(scope="module")
def temporal(t1) -> str:
    """The address of a dev server with the namespace of naming.yaml, one per test module, which
    is gone before the next module runs."""
    from wdf_workflow.contract import naming

    with t1.container(
        "temporalio/temporal",
        ports={0: 7233},
        command=(
            "server",
            "start-dev",
            "--ip",
            "0.0.0.0",
            "--headless",
            "--namespace",
            naming().namespace,
        ),
    ) as container:
        container.wait_ready(0)
        yield f"127.0.0.1:{container.host_ports[0]}"
