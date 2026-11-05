"""Staging against a client that records what it is asked to store, so no store is needed."""

import io
import os
from pathlib import Path

import pytest
from wdf_artifactclient.contract import registry_outputs
from wdf_artifactclient.errors import OutputMissing
from wdf_artifactclient.staging import (
    Skipped,
    capture_tail,
    collect_outputs,
    contract,
    stage_inputs,
    upload_tail,
)
from wdf_events.models import ProducerRef

JOB = "123e4567-e89b-42d3-a456-426614174000"
PRODUCER = ProducerRef(job=JOB, task="t0", attempt="r0a1")
SHA = "a" * 64


class Recorder:
    """Stands in for the client: remembers each call and the bytes of the file it was handed."""

    def __init__(self, objects=None):
        self.calls: list[tuple] = []
        self.objects = objects or {}

    def put_content(self, source, kind, name, **kwargs):
        self.calls.append(("put_content", kind, name, _bytes(source)))
        return ("content", kind, name)

    def put_attempt(self, source, kind, name, producer, tenant, batch, seed=None, **kwargs):
        self.calls.append(("put_attempt", kind, name, _bytes(source), seed))
        return ("attempt", kind, name)

    def get(self, reference, dest, **kwargs):
        self.calls.append(("get", reference.name))
        Path(dest).write_bytes(self.objects[reference.name])


def _bytes(source) -> bytes:
    return source.read_bytes() if isinstance(source, Path) else source


def reference(name: str, kind: str = "component") -> dict:
    uri = f"s3://wdf-components/sha256/{SHA[:2]}/{SHA}"
    return {"uri": uri, "sha256": SHA, "size": 3, "name": name, "kind": kind}


def collect(client, scratch, task_type="simulate", seed=1):
    return collect_outputs(client, scratch, task_type, PRODUCER, "acme", "b1", seed)


def test_declared_outputs_of_every_task_type_match_the_registry():
    declared = {
        name: sorted(out.kind for out in task.outputs)
        for name, task in contract().task_types.items()
    }
    assert declared == registry_outputs()


def test_missing_declared_output_is_output_missing(scratch):
    client = Recorder()
    (scratch / "output/component.wasm").write_bytes(b"wasm")
    with pytest.raises(OutputMissing) as raised:
        collect(client, scratch, "compile")
    assert raised.value.reason.startswith("OUTPUT_MISSING: ")
    assert "build-info.json" in str(raised.value)
    assert client.calls == []


def test_a_declared_output_that_is_not_a_regular_file_is_missing(scratch):
    (scratch / "output/real.xml").write_text("x")
    os.symlink("real.xml", scratch / "output/results.xml")
    with pytest.raises(OutputMissing):
        collect(Recorder(), scratch)
    (scratch / "output/results.xml").unlink()
    (scratch / "output/results.xml").mkdir()
    with pytest.raises(OutputMissing):
        collect(Recorder(), scratch)


def test_undeclared_file_is_not_uploaded_and_is_listed(scratch):
    client = Recorder()
    (scratch / "output/results.xml").write_text("<r/>")
    (scratch / "output/scratch.tmp").write_text("temporary")
    collected = collect(client, scratch)
    assert [call[2] for call in client.calls] == ["results.xml"]
    assert collected.undeclared == [Skipped("scratch.tmp", "file")]


def test_symlink_and_directory_are_ignored(scratch):
    client = Recorder()
    (scratch / "output/results.xml").write_text("<r/>")
    os.symlink("/etc/hostname", scratch / "output/link")
    (scratch / "output/sub").mkdir()
    (scratch / "output/sub/inner.bin").write_text("never read")
    collected = collect(client, scratch)
    assert [call[2] for call in client.calls] == ["results.xml"]
    assert collected.undeclared == [Skipped("link", "symlink"), Skipped("sub", "directory")]


def test_a_declared_output_above_its_cap_is_refused_before_any_upload(scratch, monkeypatch):
    client = Recorder()
    (scratch / "output/component.wasm").write_bytes(b"x" * 10)
    (scratch / "output/build-info.json").write_bytes(b"{" + b" " * 2_000_000 + b"}")
    with pytest.raises(ValueError, match="above its cap"):
        collect(client, scratch, "compile")
    assert client.calls == []


def test_the_seed_goes_only_to_a_kind_whose_key_has_one(scratch):
    client = Recorder()
    (scratch / "output/summary.json").write_text("{}")
    collect(client, scratch, "aggregate", seed=7)
    assert client.calls == [("put_attempt", "summary", "summary.json", b"{}", None)]
    (scratch / "output/results.xml").write_text("<r/>")
    client.calls.clear()
    collect(client, scratch, "simulate", seed=7)
    assert client.calls == [("put_attempt", "result", "results.xml", b"<r/>", 7)]


@pytest.mark.parametrize("cap", [0, 1, 10, 65536])
def test_tail_keeps_the_last_cap_bytes_even_on_failure(cap):
    data = bytes(range(256)) * 1000 + b"the guest failed\n"
    assert capture_tail(io.BytesIO(data), cap) == data[len(data) - cap :]
    assert capture_tail(io.BytesIO(b"short"), 65536) == b"short"
    assert capture_tail(io.BytesIO(b""), 10) == b""


def test_the_tail_buffer_stays_bounded():
    class Counting(io.RawIOBase):
        """A stream of 200 MiB that notes the largest read it was asked for."""

        left = 200 * 1024 * 1024
        largest = 0

        def readable(self):
            return True

        def read(self, size=-1):
            self.largest = max(self.largest, size)
            step = min(size, self.left)
            self.left -= step
            return b"y" * step

    stream = Counting()
    assert capture_tail(stream, 1000) == b"y" * 1000
    assert 0 < stream.largest <= 1024 * 1024


def test_the_uploaded_tail_lists_the_undeclared_entries_within_the_cap():
    client = Recorder()
    cap = contract().log_tail.cap_bytes
    upload_tail(
        client,
        b"z" * cap,
        PRODUCER,
        "acme",
        "b1",
        1,
        [Skipped("a.tmp", "file"), Skipped("d", "directory")],
    )
    (_, kind, name, body, seed) = client.calls[0]
    assert (kind, name, seed) == ("log-tail", "tail.log", 1)
    assert len(body) == cap
    assert body.endswith(b"\nundeclared: a.tmp\nundeclared: d (directory)")


def test_staged_inputs_are_read_only_and_named_by_reference(scratch):
    client = Recorder({"component.wasm": b"wasm", "data.bin": b"123"})
    staged = stage_inputs(client, [reference("component.wasm"), reference("data.bin")], scratch)
    assert sorted(path.name for path in (scratch / "input").iterdir()) == [
        "component.wasm",
        "data.bin",
    ]
    assert staged.paths["data.bin"].read_bytes() == b"123"
    for path in staged.paths.values():
        assert path.stat().st_mode & 0o222 == 0
        with pytest.raises(PermissionError):
            path.write_bytes(b"changed")
    (staged.directory / "input.json").write_text("{}")
    assert (scratch / "input/input.json").read_text() == "{}"


def test_staging_does_not_write_input_json(scratch):
    stage_inputs(Recorder({"a": b"x"}), [reference("a")], scratch)
    assert not (scratch / "input/input.json").exists()


@pytest.mark.parametrize("names", [["a", "a"], ["input.json"], ["sub/x"], [".."]])
def test_input_names_that_cannot_be_staged_are_refused_before_any_download(scratch, names):
    client = Recorder({name: b"x" for name in names})
    with pytest.raises(ValueError):
        stage_inputs(client, [reference(name) for name in names], scratch)
    assert client.calls == []
