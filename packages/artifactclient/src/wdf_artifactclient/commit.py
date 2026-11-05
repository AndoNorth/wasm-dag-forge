"""Commit by reference: what the workflow's record activity calls for an accepted attempt."""

from dataclasses import dataclass
from typing import Any

from wdf_artifactindex import Revived, RowPurged, index_reference, link_use
from wdf_events.models import AttemptReference

from .client import as_reference, wall_now
from .errors import ArtifactNotFound, OutputUploadFailed

STUBBED = ()


class CommitRefused(ValueError):
    """An attempt-scoped reference that does not name the job, task and attempt being committed."""


@dataclass(frozen=True)
class Committed:
    """The index row of each committed uri, and the uris whose row was made live again."""

    row_ids: dict[str, int]
    revived: tuple[str, ...]


def commit(
    conn: Any,
    client: Any,
    references: list[Any],
    job_id: str,
    task: str,
    accepted_attempt: str | None = None,
) -> Committed:
    """Indexes the objects of an accepted attempt, once each exists in the store.

    An attempt-scoped reference is refused unless it names this job, task and accepted attempt,
    which is what keeps a zombie attempt's objects out of the index. Each row is locked, its object
    checked with a HEAD and only then made live, and a content-addressed one is linked to the job.
    A missing object raises OutputUploadFailed after every change is rolled back, so the workflow
    can retry the attempt. Rows are taken in uri order, so two commits never wait on each other.
    """
    refs = {ref.uri: ref for ref in map(as_reference, references)}
    for ref in refs.values():
        if isinstance(ref, AttemptReference):
            named = (ref.producer.job, ref.producer.task, ref.producer.attempt)
            if named != (job_id, task, accepted_attempt):
                raise CommitRefused(
                    f"{ref.uri} names {named}, the commit is of {(job_id, task, accepted_attempt)}"
                )
    row_ids: dict[str, int] = {}
    revived: list[str] = []
    with conn.transaction():
        for uri, ref in sorted(refs.items(), key=lambda item: item[0]):
            indexed = index_reference(conn, ref)
            row_ids[uri] = indexed.row_id
            if isinstance(indexed, Revived):
                revived.append(uri)
            _require_object(client, ref)
            if not isinstance(ref, AttemptReference):
                if isinstance(link_use(conn, job_id, uri, wall_now()), RowPurged):
                    raise OutputUploadFailed(f"{uri} was purged while it was committed")
    return Committed(row_ids, tuple(revived))


def _require_object(client: Any, ref: Any) -> None:
    try:
        stored = client.head(ref)
    except ArtifactNotFound as error:
        raise OutputUploadFailed(f"{ref.uri} is not in the store") from error
    if stored.size != ref.size:
        raise OutputUploadFailed(f"{ref.uri} holds {stored.size} bytes, expected {ref.size}")
