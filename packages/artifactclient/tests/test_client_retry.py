"""The retry policy and the classification of store responses, on a scripted store."""

import hashlib
import json
import subprocess
import sys
import time
from collections import deque

import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError
from wdf_artifactclient.client import ArtifactClient, RetryPolicy
from wdf_artifactclient.errors import (
    ArtifactCorrupt,
    ArtifactLost,
    ArtifactNotFound,
    OutputUploadFailed,
    StoreForbidden,
    StoreUnavailable,
)
from wdf_events.models import ProducerRef
from wdf_storecheck.s3 import Creds, dump_credentials

PRODUCER = ProducerRef(job="123e4567-e89b-42d3-a456-426614174000", task="t", attempt="r0a1")
BYTES = b"artifact bytes"
SHA = hashlib.sha256(BYTES).hexdigest()
REFERENCE = {
    "uri": f"s3://wdf-sources/sha256/{SHA[:2]}/{SHA}",
    "sha256": SHA,
    "size": len(BYTES),
    "name": "n",
    "kind": "manifest",
}


def response(status: int, code: str, operation: str = "GetObject") -> ClientError:
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, operation
    )


class Clock:
    """A clock that only the sleep of the policy moves."""

    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Body:
    def __init__(self, data: bytes):
        self.data = data

    def iter_chunks(self, size):
        for start in range(0, len(self.data), size):
            yield self.data[start : start + size]

    def close(self):
        pass


class Store:
    """A store that answers each call with the next scripted outcome, and the last one for ever."""

    def __init__(self, *outcomes):
        self.outcomes, self.calls = deque(outcomes), 0

    def answer(self):
        self.calls += 1
        outcome = self.outcomes[0] if len(self.outcomes) == 1 else self.outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def get_object(self, **_):
        reply = self.answer()
        return {"Body": Body(reply)}

    def put_object(self, **_):
        return self.answer()

    def head_object(self, **_):
        return self.answer()


def make(store: Store, **policy) -> tuple[ArtifactClient, Clock]:
    clock = Clock()
    retry = RetryPolicy(clock=clock, sleep=clock.sleep, random=lambda: 0.0, **policy)
    client = ArtifactClient("http://127.0.0.1:1", Creds("a", "b"), retry)
    client._s3 = store
    return client, clock


def test_a_missing_object_is_fetched_once_more_and_never_waited_for(tmp_path):
    store = Store(response(404, "NoSuchKey"))
    client, clock = make(store)
    with pytest.raises(ArtifactLost) as lost:
        client.get(REFERENCE, tmp_path / "out")
    assert isinstance(lost.value.cause, ArtifactNotFound)
    assert (store.calls, clock.sleeps) == (2, [])
    assert not list(tmp_path.iterdir())


def test_a_corrupt_object_is_fetched_once_more_and_never_waited_for(tmp_path):
    store = Store(b"artifact bytez")
    client, clock = make(store)
    with pytest.raises(ArtifactLost) as lost:
        client.get(REFERENCE, tmp_path / "out")
    assert isinstance(lost.value.cause, ArtifactCorrupt)
    assert (store.calls, clock.sleeps) == (2, [])
    assert not list(tmp_path.iterdir())


def test_a_corrupt_object_that_is_whole_on_the_second_fetch_succeeds(tmp_path):
    store = Store(b"artifact bytez", BYTES)
    client, _ = make(store)
    client.get(REFERENCE, tmp_path / "out")
    assert (tmp_path / "out").read_bytes() == BYTES


@pytest.mark.parametrize("code", ["AccessDenied", "SignatureDoesNotMatch", "InvalidAccessKeyId"])
def test_a_forbidden_is_never_retried(code, tmp_path):
    store = Store(response(403, code))
    client, clock = make(store)
    with pytest.raises(StoreForbidden):
        client.get(REFERENCE, tmp_path / "out")
    assert (store.calls, clock.sleeps) == (1, [])


FAULTS = [
    response(500, "InternalError"),
    response(503, "SlowDown"),
    response(429, "TooManyRequests"),
    response(400, "BadDigest"),
    response(404, "NoSuchBucket"),
    ConnectTimeoutError(endpoint_url="http://store"),
    EndpointConnectionError(endpoint_url="http://store"),
    ConnectionResetError("reset"),
    TimeoutError("timed out"),
]


@pytest.mark.parametrize("fault", FAULTS, ids=lambda f: type(f).__name__)
def test_every_other_store_fault_is_retried_with_a_doubling_delay(fault, tmp_path):
    store = Store(fault)
    client, clock = make(store)
    with pytest.raises(StoreUnavailable):
        client.get(REFERENCE, tmp_path / "out")
    assert store.calls == 4
    assert clock.sleeps == [0.5, 1.0, 2.0]


def test_a_store_that_recovers_inside_the_tries_is_a_success(tmp_path):
    store = Store(response(500, "InternalError"), ConnectionResetError(), BYTES)
    client, clock = make(store)
    client.get(REFERENCE, tmp_path / "out")
    assert (tmp_path / "out").read_bytes() == BYTES
    assert clock.sleeps == [0.5, 1.0]


@pytest.mark.parametrize("deadline", [0.1, 0.5, 0.75, 1.0, 1.4, 2.5, 10])
def test_total_sleep_never_goes_beyond_the_deadline(deadline, tmp_path):
    store = Store(response(500, "InternalError"))
    client, clock = make(store, jitter=1.0)
    with pytest.raises(StoreUnavailable):
        client.get(REFERENCE, tmp_path / "out", deadline=deadline)
    assert sum(clock.sleeps) <= deadline
    assert clock.now <= deadline


def test_jitter_adds_to_the_delay_and_still_respects_the_deadline(tmp_path):
    store = Store(response(500, "InternalError"))
    clock = Clock()
    retry = RetryPolicy(clock=clock, sleep=clock.sleep, random=lambda: 1.0, jitter=0.5)
    client = ArtifactClient("http://127.0.0.1:1", Creds("a", "b"), retry)
    client._s3 = store
    with pytest.raises(StoreUnavailable):
        client.get(REFERENCE, tmp_path / "out", deadline=2.0)
    assert clock.sleeps == [0.75, 1.25]


def test_a_head_of_a_missing_object_is_not_found_and_not_retried():
    store = Store(response(404, "404", "HeadObject"))
    client, clock = make(store)
    with pytest.raises(ArtifactNotFound):
        client.head(REFERENCE)
    assert (store.calls, clock.sleeps) == (1, [])


def test_a_put_that_the_full_store_keeps_answering_with_500_is_an_upload_failure():
    store = Store(response(500, "InternalError", "PutObject"))
    client, clock = make(store)
    with pytest.raises(OutputUploadFailed):
        client.put_attempt(BYTES, "result", "r.xml", PRODUCER, "acme", "b1", 1)
    assert store.calls == 4
    assert clock.sleeps == [0.5, 1.0, 2.0]


def test_a_put_to_a_store_that_cannot_be_reached_is_store_unavailable():
    store = Store(ConnectTimeoutError(endpoint_url="http://store"))
    client, _ = make(store)
    with pytest.raises(StoreUnavailable):
        client.put_attempt(BYTES, "result", "r.xml", PRODUCER, "acme", "b1", 1)
    assert store.calls == 4


def test_a_put_that_finds_the_object_already_there_is_a_success():
    store = Store(response(412, "PreconditionFailed", "PutObject"))
    client, clock = make(store)
    reference = client.put_content(BYTES, "manifest", "n")
    assert reference.sha256 == SHA and reference.uri == REFERENCE["uri"]
    assert (store.calls, clock.sleeps) == (1, [])


def test_an_entity_tag_that_is_not_the_md5_of_the_bytes_is_retried():
    md5 = hashlib.md5(BYTES, usedforsecurity=False).hexdigest()
    store = Store({"ETag": '"' + "0" * 32 + '"'}, {"ETag": f'"{md5}"'})
    client, clock = make(store)
    client.put_content(BYTES, "manifest", "n")
    assert (store.calls, clock.sleeps) == (2, [0.5])


def test_an_entity_tag_that_is_not_an_md5_is_not_compared():
    store = Store({"ETag": '"not-an-md5"'})
    client, _ = make(store)
    client.put_content(BYTES, "manifest", "n")
    assert store.calls == 1


def test_a_source_above_the_cap_of_its_kind_is_refused_before_any_call():
    store = Store({})
    client, _ = make(store)
    with pytest.raises(ValueError, match="cap"):
        client.put_content(b"x" * (1024 * 1024 + 1), "manifest", "n")
    assert store.calls == 0


def test_verify_command_reports_a_store_that_is_down_in_under_a_minute(tmp_path):
    credentials, path = tmp_path / "credentials.json", tmp_path / "reference.json"
    dump_credentials(credentials, {"aggregate": Creds("a", "b")})
    path.write_text(json.dumps(REFERENCE))
    started = time.monotonic()
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "wdf_artifactclient",
            "verify",
            "--endpoint",
            "http://127.0.0.1:1",
            "--credentials",
            str(credentials),
            "--role",
            "aggregate",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 1
    assert "code: STORE_UNAVAILABLE" in done.stderr.splitlines()
    assert time.monotonic() - started < 60
