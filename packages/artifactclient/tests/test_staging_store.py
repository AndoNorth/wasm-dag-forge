"""Staging through the real client against both object store products."""

import hashlib
import io
import os
import time

import pytest
from wdf_artifactclient.client import ArtifactClient
from wdf_artifactclient.errors import OutputMissing
from wdf_artifactclient.staging import (
    capture_tail,
    collect_outputs,
    contract,
    stage_inputs,
    upload_tail,
)
from wdf_artifactclient.testing import make_scratch
from wdf_events.models import ProducerRef

pytestmark = pytest.mark.integration

JOB = "123e4567-e89b-42d3-a456-426614174000"
LOG = "".join(f"log line {n}\n" for n in range(20000))


def producer(task: str = "t0", attempt: str = "r0a1") -> ProducerRef:
    return ProducerRef(job=JOB, task=task, attempt=attempt)


@pytest.fixture(scope="module", autouse=True)
def buckets(store):
    store.store().create_buckets()


@pytest.fixture
def role(store):
    return lambda name: ArtifactClient(store.endpoint, store.credentials[name])


@pytest.fixture
def unique(request):
    return hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:12]


def guest_of(outputs: dict[str, bytes], log: str = ""):
    def guest(input_dir, output_dir):
        for name, data in outputs.items():
            (output_dir / name).write_bytes(data)
        print(log, end="")

    return guest


def test_compile_outputs_are_content_addressed_and_simulate_output_is_attempt_scoped(
    role, unique, scratch, runner, tmp_path
):
    wasm, info = b"\0asm component", b'{"toolchain": "x"}'
    runner(guest_of({"component.wasm": wasm, "build-info.json": info}), scratch)
    built = collect_outputs(role("compile"), scratch, "compile", producer(), unique, "b1", 0)
    by_name = {reference.name: reference for reference in built.references}
    for name, data in (("component.wasm", wasm), ("build-info.json", info)):
        digest = hashlib.sha256(data).hexdigest()
        assert by_name[name].uri.endswith(f"/sha256/{digest[:2]}/{digest}")
        assert not hasattr(by_name[name], "producer")
    assert [r.kind for r in built.references] == ["component", "build-info"]

    again = make_scratch(scratch.parent / "again")
    runner(guest_of({"component.wasm": wasm, "build-info.json": info}), again)
    repeated = collect_outputs(
        role("compile"), again, "compile", producer(attempt="r0a2"), unique, "b1", 0
    )
    assert repeated.references == built.references

    scratch2 = make_scratch(scratch.parent / "sim")
    runner(guest_of({"results.xml": b"<results/>"}), scratch2)
    first = collect_outputs(role("simulate"), scratch2, "simulate", producer("t1"), unique, "b1", 5)
    scratch3 = make_scratch(scratch.parent / "sim2")
    runner(guest_of({"results.xml": b"<results/>"}), scratch3)
    second = collect_outputs(
        role("simulate"), scratch3, "simulate", producer("t1", "r0a2"), unique, "b1", 5
    )
    (one,), (two,) = first.references, second.references
    assert one.sha256 == two.sha256
    assert one.uri != two.uri
    assert f"/{JOB}/5/t1/r0a1/results.xml" in one.uri and f"/{JOB}/5/t1/r0a2/" in two.uri
    assert one.producer == producer("t1")
    role("workflow").get(built.references[0], tmp_path / "w.wasm")
    assert (tmp_path / "w.wasm").read_bytes() == wasm


def test_failed_compile_uploads_its_tail_under_the_compile_role(role, unique, scratch, runner):
    cap = contract().log_tail.cap_bytes
    log = runner(guest_of({"stray.tmp": b"left behind"}, log=LOG), scratch)
    tail = capture_tail(io.BytesIO(log), cap)
    with pytest.raises(OutputMissing) as failed:
        collect_outputs(role("compile"), scratch, "compile", producer(), unique, "b1", 0)
    assert failed.value.code == "OUTPUT_MISSING"

    reference = upload_tail(role("compile"), tail, producer(), unique, "b1", 0)
    assert (reference.kind, reference.name, reference.size) == ("log-tail", "tail.log", cap)
    stored = role("workflow").get_bytes(reference, cap)
    assert stored == log[-cap:]
    assert "wdf-results" in reference.uri and f"/{JOB}/0/t0/r0a1/tail.log" in reference.uri


def test_a_thousand_small_outputs(role, unique, scratch, runner, store):
    client = role("simulate")
    started = time.monotonic()
    references = []
    for seed in range(1000):
        scratch_n = scratch / f"n{seed}"
        os.makedirs(scratch_n / "output")
        os.makedirs(scratch_n / "input")
        runner(guest_of({"results.xml": f"<r seed={seed}/>".encode()}), scratch_n)
        (reference,) = collect_outputs(
            client, scratch_n, "simulate", producer("t2"), unique, "b1", seed
        ).references
        references.append(reference)
    seconds = time.monotonic() - started
    print(f"1000 outputs in {seconds:.1f} s on {store.product}")
    assert len({reference.uri for reference in references}) == 1000
    reader = role("aggregate")
    for seed in (0, 499, 999):
        assert reader.get_bytes(references[seed], 100) == f"<r seed={seed}/>".encode()


def test_staged_inputs_come_from_the_store_verified_and_read_only(role, unique, scratch, tmp_path):
    wasm = role("compile").put_content(b"\0asm one", "component", "component.wasm")
    info = role("compile").put_content(b"{}", "build-info", "build-info.json")
    staged = stage_inputs(role("simulate"), [wasm, info], scratch)
    assert staged.paths["component.wasm"].read_bytes() == b"\0asm one"
    assert staged.paths["build-info.json"].stat().st_mode & 0o222 == 0
    assert sorted(path.name for path in (scratch / "input").iterdir()) == [
        "build-info.json",
        "component.wasm",
    ]


def test_staging_accepts_any_runner_callable(role, unique, scratch, runner):
    """The runner is a fixture parameter, so a real runner joins RUNNERS and passes this test."""
    wasm = role("compile").put_content(b"\0asm two", "component", "component.wasm")
    staged = stage_inputs(role("simulate"), [wasm], scratch)
    (staged.directory / "input.json").write_text("{}")

    def guest(input_dir, output_dir):
        print("simulating")
        (output_dir / "results.xml").write_bytes((input_dir / "component.wasm").read_bytes()[:4])

    log = runner(guest, scratch)
    assert log == b"simulating\n"
    (reference,) = collect_outputs(
        role("simulate"), scratch, "simulate", producer("t3"), unique, "b1", 1
    ).references
    assert role("aggregate").get_bytes(reference, 100) == b"\0asm"
