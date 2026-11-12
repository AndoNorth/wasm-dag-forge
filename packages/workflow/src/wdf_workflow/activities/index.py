"""The `build_index` control activity: the aggregate index of an aggregate task's inputs, put with
the `indexer` store role and committed under a login in `wdf_artifacts`."""

from temporalio import activity
from wdf_artifactclient.aggregate import put_index
from wdf_artifactclient.client import ArtifactClient
from wdf_artifactclient.contract import Refused
from wdf_artifactclient.models import IndexHeader
from wdf_events.models import ContentReference

from ..testing.control import BuildIndex, control_error
from .commit import commit_references
from .db import typed_errors


class IndexBuilder:
    """Puts indexes with the `indexer` client and checks them with the `workflow` client."""

    def __init__(self, dsn: str, store: ArtifactClient, indexer: ArtifactClient):
        self.dsn = dsn
        self.store = store
        self.indexer = indexer

    @activity.defn(name="build_index")
    def build_index(self, request: BuildIndex) -> ContentReference:
        """The committed index of the recorded results, whose entries are in task id order.

        A result of another job or task, an entry that did not succeed in an index that refuses
        partial results, or a task listed twice can never give an index the aggregate reads, so
        each is a non-retryable CommitRefused.

        The index is content-addressed, so a retry puts the same object and commits it again.
        """
        header = IndexHeader(
            schema="wdf.artifact.index/1",
            job=request.job_id,
            tenant=request.tenant,
            task=request.task,
            partial="allow" if request.partial else "refuse",
        )
        for entry in request.entries:
            producer = entry.reference and entry.reference.producer
            if producer and (producer.job, producer.task) != (request.job_id, entry.task):
                raise control_error(
                    "CommitRefused", f"{entry.task} reads {entry.reference.uri} of {producer}"
                )
            if entry.status != "succeeded" and not request.partial:
                raise control_error(
                    "CommitRefused", f"{entry.task} is {entry.status} and the index refuses"
                )
        entries = sorted(request.entries, key=lambda entry: entry.task)
        try:
            with typed_errors():
                reference = put_index(self.indexer, entries, header)
        except Refused as error:
            raise control_error("CommitRefused", str(error)) from error
        commit_references(self.dsn, self.store, [reference], request.job_id, request.task, None)
        return reference
