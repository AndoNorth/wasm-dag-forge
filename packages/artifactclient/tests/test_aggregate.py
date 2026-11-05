"""The aggregate index builder and reader, without a store."""

import hashlib
import io
import tracemalloc
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from wdf_artifactclient.aggregate import build_index, read_index
from wdf_artifactclient.contract import Refused, check_index
from wdf_artifactclient.models import IndexEntry
from wdf_events.models import AttemptReference, ContentReference, ProducerRef

JOB = "123e4567-e89b-42d3-a456-426614174000"
TENANT = "acme"
HEADER = {"job": JOB, "tenant": TENANT, "task": "aggregate-0", "partial": "refuse"}
TASK_IDS = st.from_regex(r"[a-z0-9][a-z0-9._-]{0,20}", fullmatch=True)


def result(task: str, tenant: str = TENANT) -> AttemptReference:
    digest = hashlib.sha256(task.encode()).hexdigest()
    return AttemptReference(
        uri=f"s3://wdf-results/{tenant}/b1/{JOB}/1/{task}/r0a1/result.xml",
        sha256=digest,
        size=1024,
        name="result.xml",
        kind="result",
        producer=ProducerRef(job=JOB, task=task, attempt="r0a1"),
    )


def entry(task: str, status: str = "succeeded", tenant: str = TENANT) -> IndexEntry:
    reference = result(task, tenant) if status == "succeeded" else None
    return IndexEntry(task=task, status=status, reference=reference)


def built(entries, **header) -> bytes:
    out = io.BytesIO()
    build_index(entries, {**HEADER, **header}, out)
    return out.getvalue()


class IndexClient:
    """Serves one index object and has no other call, so a reader that lists cannot run."""

    def __init__(self, data: bytes):
        self.data = data

    def get(self, reference, dest: Path, **_):
        dest.write_bytes(self.data)


def index_reference(data: bytes) -> ContentReference:
    digest = hashlib.sha256(data).hexdigest()
    return ContentReference(
        uri=f"s3://wdf-sources/sha256/{digest[:2]}/{digest}",
        sha256=digest,
        size=len(data),
        name="index.jsonl",
        kind="index",
    )


def read(data: bytes, tenant: str = TENANT) -> list[IndexEntry]:
    return list(read_index(IndexClient(data), index_reference(data), tenant))


@given(st.lists(TASK_IDS, unique=True, max_size=20).flatmap(st.permutations))
def test_index_bytes_are_the_same_for_equal_sets_in_order(tasks):
    first = built(entry(t) for t in sorted(tasks))
    second = built(entry(t).model_dump() for t in sorted(tasks))
    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
    check_index(first.decode())
    assert [e.task for e in read(first)] == sorted(tasks)


def test_the_built_object_is_canonical_json_lines():
    data = built([entry("a"), entry("b", "failed")])
    lines = data.decode().split("\n")
    assert lines[0].startswith('{"job":')
    assert lines[2] == '{"status":"failed","task":"b"}'
    assert lines[-1] == ""


@pytest.mark.parametrize(
    "tasks", [["b", "a"], ["a", "a"], ["a", "c", "b"]], ids=["descending", "repeated", "late"]
)
def test_out_of_order_or_repeated_entries_are_refused(tasks):
    with pytest.raises(Refused, match="^out-of-order: "):
        built(entry(t) for t in tasks)


def test_an_entry_that_breaks_the_format_is_refused_by_the_builder():
    with pytest.raises(Refused, match="^entry-without-reference: "):
        built([IndexEntry(task="a", status="succeeded")])
    with pytest.raises(Refused, match="^reference-with-failure-status: "):
        built([IndexEntry(task="a", status="failed", reference=result("a"))])
    summary = result("a").model_copy(update={"kind": "summary"})
    with pytest.raises(Refused, match="^wrong-kind: "):
        built([IndexEntry(task="a", status="succeeded", reference=summary)])


def test_partial_policy_refuse_and_allow():
    entries = [entry("a"), entry("b", "failed"), entry("c", "missing")]
    with pytest.raises(Refused, match="^partial-refused: b is failed"):
        read(built(entries, partial="refuse"))
    assert [e.status for e in read(built(entries, partial="allow"))] == [
        "succeeded",
        "failed",
        "missing",
    ]


def test_foreign_tenant_result_is_refused():
    other = [entry("a"), entry("b", tenant="rival")]
    with pytest.raises(Refused, match="^foreign-tenant: b reads s3://wdf-results/rival/"):
        read(built(other, partial="allow"))
    with pytest.raises(Refused, match="^foreign-tenant: the index is of acme, not rival"):
        read(built([entry("a")]), tenant="rival")


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"", "header-not-first"),
        (built([entry("a")]).split(b"\n", 1)[1], "header-not-first"),
        (built([]) + built([]), "header-not-first"),
        (built([entry("a")]) + b"not json\n", "schema-violation"),
        (built([entry("a")]) + b'{"status":"seen","task":"b"}\n', "schema-violation"),
        (built([entry("b")]) + built([entry("a")]).split(b"\n", 1)[1], "out-of-order"),
    ],
)
def test_a_reader_refuses_what_the_loader_refuses(data, reason):
    with pytest.raises(Refused, match=f"^{reason}: "):
        read(data)


def test_a_reader_refuses_an_object_that_is_not_an_index():
    summary = index_reference(b"x").model_copy(update={"kind": "manifest"})
    with pytest.raises(Refused, match="^wrong-kind: "):
        list(read_index(IndexClient(b""), summary, TENANT))


class Sink:
    """A binary stream that keeps only the hash and the size of what it was given."""

    def __init__(self):
        self.digest, self.size = hashlib.sha256(), 0

    def write(self, data: bytes) -> None:
        self.digest.update(data)
        self.size += len(data)


MEMORY_BOUND = 4 * 1024 * 1024


def peak_of(run) -> int:
    tracemalloc.start()
    try:
        run()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_hundred_thousand_entries_build_in_bounded_memory(tmp_path):
    count, sink = 100_000, Sink()
    peak = peak_of(lambda: build_index((entry(f"t{i:06d}") for i in range(count)), HEADER, sink))
    assert sink.size > 8 * MEMORY_BOUND
    assert peak < MEMORY_BOUND

    path = tmp_path / "index.jsonl"
    with path.open("wb") as out:
        build_index((entry(f"t{i:06d}") for i in range(count)), HEADER, out)
    reference = index_reference(b"")
    reference = reference.model_copy(update={"size": path.stat().st_size})

    class FileClient:
        def get(self, reference, dest: Path, **_):
            dest.symlink_to(path)

    seen = 0

    def drain():
        nonlocal seen
        for _ in read_index(FileClient(), reference, TENANT):
            seen += 1

    assert peak_of(drain) < MEMORY_BOUND
    assert seen == count
