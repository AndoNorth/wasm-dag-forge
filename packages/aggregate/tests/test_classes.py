"""Each input failure of a run exits with the code of its class and names it on the last line."""

import hashlib
import io
import json

import pytest
import wdf_aggregate.__main__ as cli
from wdf_artifactclient.aggregate import build_index
from wdf_artifactclient.errors import ArtifactCorrupt, ArtifactLost, StoreUnavailable
from wdf_events.models import AttemptReference, ContentReference, ProducerRef

JOB = "123e4567-e89b-42d3-a456-426614174000"
TENANT = "acme"


def result(task: str) -> AttemptReference:
    return AttemptReference(
        uri=f"s3://wdf-results/{TENANT}/b1/{JOB}/1/{task}/r0a1/results.xml",
        sha256=hashlib.sha256(task.encode()).hexdigest(),
        size=1024,
        name="results.xml",
        kind="result",
        producer=ProducerRef(job=JOB, task=task, attempt="r0a1"),
    )


def index(tenant: str = TENANT, partial: str = "refuse", status: str = "succeeded") -> bytes:
    entries = [
        {"task": "t1", "status": "succeeded", "reference": result("t1")},
        {
            "task": "t2",
            "status": status,
            "reference": result("t2") if status == "succeeded" else None,
        },
    ]
    header = {"job": JOB, "tenant": tenant, "task": "aggregate-0", "partial": partial}
    out = io.BytesIO()
    build_index(entries, header, out)
    return out.getvalue()


class Client:
    """Serves one index object and fails every result read with the given error."""

    def __init__(self, data: bytes, failure: Exception):
        self.data, self.failure = data, failure

    def get(self, reference, dest, **_):
        if reference.kind != "index":
            raise self.failure
        dest.write_bytes(self.data)


class Unreachable:
    def get(self, reference, dest, **_):
        raise StoreUnavailable("connection refused after 5 tries")


LOST = ArtifactLost("result of task t1: gone after one more fetch", ArtifactCorrupt("bad hash"))


@pytest.mark.parametrize(
    "client,cls,detail",
    [
        (Unreachable(), "aggregate_input_unavailable", "STORE_UNAVAILABLE"),
        (Client(index(), LOST), "aggregate_input_corrupt", "cause ARTIFACT_CORRUPT"),
        (Client(index(tenant="other"), LOST), "aggregate_input_refused", "foreign-tenant"),
        (Client(index(status="failed"), LOST), "aggregate_input_refused", "partial-refused"),
    ],
    ids=["store-unavailable", "artifact-lost", "foreign-tenant", "partial-refused"],
)
def test_input_failures_classified(monkeypatch, tmp_path, capsys, client, cls, detail):
    data = index()
    digest = hashlib.sha256(data).hexdigest()
    reference = ContentReference(
        uri=f"s3://wdf-sources/sha256/{digest[:2]}/{digest}",
        sha256=digest,
        size=len(data),
        name="index.jsonl",
        kind="index",
    )
    (tmp_path / "ref.json").write_text(reference.model_dump_json())
    (tmp_path / "creds.json").write_text(json.dumps({"aggregate": {}}))
    monkeypatch.setattr(cli, "ArtifactClient", lambda endpoint, credentials: client)
    monkeypatch.setattr(cli, "load_credentials", lambda path: {"aggregate": None})

    code = cli.main(
        [
            "run",
            "--index", str(tmp_path / "ref.json"),
            "--tenant", TENANT,
            "--aggregation", "summary_stats",
            "--args", "{}",
            "--out", str(tmp_path / "out"),
            "--endpoint", "http://127.0.0.1:1",
            "--credentials", str(tmp_path / "creds.json"),
            "--role", "aggregate",
        ]
    )  # fmt: skip

    last = capsys.readouterr().err.splitlines()[-1]
    assert code == cli.EXIT_CODES[cls]
    assert last.startswith(f"reason: {cls}: ") and detail in last, last
    assert not (tmp_path / "out" / "summary.json").exists()
