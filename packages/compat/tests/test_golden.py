"""Golden events: rebuilt from fixed inputs, byte-stable through the codec, and in the manifest."""

import shutil

import pytest
from wdf_compat import read_event
from wdf_compat.vectors import GOLDEN, MANIFEST, check_manifest, golden_files, main
from wdf_events import from_bytes, to_bytes
from wdf_events.models import DATA_MODELS

FILES = golden_files()


def test_there_is_one_golden_file_per_type_and_minor():
    assert sorted(FILES) == sorted(f"{name}/{version}.json" for name, version in DATA_MODELS)


@pytest.mark.parametrize("path", sorted(FILES))
def test_a_golden_file_is_its_rebuild_and_survives_a_round_trip(path):
    committed = (GOLDEN / path).read_bytes()
    assert committed == FILES[path]
    assert to_bytes(from_bytes(committed)) == committed
    assert read_event(committed) == from_bytes(committed)


def test_the_committed_files_match_what_the_builders_write():
    assert main(["--check"]) == 0


def test_the_committed_manifest_agrees_with_the_files():
    assert check_manifest(GOLDEN) == []


@pytest.fixture
def copy(tmp_path):
    shutil.copytree(GOLDEN, tmp_path / "golden")
    return tmp_path / "golden"


def test_a_file_without_a_line_fails(copy):
    lines = (copy / MANIFEST).read_text().splitlines(keepends=True)
    (copy / MANIFEST).write_text("".join(lines[1:]))
    assert check_manifest(copy) == [f"{lines[0].split('  ')[1].strip()}: file has no line"]


def test_a_line_without_a_file_fails(copy):
    (copy / "job.failed/1.0.json").unlink()
    assert check_manifest(copy) == ["job.failed/1.0.json: line has no file"]


def test_a_changed_hash_fails(copy):
    (copy / "job.failed/1.0.json").write_bytes(b"{}")
    assert check_manifest(copy) == ["job.failed/1.0.json: hash differs"]
