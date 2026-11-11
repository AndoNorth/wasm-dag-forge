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
