"""The window constants and the catalogue rule that a reader never meets a major two steps back."""

import json
from pathlib import Path

from wdf_compat import check_catalogue, readable_days, window
from wdf_events.models import INTRODUCED

FIXTURES = Path(__file__).parent / "fixtures"


def catalogue(name: str) -> dict[tuple[str, int], str]:
    return {
        (type_name, int(major)): day
        for type_name, majors in json.loads((FIXTURES / name).read_text()).items()
        for major, day in majors.items()
    }


def test_the_placeholders_and_the_window_they_give():
    assert window.MAX_JOB_DURATION_DAYS == 7
    assert window.DEAD_LETTER_RETENTION_DAYS == 14
    assert readable_days() == 21


def test_majors_20_days_apart_fail():
    assert check_catalogue(catalogue("catalogue-20-days.json")) == [
        "job.fixture v2 is introduced 20 days after v1, the window is 21 days"
    ]


def test_majors_22_days_apart_pass():
    assert check_catalogue(catalogue("catalogue-22-days.json")) == []


def test_majors_exactly_one_window_apart_fail():
    assert check_catalogue({("job.fixture", 1): "2026-01-01", ("job.fixture", 2): "2026-01-22"})


def test_the_catalogue_passes():
    assert check_catalogue(INTRODUCED) == []


def test_the_window_follows_its_constants(monkeypatch):
    monkeypatch.setattr(window, "DEAD_LETTER_RETENTION_DAYS", 30)
    assert readable_days() == 37
