"""FakeControl passes the control activity contract, and the contract refuses activities that
break it."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from wdf_artifactclient.models import IndexEntry
from wdf_events import from_bytes
from wdf_events.models import ContentReference, JobParams, ManifestReference
from wdf_plan import read_plan
from wdf_workflow.contract import naming
from wdf_workflow.models import FailureDetails, TaskResult
from wdf_workflow.testing.control import (
    SIGNATURES,
    BuildIndex,
    CacheInvalidate,
    CacheLookup,
    CacheRecord,
    Commit,
    ControlCase,
    Emit,
    Emitted,
    FakeControl,
    IndexCheckpoint,
    LoadedPlan,
    LoadPlan,
    Transition,
    control_activity_contract,
)

ROOT = Path(__file__).resolve().parents[3]
ACTIVITY = ROOT / "contracts/activity/vectors"
JOB = "3a9617fa-df7c-5760-a644-42d559e0f433"


def reference(kind: str, name: str, digit: str) -> dict:
    sha256 = digit * 64
    bucket = {"source": "sources", "manifest": "sources"}.get(kind, "components")
    return {
        "uri": f"s3://wdf-{bucket}/sha256/{sha256[:2]}/{sha256}",
        "sha256": sha256,
        "size": 10,
        "name": name,
        "kind": kind,
    }


MANIFEST = ManifestReference.model_validate(reference("manifest", "manifest.json", "1"))
SOURCE = ContentReference.model_validate(reference("source", "source.tar", "2"))
COMPONENT = ContentReference.model_validate(reference("component", "component.wasm", "3"))
OTHER = ContentReference.model_validate(reference("component", "component.wasm", "4"))
BUILD_INFO = ContentReference.model_validate(reference("build-info", "build-info.json", "5"))
RESULT = TaskResult.model_validate_json((ACTIVITY / "task-result/valid/simulate.json").read_text())
CHECKPOINT = FailureDetails.model_validate(
    json.loads((ACTIVITY / "failure-details/valid/operator-interrupt.json").read_text())["details"]
).checkpoint
STARTED = from_bytes((ROOT / "contracts/events/vectors/golden/job.started/1.0.json").read_bytes())
PLAN = LoadedPlan(
    plan=read_plan(
        (ROOT / "contracts/job/vectors/plans/1/level1-simulate.seed-7.plan.json").read_bytes()
    ),
    max_history_events=10_000,
)
KEY = hashlib.sha256(f"{SOURCE.sha256} main".encode()).hexdigest()


def other_attempt(model):
    producer = model.producer.model_copy(update={"attempt": "r0a2"})
    return model.model_copy(update={"producer": producer})


def cases() -> list[ControlCase]:
    commit = Commit(job_id=JOB, task="sim-0", attempt="r0a1", references=[*RESULT.outputs])
    checkpoint = IndexCheckpoint(reference=CHECKPOINT, job_id=JOB, task="sim-0", attempt="r0a1")
    started = Emit(
        job_id=STARTED.jobid,
        events=[STARTED],
        transition=Transition(expected="admitted", to="running"),
    )
    lookup = CacheLookup(source=SOURCE, entry="main", job_id=JOB)
    return [
        ControlCase(
            "load_plan", LoadPlan(manifest=MANIFEST, params=JobParams(seed=7, parameters={}))
        ),
        ControlCase(
            "load_plan",
            LoadPlan(
                manifest=MANIFEST.model_copy(update={"uri": "s3://wdf-sources/missing"}),
                params=JobParams(seed=7, parameters={}),
            ),
            raises="ARTIFACT_LOST",
        ),
        ControlCase("cache_lookup", lookup),
        ControlCase(
            "cache_record",
            CacheRecord(key=KEY, component=COMPONENT, build_info=BUILD_INFO, job_id=JOB),
        ),
        ControlCase("cache_lookup", lookup),
        ControlCase("cache_invalidate", CacheInvalidate(key=KEY, component=OTHER)),
        ControlCase("cache_invalidate", CacheInvalidate(key=KEY, component=COMPONENT)),
        ControlCase("commit", commit),
        ControlCase(
            "commit",
            commit.model_copy(update={"references": [other_attempt(RESULT.outputs[0])]}),
            raises="CommitRefused",
        ),
        ControlCase("index_checkpoint", checkpoint),
        ControlCase(
            "index_checkpoint",
            checkpoint.model_copy(update={"reference": other_attempt(CHECKPOINT)}),
            raises="CommitRefused",
        ),
        ControlCase(
            "build_index",
            BuildIndex(
                job_id=JOB,
                tenant="acme",
                task="agg",
                partial=False,
                entries=[
                    IndexEntry(task="sim-0", status="succeeded", reference=RESULT.outputs[0]),
                    IndexEntry(task="sim-1", status="missing"),
                ],
            ),
        ),
        ControlCase("emit", started),
        ControlCase("emit", started, lost="running"),
        ControlCase(
            "emit",
            started.model_copy(
                update={"transition": Transition(expected="succeeded", to="running")}
            ),
            raises="IllegalTransition",
        ),
    ]


def fake() -> FakeControl:
    return FakeControl(plans={MANIFEST.uri: PLAN})


def test_signatures_name_every_control_activity_but_place():
    activities = naming().activities
    names = {a.name for a in activities.control} | {a.name for a in activities.local}
    assert set(SIGNATURES) == names - {"place"}
    assert set(fake().activities()) == set(SIGNATURES)


def test_fake_control_passes_control_contract():
    control = fake()
    asyncio.run(control_activity_contract(control.activities(), cases()))
    assert control.cache == {}
    assert control.uses == [(JOB, COMPONENT.uri)]
    assert control.status == "running"
    assert list(control.events) == [STARTED.id]


def test_fake_control_scripted_errors_raise_in_order():
    control = FakeControl(errors={"commit": ["OUTPUT_UPLOAD_FAILED"]})
    commit = Commit(job_id=JOB, task="sim-0", attempt="r0a1", references=[*RESULT.outputs])
    with pytest.raises(ApplicationError) as raised:
        asyncio.run(control.commit(commit))
    assert (raised.value.type, raised.value.non_retryable) == ("OUTPUT_UPLOAD_FAILED", False)
    asyncio.run(control.commit(commit))
    assert control.committed[(JOB, "sim-0")] == RESULT.outputs


@activity.defn(name="commit")
async def plain_exception(request: Commit) -> None:
    raise ValueError("the database went away")


@activity.defn(name="commit")
async def untyped_error(request: Commit) -> None:
    raise ApplicationError("boom", type="Boom")


@activity.defn(name="commit")
async def retryable_refusal(request: Commit) -> None:
    raise ApplicationError("refused", type="CommitRefused")


@activity.defn(name="emit")
async def lost_but_written(request: Emit) -> Emitted:
    return Emitted(inserted=len(request.events), lost="running")


@activity.defn(name="commit")
async def wrong_argument(request: dict) -> None:
    return None


@pytest.mark.parametrize(
    ("name", "replacement", "message"),
    [
        ("commit", plain_exception, "raised a plain ValueError"),
        ("commit", untyped_error, "raised Boom"),
        ("commit", retryable_refusal, "CommitRefused has non_retryable False"),
        ("emit", lost_but_written, "a lost move wrote 1 events"),
        ("commit", wrong_argument, "not one Commit"),
    ],
)
def test_control_contract_refuses_activities_that_break_it(name, replacement, message):
    impl = {**fake().activities(), name: replacement}
    with pytest.raises(AssertionError, match=message):
        asyncio.run(control_activity_contract(impl, cases()))
