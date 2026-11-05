"""The stand-in guest and runner."""

import pytest
from wdf_artifactclient.testing import RUNNERS, make_scratch, run_guest


def test_a_guest_reads_the_input_and_writes_the_output_and_its_log_is_returned(scratch):
    (scratch / "input/in.txt").write_text("abc")

    def guest(input_dir, output_dir):
        (output_dir / "out.txt").write_text((input_dir / "in.txt").read_text().upper())
        print("to stdout")
        import sys

        print("to stderr", file=sys.stderr)

    log = run_guest(guest, scratch)
    assert (scratch / "output/out.txt").read_text() == "ABC"
    assert log == b"to stdout\nto stderr\n"


def test_a_guest_failure_propagates(scratch):
    def guest(input_dir, output_dir):
        raise RuntimeError("guest failed")

    with pytest.raises(RuntimeError, match="guest failed"):
        run_guest(guest, scratch)


def test_make_scratch_is_idempotent(tmp_path):
    assert make_scratch(tmp_path / "s") == make_scratch(tmp_path / "s")
    assert sorted(p.name for p in (tmp_path / "s").iterdir()) == ["input", "output"]


def test_the_runner_fixture_covers_every_registered_runner(runner, scratch):
    assert runner in RUNNERS.values()
    assert runner(lambda i, o: None, scratch) == b""
