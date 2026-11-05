"""Usage: python -m wdf_artifactclient.bench --results N [--twice] [--endpoint URL --credentials F]

Puts N small results, builds the aggregate index of them, reads the index back and verifies every
result through the client, then prints the counts, the SHA-256 of the index and the seconds of
each phase. With no endpoint it starts a SeaweedFS container of its own and removes it.
Exit 0 when every count is right, 1 when not, 2 on usage.
"""

import argparse
import hashlib
import sys
import tempfile
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from wdf_events.models import AttemptReference, ProducerRef
from wdf_storecheck.s3 import Creds, load_credentials

from .aggregate import open_results, put_index, read_index
from .client import ArtifactClient
from .keys import attempt_key, uri_of
from .models import IndexEntry

STUBBED = ()

TENANT, BATCH, NAME = "bench", "b1", "result.xml"
AGGREGATE_JOB = "00000000-0000-4000-8000-000000000001"
SIMULATE_JOB = "00000000-0000-4000-8000-000000000002"
RESULT_BYTES = 1024
# The thread count of the object store spike that measured 100,000 puts, and an in-flight bound.
THREADS = 16
WINDOW = 4 * THREADS


class ListCounter:
    """Stands in for the boto client of an ArtifactClient and counts the calls that list."""

    def __init__(self, client: ArtifactClient):
        self.calls = 0
        self._inner = client._s3
        client._s3 = self

    def __getattr__(self, name: str):
        if name.startswith("list_"):
            self.calls += 1
        return getattr(self._inner, name)


def task_id(i: int, width: int) -> str:
    """A task id whose text order is the numeric order, as the index needs."""
    return f"simulate-{i:0{width}d}"


def payload(i: int) -> bytes:
    return hashlib.shake_256(f"bench result {i}".encode()).digest(RESULT_BYTES)


def producer(i: int, width: int) -> ProducerRef:
    return ProducerRef(job=SIMULATE_JOB, task=task_id(i, width), attempt="r0a1")


def result_reference(i: int, width: int) -> AttemptReference:
    """The reference that a put of result i returns, known without the store."""
    who, data = producer(i, width), payload(i)
    key = attempt_key("result", NAME, who, TENANT, BATCH, i)
    return AttemptReference(
        uri=uri_of("result", key),
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        name=NAME,
        kind="result",
        producer=who,
    )


def ordered[T, R](
    pool: ThreadPoolExecutor, fn: Callable[[T], R], items: Iterable[T]
) -> Iterator[R]:
    """Maps in the pool with a bounded number in flight, yielding in the order of the items."""
    pending = deque()
    for item in items:
        pending.append(pool.submit(fn, item))
        if len(pending) >= WINDOW:
            yield pending.popleft().result()
    while pending:
        yield pending.popleft().result()


def entries(count: int, width: int) -> Iterator[IndexEntry]:
    for i in range(count):
        yield IndexEntry(
            task=task_id(i, width), status="succeeded", reference=result_reference(i, width)
        )


def measure(count: int, simulate, indexer, aggregate) -> dict[str, str]:
    """One run against the three clients, as the counts and times to print."""
    width = len(str(count - 1))
    counters = [ListCounter(client) for client in (simulate, indexer, aggregate)]

    def put(i: int) -> None:
        got = simulate.put_attempt(payload(i), "result", NAME, producer(i, width), TENANT, BATCH, i)
        if got != result_reference(i, width):
            raise RuntimeError(f"the reference of result {i} differs from the one expected")

    started = time.monotonic()
    with ThreadPoolExecutor(THREADS) as pool:
        put_count = sum(1 for _ in ordered(pool, put, range(count)))
    put_seconds = time.monotonic() - started

    started = time.monotonic()
    header = {"job": AGGREGATE_JOB, "tenant": TENANT, "task": "aggregate-0", "partial": "refuse"}
    reference = put_index(indexer, entries(count, width), header)
    index_seconds = time.monotonic() - started

    started = time.monotonic()
    read = verified = 0

    def counted() -> Iterator[IndexEntry]:
        nonlocal read
        for entry in read_index(aggregate, reference, TENANT):
            read += 1
            yield entry

    with tempfile.TemporaryDirectory() as scratch:
        for entry, path in open_results(aggregate, counted(), Path(scratch)):
            i = int(entry.task.rsplit("-", 1)[1])
            if path is None or path.read_bytes() != payload(i):
                raise RuntimeError(f"{entry.task} did not open to the bytes that were put")
            verified += 1
    verify_seconds = time.monotonic() - started

    return {
        "results_put": str(put_count),
        "results_verified": str(verified),
        "index_entries": str(read),
        "index_sha256": reference.sha256,
        "lists": str(sum(counter.calls for counter in counters)),
        "put_seconds": f"{put_seconds:.2f}",
        "index_seconds": f"{index_seconds:.2f}",
        "verify_seconds": f"{verify_seconds:.2f}",
    }


def run(endpoint: str, credentials: dict[str, Creds], count: int, twice: bool) -> int:
    runs = []
    for number in range(1, 3 if twice else 2):
        clients = [
            ArtifactClient(endpoint, credentials[role])
            for role in ("simulate", "indexer", "aggregate")
        ]
        report = measure(count, *clients)
        runs.append(report)
        if twice:
            print(f"run: {number}")
        for key, value in report.items():
            print(f"{key}: {value}")
        sys.stdout.flush()
    counts = ("results_put", "results_verified", "index_entries")
    wrong = [
        f"{key} is {report[key]}, not {count}"
        for report in runs
        for key in counts
        if report[key] != str(count)
    ]
    wrong += [f"lists is {report['lists']}" for report in runs if report["lists"] != "0"]
    if len({report["index_sha256"] for report in runs}) > 1:
        wrong.append("the two runs built indexes of different hashes")
    for line in wrong:
        print(f"mismatch: {line}", file=sys.stderr)
    return 1 if wrong else 0


def positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="wdf_artifactclient.bench", description=__doc__)
    parser.add_argument("--results", type=positive, required=True, help="the number of results")
    parser.add_argument("--twice", action="store_true", help="run twice and compare the hashes")
    parser.add_argument("--endpoint", help="a store to use, instead of a container of its own")
    parser.add_argument("--credentials", type=Path, help="the credentials file of that store")
    args = parser.parse_args(argv)
    if bool(args.endpoint) != bool(args.credentials):
        parser.error("--endpoint and --credentials go together")
    if args.endpoint:
        return run(args.endpoint, load_credentials(args.credentials), args.results, args.twice)
    # Imported here because only this path starts a container, and a wheel need not carry them.
    from wdf_itest import Session
    from wdf_storecheck.fixtures import running

    with (
        Session() as session,
        tempfile.TemporaryDirectory() as directory,
        running(session, "seaweedfs", Path(directory)) as started,
    ):
        started.store().create_buckets()
        return run(started.endpoint, started.credentials, args.results, args.twice)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
