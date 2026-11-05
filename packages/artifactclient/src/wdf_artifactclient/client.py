"""The store client of one role: streaming puts and gets that verify size and SHA-256.

The client is synchronous and thread safe, takes a `deadline` in seconds and a `progress` callback
that receives the number of bytes moved since its last call, and never heartbeats itself.
"""

import contextlib
import hashlib
import itertools
import os
import random
import re
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import BinaryIO

import yaml
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel, TypeAdapter
from wdf_events.models import ArtifactReference, AttemptReference, ContentReference, ProducerRef
from wdf_storecheck.s3 import Creds, error_code, status
from wdf_storecheck.s3 import client as s3_client

from .errors import (
    ArtifactCorrupt,
    ArtifactError,
    ArtifactLost,
    ArtifactNotFound,
    OutputUploadFailed,
    StoreForbidden,
    StoreUnavailable,
)
from .keys import CONTENT, attempt_key, bucket_of, content_key, kind_spec, split_uri
from .models import CONTRACT, Staging

STUBBED = ()

MIB = 1024 * 1024
# Parts of a multipart upload, and the read size of every stream. Both are estimates.
PART_BYTES = 8 * MIB
CHUNK_BYTES = MIB

Source = bytes | Path | BinaryIO
Progress = Callable[[int], None]

FORBIDDEN_CODES = {"AccessDenied", "SignatureDoesNotMatch", "InvalidAccessKeyId"}
MISSING_CODES = {"NoSuchKey", "NotFound", "404", ""}
FULL_STORE_STATUS = 500
# What a call to the store can raise: a response, a transport failure or a failed transfer.
STORE_ERRORS = (ClientError, BotoCoreError, ConnectionError, TimeoutError)
ETAG_MD5 = re.compile("^[0-9a-f]{32}$")


class TooLarge(ValueError):
    """A source above the size cap of its kind in the staging contract."""


class SourceChanged(ValueError):
    """A source whose bytes differ from the bytes that were hashed for its content address."""


@dataclass(frozen=True)
class RetryPolicy:
    """How a store fault is retried: `tries` calls, with a doubling delay and random jitter.

    Every number is an estimate. The clock, the sleep and the random source are fields so that a
    test can run the policy on a fake clock.
    """

    tries: int = 4
    base_delay: float = 0.5
    jitter: float = 0.5
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    random: Callable[[], float] = random.random

    def delay(self, failures: int) -> float:
        """The wait after the given number of failures, which is doubled by each one."""
        return self.base_delay * 2 ** (failures - 1) * (1 + self.jitter * self.random())


@dataclass(frozen=True)
class Head:
    size: int
    etag: str | None


class Deadline:
    """The time left to a call, from `seconds` after it started, or no limit."""

    def __init__(self, policy: RetryPolicy, seconds: float | None):
        self.clock = policy.clock
        self.seconds = seconds
        self.end = None if seconds is None else self.clock() + seconds

    def remaining(self) -> float:
        return float("inf") if self.end is None else self.end - self.clock()

    def check(self) -> None:
        if self.remaining() <= 0:
            raise StoreUnavailable(f"the deadline of {self.seconds} s passed during the transfer")


class Transient(Exception):
    """A fault found by the client itself that is worth another try, such as a bad entity tag."""


def classify(error: Exception) -> tuple[ArtifactError, int]:
    """The typed error of a store fault, with the HTTP status of the response, 0 for none."""
    if isinstance(error, ClientError):
        code, http = error_code(error), status(error)
        if http == 403 or code in FORBIDDEN_CODES:
            return StoreForbidden(f"{code or http}: the store refused the credential"), http
        if http == 404 and code in MISSING_CODES:
            return ArtifactNotFound("the store has no such object"), http
        return StoreUnavailable(f"the store answered {http} {code}"), http
    return StoreUnavailable(f"{type(error).__name__}: {error}"), 0


@cache
def kind_caps() -> dict[str, int]:
    return Staging.model_validate(yaml.safe_load((CONTRACT / "staging.yaml").read_text())).kind_caps


def as_reference(reference) -> ContentReference | AttemptReference:
    data = reference.model_dump() if isinstance(reference, BaseModel) else reference
    return TypeAdapter(ArtifactReference).validate_python(data)


class Prepared:
    """A source that can be read from its start as often as a retry needs.

    A stream is spooled to a scratch file, because it cannot be read again, while a path and bytes
    are read where they are.
    """

    def __init__(self, source: Source, cap: int, chunk_bytes: int, deadline: Deadline):
        self.cap, self.chunk_bytes = cap, chunk_bytes
        self.sha256: str | None = None
        self._bytes = source if isinstance(source, bytes) else None
        self._path = source if isinstance(source, Path) else None
        self._spool: BinaryIO | None = None
        try:
            if self._bytes is not None:
                self.size = len(self._bytes)
            elif self._path is not None:
                self.size = self._path.stat().st_size
            else:
                self._spool = tempfile.TemporaryFile()
                self.size = self._copy(source, deadline)
            if self.size > cap:
                raise TooLarge(f"{self.size} bytes are above the cap of {cap}")
        except BaseException:
            self.close()
            raise

    def _copy(self, stream: BinaryIO, deadline: Deadline) -> int:
        """Spools the stream, hashing as it goes, and stops reading once the cap is passed."""
        digest, total = hashlib.sha256(), 0
        while chunk := stream.read(self.chunk_bytes):
            deadline.check()
            digest.update(chunk)
            total += len(chunk)
            if total > self.cap:
                break
            self._spool.write(chunk)
        self.sha256 = digest.hexdigest()
        return total

    def chunks(self) -> Iterator[bytes | memoryview]:
        if self._bytes is not None:
            view = memoryview(self._bytes)
            for start in range(0, len(view), self.chunk_bytes):
                yield view[start : start + self.chunk_bytes]
            return
        with contextlib.ExitStack() as stack:
            if self._spool is not None:
                reader = self._spool
                reader.seek(0)
            else:
                reader = stack.enter_context(self._path.open("rb"))
            while chunk := reader.read(self.chunk_bytes):
                yield chunk

    def close(self) -> None:
        if self._spool is not None:
            self._spool.close()

    def hash_through(self, deadline: Deadline) -> None:
        """Takes the SHA-256 of a path or bytes source, which is not spooled, by one read."""
        if self.sha256 is None:
            digest = hashlib.sha256()
            for chunk in self.chunks():
                deadline.check()
                digest.update(chunk)
            self.sha256 = digest.hexdigest()


def parts_of(chunks: Iterator, part_bytes: int, feed: Callable[[bytes], None]) -> Iterator[bytes]:
    """Regroups chunks into parts of exactly `part_bytes`, and a shorter last one."""
    buffer = bytearray()
    for chunk in chunks:
        feed(chunk)
        buffer += chunk
        while len(buffer) >= part_bytes:
            yield bytes(buffer[:part_bytes])
            del buffer[:part_bytes]
    if buffer:
        yield bytes(buffer)


class Sink:
    """Where a download goes. A sink is begun again for each fetch of the same object."""

    def begin(self) -> None: ...

    def write(self, chunk: bytes) -> None: ...

    def finish(self) -> None: ...

    def abort(self) -> None: ...


class MemorySink(Sink):
    def begin(self) -> None:
        self.buffer = bytearray()

    def write(self, chunk: bytes) -> None:
        self.buffer += chunk


class FileSink(Sink):
    """A temporary file beside the destination that is renamed only once the object verified."""

    def __init__(self, dest: Path):
        self.dest, self.temp = dest, None

    def begin(self) -> None:
        self.abort()
        handle, name = tempfile.mkstemp(dir=self.dest.parent, prefix=f".{self.dest.name}.")
        self.temp, self.file = Path(name), os.fdopen(handle, "wb")

    def write(self, chunk: bytes) -> None:
        self.file.write(chunk)

    def finish(self) -> None:
        self.file.close()
        os.replace(self.temp, self.dest)
        self.temp = None

    def abort(self) -> None:
        if self.temp is not None:
            self.file.close()
            self.temp.unlink(missing_ok=True)
            self.temp = None


class ArtifactClient:
    """One role's credentials against the store, with the retry policy and the read size."""

    def __init__(
        self,
        endpoint: str,
        credentials: Creds,
        retry: RetryPolicy | None = None,
        chunk_bytes: int | None = None,
    ):
        self.retry = retry or RetryPolicy()
        self.chunk_bytes = chunk_bytes or CHUNK_BYTES
        self._s3 = s3_client(endpoint, credentials)

    def put_content(
        self,
        source: Source,
        kind: str,
        name: str,
        *,
        conn=None,
        job_id: str | None = None,
        deadline: float | None = None,
        progress: Progress | None = None,
    ) -> ContentReference:
        """Stores bytes under the key of their own hash. An object that exists is a success."""
        if conn is not None:
            if job_id is None:
                raise ValueError("job_id is required with conn")
            raise NotImplementedError("indexing through a connection is not delivered yet")
        if kind_spec(kind)["class"] != CONTENT:
            raise ValueError(f"{kind} is not content-addressed")
        limit = Deadline(self.retry, deadline)
        prepared = Prepared(source, kind_caps()[kind], self.chunk_bytes, limit)
        try:
            prepared.hash_through(limit)
            key = content_key(kind, prepared.sha256)
            sha256, size = self._upload(bucket_of(kind), key, prepared, prepared, limit, progress)
        finally:
            prepared.close()
        return ContentReference(
            uri=f"s3://{bucket_of(kind)}/{key}", sha256=sha256, size=size, name=name, kind=kind
        )

    def put_attempt(
        self,
        source: Source,
        kind: str,
        name: str,
        producer: ProducerRef,
        tenant: str,
        batch: str,
        seed: int | str | None = None,
        segment: str | None = None,
        deadline: float | None = None,
        progress: Progress | None = None,
    ) -> AttemptReference:
        """Stores bytes under the key of an attempt that the caller names."""
        key = attempt_key(kind, name, producer, tenant, batch, seed, segment)
        limit = Deadline(self.retry, deadline)
        prepared = Prepared(source, kind_caps()[kind], self.chunk_bytes, limit)
        try:
            sha256, size = self._upload(bucket_of(kind), key, prepared, None, limit, progress)
        finally:
            prepared.close()
        return AttemptReference(
            uri=f"s3://{bucket_of(kind)}/{key}",
            sha256=sha256,
            size=size,
            name=name,
            kind=kind,
            producer=producer,
        )

    def get(
        self,
        reference,
        dest: Path,
        *,
        deadline: float | None = None,
        progress: Progress | None = None,
    ) -> None:
        """Writes the object to `dest` only once its size and SHA-256 matched the reference."""
        self._read(reference, FileSink(Path(dest)), deadline, progress)

    def get_bytes(
        self,
        reference,
        max_bytes: int,
        *,
        deadline: float | None = None,
        progress: Progress | None = None,
    ) -> bytes:
        if as_reference(reference).size > max_bytes:
            raise TooLarge(f"the object is above the {max_bytes} bytes asked for")
        sink = MemorySink()
        self._read(reference, sink, deadline, progress)
        return bytes(sink.buffer)

    def head(
        self,
        reference,
        *,
        deadline: float | None = None,
        progress: Progress | None = None,
    ) -> Head:
        """The size that the store holds for the object, without reading it."""
        bucket, key = split_uri(as_reference(reference).uri)
        reply = self._retrying(
            lambda: self._s3.head_object(Bucket=bucket, Key=key), Deadline(self.retry, deadline)
        )
        return Head(reply["ContentLength"], reply.get("ETag", "").strip('"') or None)

    def _retrying(self, call: Callable, deadline: Deadline, *, put: bool = False):
        """Runs `call`, trying again after a store fault within the policy and the deadline.

        A missing object and a refused credential are never retried. After the last try, a put
        that the store answered with a 500 is a full store, which is its own failure.
        """
        failures = 0
        while True:
            try:
                return call()
            except (*STORE_ERRORS, Transient) as error:
                fault, http, cause = *classify(error), error
            if isinstance(fault, ArtifactNotFound | StoreForbidden):
                raise fault from cause
            failures += 1
            wait = min(self.retry.delay(failures), deadline.remaining())
            if failures >= self.retry.tries or wait <= 0:
                if put and http == FULL_STORE_STATUS:
                    raise OutputUploadFailed(str(fault)) from cause
                raise fault from cause
            self.retry.sleep(wait)
            if deadline.remaining() <= 0:
                raise fault from cause

    def _upload(
        self,
        bucket: str,
        key: str,
        prepared: Prepared,
        expected: Prepared | None,
        deadline: Deadline,
        progress: Progress | None,
    ) -> tuple[str, int]:
        """Sends the object and returns its SHA-256 and size as they were read."""
        guard = {"IfNoneMatch": "*"} if expected is not None else {}
        return self._retrying(
            lambda: self._upload_once(bucket, key, prepared, expected, guard, deadline, progress),
            deadline,
            put=True,
        )

    def _upload_once(self, bucket, key, prepared, expected, guard, deadline, progress):
        sha256, md5, total = hashlib.sha256(), hashlib.md5(usedforsecurity=False), 0

        def feed(chunk) -> None:
            nonlocal total
            deadline.check()
            sha256.update(chunk)
            md5.update(chunk)
            total += len(chunk)
            if total > prepared.cap:
                raise TooLarge(f"more than the cap of {prepared.cap} bytes were read")

        def settle() -> tuple[str, int]:
            if expected is not None and (sha256.hexdigest(), total) != (
                expected.sha256,
                expected.size,
            ):
                raise SourceChanged(f"the source changed while {key} was uploaded")
            return sha256.hexdigest(), total

        chunks = prepared.chunks()
        parts = parts_of(chunks, PART_BYTES, feed)
        try:
            first = next(parts, b"")
            second = next(parts, None)
            if second is None:
                result = settle()
                self._put_object(bucket, key, first, md5.hexdigest(), guard)
                self._moved(progress, len(first))
                return result
            return self._multipart(
                bucket, key, [first, second], parts, guard, settle, deadline, progress
            )
        finally:
            parts.close()
            chunks.close()

    def _put_object(self, bucket, key, body: bytes, md5: str, guard: dict) -> None:
        try:
            reply = self._s3.put_object(Bucket=bucket, Key=key, Body=body, **guard)
        except ClientError as error:
            if error_code(error) == "PreconditionFailed" or status(error) == 412:
                return
            raise
        etag = reply.get("ETag", "").strip('"')
        if ETAG_MD5.fullmatch(etag) and etag != md5:
            raise Transient(f"the entity tag {etag} is not the MD5 {md5} of the bytes sent")

    def _multipart(self, bucket, key, first, rest, guard, settle, deadline, progress):
        upload = self._s3.create_multipart_upload(Bucket=bucket, Key=key)["UploadId"]
        done = []
        try:
            for number, part in enumerate(itertools.chain(first, rest), start=1):
                reply = self._s3.upload_part(
                    Bucket=bucket, Key=key, UploadId=upload, PartNumber=number, Body=part
                )
                done.append({"ETag": reply["ETag"], "PartNumber": number})
                self._moved(progress, len(part))
                deadline.check()
            result = settle()
            self._s3.complete_multipart_upload(
                Bucket=bucket, Key=key, UploadId=upload, MultipartUpload={"Parts": done}, **guard
            )
            return result
        except BaseException as error:
            with contextlib.suppress(*STORE_ERRORS):
                self._s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload)
            if isinstance(error, ClientError) and (
                error_code(error) == "PreconditionFailed" or status(error) == 412
            ):
                return settle()
            raise

    @staticmethod
    def _moved(progress: Progress | None, count: int) -> None:
        if progress is not None and count:
            progress(count)

    def _read(
        self, reference, sink: Sink, deadline: float | None, progress: Progress | None
    ) -> None:
        """Fetches the object into the sink, and once more when it is missing or corrupt."""
        ref = as_reference(reference)
        limit = Deadline(self.retry, deadline)
        first: ArtifactError | None = None
        for fetch in (1, 2):
            try:
                self._retrying(lambda: self._fetch(ref, sink, limit, progress), limit)
                return
            except (ArtifactNotFound, ArtifactCorrupt) as fault:
                first = first or fault
                if fetch == 2:
                    raise ArtifactLost(
                        f"{ref.uri}: {first.code} after one more fetch", first
                    ) from first

    def _fetch(self, ref, sink: Sink, deadline: Deadline, progress: Progress | None) -> None:
        bucket, key = split_uri(ref.uri)
        digest, total = hashlib.sha256(), 0
        body = self._s3.get_object(Bucket=bucket, Key=key)["Body"]
        sink.begin()
        try:
            chunks = body.iter_chunks(self.chunk_bytes)
            while chunk := next(chunks, b""):
                deadline.check()
                total += len(chunk)
                if total > ref.size:
                    raise ArtifactCorrupt(
                        f"{ref.uri} holds more than the {ref.size} bytes expected"
                    )
                digest.update(chunk)
                sink.write(chunk)
                self._moved(progress, len(chunk))
            if total != ref.size:
                raise ArtifactCorrupt(f"{ref.uri} holds {total} bytes, expected {ref.size}")
            if digest.hexdigest() != ref.sha256:
                raise ArtifactCorrupt(f"{ref.uri} has the SHA-256 {digest.hexdigest()}")
            sink.finish()
        except BaseException:
            sink.abort()
            raise
        finally:
            body.close()


def verify(client: ArtifactClient, reference, deadline: float | None = None) -> None:
    """Reads the object through the hash check and keeps nothing. Raises the failure of the read."""
    client._read(reference, Sink(), deadline, None)
