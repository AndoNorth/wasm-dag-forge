"""The client against both object store products, one container per product for this module."""

import hashlib
import io
import subprocess
import sys
import threading
import time

import pytest
from botocore.exceptions import ClientError
from wdf_artifactclient.client import PART_BYTES, ArtifactClient, RetryPolicy, verify
from wdf_artifactclient.errors import (
    ArtifactCorrupt,
    ArtifactLost,
    ArtifactNotFound,
    StoreForbidden,
    StoreUnavailable,
)
from wdf_artifactclient.keys import split_uri
from wdf_events.models import ProducerRef
from wdf_storecheck.s3 import Creds, dump_credentials
from wdf_storecheck.s3 import client as s3_client

pytestmark = pytest.mark.integration

MIB = 1024 * 1024
JOB = "123e4567-e89b-42d3-a456-426614174000"
BOUNDARY_SIZES = [0, 1, PART_BYTES, PART_BYTES + 1, 40 * MIB]


def payload(size: int, label: str = "") -> bytes:
    return hashlib.shake_256(f"payload {size} {label}".encode()).digest(size)


def producer(task: str = "t0", attempt: str = "r0a1") -> ProducerRef:
    return ProducerRef(job=JOB, task=task, attempt=attempt)


class Naps:
    """A retry policy that records its sleeps and does not wait."""

    def __init__(self):
        self.sleeps: list[float] = []
        self.policy = RetryPolicy(sleep=self.sleeps.append)


@pytest.fixture(scope="module", autouse=True)
def buckets(store):
    store.store().create_buckets()


@pytest.fixture
def naps():
    return Naps()


@pytest.fixture
def role(store, naps):
    """A client of a role of the contract, with no real waiting between tries."""
    return lambda name: ArtifactClient(store.endpoint, store.credentials[name], naps.policy)


@pytest.fixture
def admin(store):
    return s3_client(store.endpoint, store.credentials["admin"])


@pytest.fixture
def unique(request):
    """A tenant name no other test of the module shares, so that keys never collide."""
    return hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:12]


def put_result(role, unique, data: bytes, task: str = "t0", name: str = "out.bin", **kwargs):
    return role("aggregate").put_attempt(
        data, "result", name, producer(task), unique, "b1", 1, **kwargs
    )


@pytest.mark.parametrize("size", BOUNDARY_SIZES)
def test_round_trip_at_the_part_boundary(size, role, unique, tmp_path):
    data = payload(size)
    sha256 = hashlib.sha256(data).hexdigest()

    scoped = put_result(role, unique, data)
    assert (scoped.sha256, scoped.size) == (sha256, size)
    content = role("indexer").put_content(data, "index", "idx.jsonl")
    assert (content.sha256, content.size) == (sha256, size)
    assert content.uri == f"s3://wdf-sources/sha256/{sha256[:2]}/{sha256}"

    again = role("indexer").put_content(tmp_path_of(tmp_path, data), "index", "idx.jsonl")
    assert again == content

    out = tmp_path / "out.bin"
    role("aggregate").get(scoped, out)
    assert out.read_bytes() == data
    role("workflow").get(content, tmp_path / "content.bin")
    assert (tmp_path / "content.bin").read_bytes() == data
    assert role("workflow").head(content).size == size
    if size <= MIB:
        assert role("workflow").get_bytes(content, MIB) == data


def tmp_path_of(tmp_path, data: bytes):
    path = tmp_path / "source.bin"
    path.write_bytes(data)
    return path


def test_get_bytes_refuses_an_object_above_the_limit(role, unique):
    reference = put_result(role, unique, b"x" * 100)
    with pytest.raises(ValueError, match="above"):
        role("aggregate").get_bytes(reference, 99)


def test_flipped_byte_is_corrupt_then_lost(role, unique, admin, tmp_path):
    data = payload(MIB + 3)
    reference = put_result(role, unique, data)
    bucket, key = split_uri(reference.uri)
    flipped = bytearray(data)
    flipped[len(data) // 2] ^= 0x01
    admin.put_object(Bucket=bucket, Key=key, Body=bytes(flipped))

    dest = tmp_path / "out.bin"
    with pytest.raises(ArtifactLost) as lost:
        role("aggregate").get(reference, dest)
    assert isinstance(lost.value.cause, ArtifactCorrupt)
    assert lost.value.reason.startswith("ARTIFACT_LOST: ")
    assert list(tmp_path.iterdir()) == []

    admin.put_object(Bucket=bucket, Key=key, Body=data)
    role("aggregate").get(reference, dest)
    assert dest.read_bytes() == data


def test_a_truncated_object_is_corrupt_then_lost(role, unique, admin, tmp_path):
    reference = put_result(role, unique, payload(4096))
    bucket, key = split_uri(reference.uri)
    admin.put_object(Bucket=bucket, Key=key, Body=payload(4096)[:-1])
    with pytest.raises(ArtifactLost) as lost:
        role("aggregate").get(reference, tmp_path / "out.bin")
    assert isinstance(lost.value.cause, ArtifactCorrupt)


def test_deleted_object_is_not_found_then_lost(role, unique, admin, tmp_path):
    reference = put_result(role, unique, payload(2048))
    bucket, key = split_uri(reference.uri)
    admin.delete_object(Bucket=bucket, Key=key)
    with pytest.raises(ArtifactLost) as lost:
        role("aggregate").get(reference, tmp_path / "out.bin")
    assert isinstance(lost.value.cause, ArtifactNotFound)
    with pytest.raises(ArtifactNotFound):
        role("aggregate").head(reference)
    assert list(tmp_path.iterdir()) == []


def test_verify_reads_through_the_hash_check_and_keeps_nothing(role, unique, admin):
    reference = put_result(role, unique, payload(1000))
    verify(role("aggregate"), reference)
    bucket, key = split_uri(reference.uri)
    admin.put_object(Bucket=bucket, Key=key, Body=payload(1000, "other"))
    with pytest.raises(ArtifactLost) as lost:
        verify(role("aggregate"), reference)
    assert lost.value.cause.code == "ARTIFACT_CORRUPT"


def test_wrong_credentials_are_forbidden_and_not_retried(store, role, unique, naps, tmp_path):
    reference = put_result(role, unique, b"payload")
    wrong = ArtifactClient(store.endpoint, Creds("nobody", "nothing"), naps.policy)
    with pytest.raises(StoreForbidden):
        wrong.get(reference, tmp_path / "out.bin")
    with pytest.raises(StoreForbidden):
        wrong.put_attempt(b"x", "result", "o", producer(), unique, "b1", 1)
    stranger = role("submit")
    with pytest.raises(StoreForbidden):
        stranger.put_attempt(b"x", "result", "o", producer(), unique, "b1", 1)
    assert naps.sleeps == []


def uploads(admin, bucket: str, prefix: str) -> list:
    return admin.list_multipart_uploads(Bucket=bucket, Prefix=prefix).get("Uploads", [])


def test_multipart_abort_leaves_no_parts(role, unique, admin):
    """A failure on the third of five parts, whatever raises it, aborts the upload."""
    beats = []

    def fail_on_third(count: int) -> None:
        beats.append(count)
        if len(beats) == 3:
            raise RuntimeError("the caller gave up")

    with pytest.raises(RuntimeError, match="gave up"):
        put_result(role, unique, payload(40 * MIB), progress=fail_on_third)
    assert uploads(admin, "wdf-results", unique) == []

    class Failing(io.RawIOBase):
        def __init__(self):
            self.sent = 0

        def readable(self):
            return True

        def readinto(self, buffer):
            if self.sent > 20 * MIB:
                raise OSError("the source broke")
            buffer[:] = bytes(len(buffer))
            self.sent += len(buffer)
            return len(buffer)

    with pytest.raises(OSError, match="source broke"):
        put_result(role, unique, io.BufferedReader(Failing()), name="broken.bin")
    assert uploads(admin, "wdf-results", unique) == []


def test_multipart_that_finds_the_object_there_is_a_success_and_leaves_no_parts(
    role, unique, admin
):
    data = payload(PART_BYTES + 5)
    first = role("indexer").put_content(data, "index", "idx.jsonl")
    again = role("indexer").put_content(data, "index", "idx.jsonl")
    assert first == again
    assert uploads(admin, "wdf-sources", "sha256/") == []


class Recorder(io.RawIOBase):
    """A stream of zeros that records the size of every read asked of it."""

    def __init__(self, size: int):
        self.left, self.reads = size, []

    def readable(self):
        return True

    def read(self, size=-1):
        self.reads.append(size)
        count = min(self.left, size if size >= 0 else self.left)
        self.left -= count
        return bytes(count)


def test_stream_is_read_chunk_by_chunk(store, unique, naps):
    chunk = 64 * 1024
    client = ArtifactClient(store.endpoint, store.credentials["aggregate"], naps.policy, chunk)
    stream = Recorder(20 * MIB)
    reference = client.put_attempt(stream, "result", "z.bin", producer(), unique, "b1", 1)
    assert reference.size == 20 * MIB
    assert reference.sha256 == hashlib.sha256(bytes(20 * MIB)).hexdigest()
    assert all(0 < size <= chunk for size in stream.reads), set(stream.reads)
    assert len(stream.reads) >= 20 * MIB // chunk


def test_a_stream_above_the_cap_is_refused_without_reading_it_all(store, naps):
    client = ArtifactClient(store.endpoint, store.credentials["submit"], naps.policy)
    stream = Recorder(10 * MIB)
    with pytest.raises(ValueError, match="cap"):
        client.put_content(stream, "manifest", "m.json")
    assert stream.left > 0


def test_heartbeat_gap_during_a_large_transfer(role, unique, tmp_path):
    """A thread that beats every 100 ms keeps beating while 256 MiB move through the client."""
    source = tmp_path / "big.bin"
    block = payload(MIB, "heartbeat")
    with source.open("wb") as handle:
        for _ in range(256):
            handle.write(block)
    beats, running = [time.monotonic()], threading.Event()
    running.set()

    def beat():
        while running.is_set():
            time.sleep(0.1)
            beats.append(time.monotonic())

    thread = threading.Thread(target=beat)
    thread.start()
    try:
        # The output cap of the staging contract is 256 MiB.
        reference = role("aggregate").put_attempt(
            source, "output", "big.bin", producer(), unique, "b1", 1
        )
        role("aggregate").get(reference, tmp_path / "back.bin")
    finally:
        running.clear()
        thread.join()
    gap = max(b - a for a, b in zip(beats, beats[1:], strict=False))
    print(
        f"largest heartbeat gap during a 256 MiB put and get: {gap:.3f} s over {len(beats)} beats"
    )
    assert (tmp_path / "back.bin").stat().st_size == 256 * MIB
    assert gap < 1.0


def test_an_expired_deadline_stops_a_transfer(role, unique, tmp_path):
    with pytest.raises(StoreUnavailable, match="deadline"):
        put_result(role, unique, payload(20 * MIB), deadline=1e-9)


def test_product_facts_if_none_match_and_the_md5_entity_tag(store, admin):
    """Records what the product does, as facts and not as a gate: the client works either way."""
    key = f"facts/{store.product}"
    body = b"facts"
    md5 = hashlib.md5(body, usedforsecurity=False).hexdigest()
    first = admin.put_object(Bucket="wdf-sources", Key=key, Body=body, IfNoneMatch="*")
    try:
        admin.put_object(Bucket="wdf-sources", Key=key, Body=b"other", IfNoneMatch="*")
        honoured = "overwritten"
    except ClientError as error:
        honoured = f"refused with {error.response['ResponseMetadata']['HTTPStatusCode']}"
    etag = first.get("ETag", "").strip('"')
    print(f"fact {store.product}: If-None-Match put on an existing object is {honoured}")
    print(f"fact {store.product}: single part entity tag is {'the MD5' if etag == md5 else etag}")
    completed = multipart(admin, key)
    print(f"fact {store.product}: If-None-Match completion of a multipart upload is {completed}")
    admin.delete_object(Bucket="wdf-sources", Key=key)


def multipart(admin, key: str) -> str:
    """What completing a multipart upload with If-None-Match does to an object that exists."""
    upload = admin.create_multipart_upload(Bucket="wdf-sources", Key=key)["UploadId"]
    parts = [b"a" * 5 * MIB, b"b"]
    done = [
        {
            "PartNumber": number,
            "ETag": admin.upload_part(
                Bucket="wdf-sources", Key=key, UploadId=upload, PartNumber=number, Body=body
            )["ETag"],
        }
        for number, body in enumerate(parts, start=1)
    ]
    try:
        admin.complete_multipart_upload(
            Bucket="wdf-sources",
            Key=key,
            UploadId=upload,
            MultipartUpload={"Parts": done},
            IfNoneMatch="*",
        )
    except ClientError as error:
        admin.abort_multipart_upload(Bucket="wdf-sources", Key=key, UploadId=upload)
        return f"refused with {error.response['ResponseMetadata']['HTTPStatusCode']}"
    return "overwritten"


def run_verify(store, role: str, reference, tmp_path, endpoint: str | None = None):
    credentials, path = tmp_path / "credentials.json", tmp_path / "reference.json"
    dump_credentials(credentials, store.credentials)
    path.write_text(reference if isinstance(reference, str) else reference.model_dump_json())
    command = [
        sys.executable,
        "-m",
        "wdf_artifactclient",
        "verify",
        "--endpoint",
        endpoint or store.endpoint,
        "--credentials",
        str(credentials),
        "--role",
        role,
        str(path),
    ]
    return subprocess.run(command, capture_output=True, text=True, timeout=120)


def test_verify_command_exits_0_for_an_intact_object(store, role, unique, tmp_path):
    reference = put_result(role, unique, payload(3000))
    done = run_verify(store, "aggregate", reference, tmp_path)
    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout.startswith(f"verified {reference.uri} 3000 bytes")


def test_verify_command_reports_a_flipped_byte_as_lost_with_its_cause(
    store, role, unique, admin, tmp_path
):
    data = payload(3000)
    reference = put_result(role, unique, data)
    bucket, key = split_uri(reference.uri)
    admin.put_object(Bucket=bucket, Key=key, Body=data[:100] + b"\x00" + data[101:])
    done = run_verify(store, "aggregate", reference, tmp_path)
    assert done.returncode == 1
    assert "code: ARTIFACT_LOST" in done.stderr.splitlines()
    assert "cause: ARTIFACT_CORRUPT" in done.stderr.splitlines()


def test_verify_command_reports_a_deleted_object_and_a_refused_credential(
    store, role, unique, admin, tmp_path
):
    reference = put_result(role, unique, payload(10))
    bucket, key = split_uri(reference.uri)
    admin.delete_object(Bucket=bucket, Key=key)
    gone = run_verify(store, "aggregate", reference, tmp_path)
    assert gone.returncode == 1
    assert {"code: ARTIFACT_LOST", "cause: ARTIFACT_NOT_FOUND"} <= set(gone.stderr.splitlines())
    refused = run_verify(store, "submit", reference, tmp_path)
    assert refused.returncode == 1
    assert "code: STORE_FORBIDDEN" in refused.stderr.splitlines()


def test_verify_command_exits_2_for_what_it_cannot_read(store, role, unique, tmp_path):
    reference = put_result(role, unique, payload(10))
    assert run_verify(store, "nobody", reference, tmp_path).returncode == 2
    assert run_verify(store, "aggregate", "not json", tmp_path).returncode == 2
    assert run_verify(store, "aggregate", '{"uri": "s3://x/y"}', tmp_path).returncode == 2
