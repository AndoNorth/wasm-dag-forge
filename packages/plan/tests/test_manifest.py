import copy
import json
import subprocess
from pathlib import Path

import yaml
from pydantic import TypeAdapter
from wdf_events.models import ArtifactReference, TaskRequestData
from wdf_jobdoc.registry import TASK_TYPES
from wdf_plan import (
    canonical_bytes,
    manifest_reference,
    plan_hash,
    task_request_fields,
)

ARTIFACT_CHECK = Path(__file__).resolve().parents[3] / "contracts/artifact/check.sh"
LAYOUT = yaml.safe_load((ARTIFACT_CHECK.with_name("layout.yaml")).read_text())
DEADLINE = "2026-10-30T13:00:00.000000Z"
JOB = "11111111-1111-4111-8111-111111111111"


def test_manifest_reference_passes_the_artifact_loader(good, plan_of, tmp_path):
    path = tmp_path / "manifest.yaml"
    path.write_text(json.dumps(manifest_reference(plan_of(good))))
    result = subprocess.run([ARTIFACT_CHECK, str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_reference_matches_layout(good, plan_of):
    plan = plan_of(good)
    reference = manifest_reference(plan)
    kind = LAYOUT["kinds"]["manifest"]
    digest = plan_hash(plan)
    key = kind["key"].replace("<hash2>", digest[:2]).replace("<hash>", digest)
    assert reference["uri"] == f"s3://{kind['bucket']}/{key}"
    assert reference["kind"] == "manifest"
    assert reference["sha256"] == digest
    assert reference["size"] == len(canonical_bytes(plan))


def reference_for(task_type, output, producer):
    """A reference of the kind that `output` of a `task_type` task carries."""
    kind = TASK_TYPES[task_type]["outputs"][output]
    base = {"sha256": "b" * 64, "size": 1, "name": output}
    if kind in ("component", "build-info"):
        return base | {"uri": f"s3://wdf-components/sha256/bb/{'b' * 64}", "kind": kind}
    return base | {
        "uri": f"s3://wdf-results/t/b/{JOB}/1/{producer}/r0a1/{output}",
        "kind": kind,
        "producer": {"job": JOB, "task": producer, "attempt": "r0a1"},
    }


def request_for(plan, task):
    kinds = {t.id: t.type for t in plan.tasks}
    inputs = [reference_for(kinds[i.task], i.output, i.task) for i in task.inputs]
    if task.type == "compile":
        inputs.append(task.params.source.model_dump())
    return TaskRequestData(
        **task_request_fields(task),
        inputs=[TypeAdapter(ArtifactReference).validate_python(i) for i in inputs],
        deadline=DEADLINE,
    )


def test_task_request_fields_name_the_plan_values(good, plan_of):
    sim = next(t for t in plan_of(good).tasks if t.id == "sim")
    assert task_request_fields(sim) == {
        "task_type": "simulate",
        "cpu_cores": 0.25,
        "memory_mib": 384,
    }


def test_plan_task_maps_to_task_request(valid_documents, plan_of):
    for data in valid_documents.values():
        plan = plan_of(data)
        for task in plan.tasks:
            request = request_for(plan, task)
            assert request.task_type == task.type
            assert request.cpu_cores * 1000 == task.cpu_milli
            assert request.memory_mib == task.memory_mib


def test_template_plan_keeps_placeholders(good, plan_of):
    template = plan_of(good)
    sim = next(t for t in template.tasks if t.id == "sim")
    assert sim.params.seed.param == "seed"
    assert template.parameters == {"seed": "seed"}
    assert b'"seed":{"param":"seed"}' in canonical_bytes(template)
    # No job value reaches the compiler, so a literal seed is what would split the manifest.
    first, second = copy.deepcopy(good), copy.deepcopy(good)
    first["tasks"]["sim"]["params"]["seed"] = 1
    second["tasks"]["sim"]["params"]["seed"] = 2
    assert plan_hash(plan_of(first)) != plan_hash(plan_of(second))
    assert plan_hash(template) == plan_hash(plan_of(good))
