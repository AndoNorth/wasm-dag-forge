"""The README of the artifact contract states the version and names every consumer."""

import re
from pathlib import Path

ARTIFACT = Path(__file__).resolve().parents[3] / "contracts/artifact"
README = (ARTIFACT / "README.md").read_text()
CONSUMERS = ("#3", "#4", "#5", "#6", "#10", "#11", "#13", "#15", "#16", "#17", "#18", "#24")


def section(title: str) -> str:
    match = re.search(rf"^## {title}\n(.*?)(?=^## |\Z)", README, re.S | re.M)
    assert match, f"no section {title}"
    return match.group(1)


def test_the_version_file_and_the_readme_agree():
    version = (ARTIFACT / "VERSION").read_text().strip()
    assert version == "3"
    assert f"Version: {version}\n" in README


def test_the_consumers_section_names_every_consumer():
    consumers = section("Consumers")
    for issue in CONSUMERS:
        assert re.search(rf"{re.escape(issue)}\b", consumers), issue


def test_the_readme_describes_the_new_files_and_the_loader():
    for name in ("failures.yaml", "staging.yaml", "retention.yaml", "index.schema.json"):
        assert name in README
    assert "python -m wdf_artifactclient.contract --check" in README
    assert "python -m wdf_artifactclient.gen" in README
