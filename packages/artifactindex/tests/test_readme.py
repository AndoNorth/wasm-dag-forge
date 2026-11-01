"""The column tables of contracts/state/README.md name every field of the row model."""

import re
from pathlib import Path

from wdf_artifactindex.models import ArtifactRow

README = (Path(__file__).resolve().parents[3] / "contracts/state/README.md").read_text()


def columns(table: str) -> set[str]:
    section = re.search(rf"^## Table `{table}`$(.*?)(?=^## )", README, re.S | re.M).group(1)
    return set(re.findall(r"^\| `([a-z_0-9]+)` \|", section, re.M))


def test_readme_lists_every_model_field():
    assert columns("artifact") == set(ArtifactRow.model_fields)


def test_readme_states_the_attempt_rule():
    assert "The store never infers an attempt" in README
