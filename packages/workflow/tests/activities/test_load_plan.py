"""The load_plan control activity against a real store, as a plain function with the `workflow`
store identity."""

import asyncio
import hashlib
import json

import pytest
from temporalio.exceptions import ApplicationError
from wdf_artifactclient.keys import split_uri
from wdf_events.models import JobParams, ManifestReference
from wdf_plan import read_plan
from wdf_workflow.activities import PlanLoader
from wdf_workflow.testing.control import ControlCase, LoadPlan, control_activity_contract

pytestmark = [pytest.mark.integration, pytest.mark.pair]

PLANS = "contracts/job/vectors/plans/1"
PARAMS = "contracts/job/vectors/job-params/valid"


@pytest.fixture
def loader(workflow_store):
    return PlanLoader(workflow_store)


@pytest.fixture
def golden(root):
    """The manifest bytes, the params and the bound plan bytes of an example."""

    def read(name: str) -> tuple[bytes, JobParams, bytes]:
        return (
            (root / PLANS / f"{name}.manifest.json").read_bytes(),
            JobParams.model_validate(
                {
                    "parameters": {},
                    **json.loads((root / PARAMS / f"{name}.seed-7.json").read_text()),
                }
            ),
            (root / PLANS / f"{name}.seed-7.plan.json").read_bytes(),
        )

    return read


@pytest.fixture
def put_manifest(submit):
    def put(body: bytes) -> ManifestReference:
        reference = submit.put_content(body, "manifest", "manifest.json")
        return ManifestReference.model_validate(reference.model_dump(mode="json"))

    return put


def refused(loader, request) -> ApplicationError:
    with pytest.raises(ApplicationError) as raised:
        loader.load_plan(request)
    return raised.value


@pytest.mark.parametrize("name", ["batch-template", "level1-simulate", "aggregate-partial"])
def test_load_plan_binds_the_manifest(loader, golden, put_manifest, name):
    manifest, params, bound = golden(name)
    loaded = loader.load_plan(LoadPlan(manifest=put_manifest(manifest), params=params))
    assert loaded.plan == read_plan(bound)
    assert loaded.max_history_events == 10_000


def test_manifest_hash_mismatch_refused(loader, golden, put_manifest, admin):
    manifest, params, _ = golden("batch-template")

    # Pretty printed, the same plan is not the canonical bytes its own reference hashes.
    loose = put_manifest(json.dumps(json.loads(manifest), indent=2).encode())
    error = refused(loader, LoadPlan(manifest=loose, params=params))
    assert (error.type, error.non_retryable) == ("ARTIFACT_CORRUPT", True)
    assert "canonical plan hashes to" in str(error)

    # The object under a reference holds other bytes than the reference hashes. The key is the
    # test's own, since the store and its content-addressed objects are shared by the module.
    digest = hashlib.sha256(b"poisoned " + manifest).hexdigest()
    poisoned = loose.model_copy(
        update={"uri": f"s3://wdf-sources/sha256/{digest[:2]}/{digest}", "sha256": digest}
    )
    bucket, key = split_uri(poisoned.uri)
    admin.put_object(Bucket=bucket, Key=key, Body=manifest)
    error = refused(loader, LoadPlan(manifest=poisoned, params=params))
    assert (error.type, error.non_retryable) == ("ARTIFACT_LOST", True)


def test_load_plan_refuses_what_can_never_load(loader, golden, put_manifest):
    manifest, params, _ = golden("batch-template")
    reference = put_manifest(manifest)
    unbound = refused(loader, LoadPlan(manifest=reference, params=JobParams(seed=7, parameters={})))
    assert (unbound.type, unbound.non_retryable) == ("ARTIFACT_CORRUPT", True)
    assert "do not bind" in str(unbound)

    text = put_manifest(b"not a plan")
    assert refused(loader, LoadPlan(manifest=text, params=params)).type == "ARTIFACT_CORRUPT"

    digest = hashlib.sha256(b"never stored").hexdigest()
    missing = reference.model_copy(
        update={"uri": f"s3://wdf-sources/sha256/{digest[:2]}/{digest}", "sha256": digest}
    )
    assert refused(loader, LoadPlan(manifest=missing, params=params)).type == "ARTIFACT_LOST"


def test_load_plan_passes_control_contract(loader, golden, put_manifest):
    manifest, params, _ = golden("batch-template")
    reference = put_manifest(manifest)
    other = reference.model_copy(update={"uri": "s3://wdf-sources/sha256/00/missing"})
    cases = [
        ControlCase("load_plan", LoadPlan(manifest=reference, params=params)),
        ControlCase("load_plan", LoadPlan(manifest=other, params=params), raises="ARTIFACT_LOST"),
    ]
    asyncio.run(control_activity_contract({"load_plan": loader.load_plan}, cases))
