"""The `commit` and `index_checkpoint` control activities: the index rows of what an attempt wrote,
under a login in `wdf_artifacts`."""

from temporalio import activity
from wdf_artifactclient.client import ArtifactClient
from wdf_artifactclient.commit import commit
from wdf_artifactindex import index_reference

from ..testing.control import Commit, IndexCheckpoint, control_error
from .db import connect, typed_errors


def commit_references(
    dsn: str,
    store: ArtifactClient,
    references: list,
    job_id: str,
    task: str,
    attempt: str | None,
) -> None:
    """Indexes and links the references once each object is in the store, all or nothing."""
    with typed_errors(), connect(dsn, autocommit=True) as conn:
        commit(conn, store, references, job_id, task, attempt)


class Committer:
    """Commits with a store client of the `workflow` role, which only reads the objects it
    checks."""

    def __init__(self, dsn: str, store: ArtifactClient):
        self.dsn = dsn
        self.store = store

    @activity.defn(name="commit")
    def commit(self, request: Commit) -> None:
        """Commits the outputs of the accepted attempt and refuses an object of any other
        attempt, so a zombie attempt's outputs never reach the index. An object missing from the
        store is a retryable OUTPUT_UPLOAD_FAILED, after which the workflow runs the attempt
        again."""
        commit_references(
            self.dsn, self.store, request.references, request.job_id, request.task, request.attempt
        )

    @activity.defn(name="index_checkpoint")
    def index_checkpoint(self, request: IndexCheckpoint) -> None:
        """Indexes a checkpoint of the attempt without reading the store, whose `workflow` role
        has no access to the checkpoint bucket."""
        producer = request.reference.producer
        named = (producer.job, producer.task, producer.attempt)
        if named != (request.job_id, request.task, request.attempt):
            raise control_error(
                "CommitRefused",
                f"{request.reference.uri} names {named}, the attempt is "
                f"{(request.job_id, request.task, request.attempt)}",
            )
        with typed_errors(), connect(self.dsn, autocommit=True) as conn:
            index_reference(conn, request.reference)
