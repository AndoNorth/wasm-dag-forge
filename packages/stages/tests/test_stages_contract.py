"""The stage contract: its vectors through the loader, and the pinned table."""

import subprocess
from pathlib import Path

import pytest
import yaml
from wdf_stages.check import CATALOGUE, Invalid, check

ROOT = Path(__file__).resolve().parents[3]
TABLE = ROOT / "contracts/stages/stages.yaml"
CONTROL_MOVES = {"job.paused", "job.resumed", "job.cancelled", "job.terminated"}


def load(path: Path = TABLE) -> dict:
    return yaml.safe_load(path.read_text())["stages"]


def emitted(stages: dict, name: str) -> set[str]:
    return {e["type"] for e in stages[name]["emits"]}


def test_vectors_keep_their_result():
    vectors = ROOT / "scripts/verify/cluster/lib/vectors.sh"
    run = subprocess.run(
        [vectors, "contracts/stages", "contracts/stages/stages.yaml"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_pinned_table_loads_and_names_the_six_stages_and_their_sources():
    stages = load()
    check({"stages": stages})
    assert {n: s["source"] for n, s in stages.items()} == {
        "dispatch": "/wdf/dispatch",
        "control": "/wdf/control",
        "admission": "/wdf/admission",
        "workflow": "/wdf/workflow",
        "placement": "/wdf/placement",
        "worker": "/wdf/worker",
    }


def test_emitters_follow_the_stage_split():
    stages = load()
    assert emitted(stages, "dispatch") == {"job.accepted", "job.published"}
    assert (
        emitted(stages, "control")
        == {
            "job.pause.requested",
            "job.resume.requested",
            "job.cancel.requested",
            "job.terminate.requested",
        }
        | CONTROL_MOVES
    )
    assert emitted(stages, "admission") == {"job.admitted", "job.dead.lettered"}
    assert emitted(stages, "placement") == emitted(stages, "worker") == set()
    others = {
        "task.requested",
        "job.accepted",
        "job.published",
        "job.admitted",
        "job.dead.lettered",
        "job.pause.requested",
        "job.resume.requested",
        "job.cancel.requested",
        "job.terminate.requested",
    }
    assert emitted(stages, "workflow") == CATALOGUE - others
    assert stages["workflow"]["builds"] == ["task.requested"]
    assert stages["worker"]["writes"] == ["heartbeat-details"]


def test_the_cases_of_a_shared_type_are_named_in_a_when():
    stages = load()
    shared = emitted(stages, "control") & emitted(stages, "workflow")
    assert shared == CONTROL_MOVES
    for name in ("control", "workflow"):
        for event in stages[name]["emits"]:
            assert ("when" in event) == (event["type"] in shared), event


def test_emitter_sources_agree_with_the_status_projection():
    projection = yaml.safe_load((ROOT / "contracts/status/status.yaml").read_text())["projection"]
    stages = load()
    for type_, entries in projection.items():
        for entry in entries:
            if "when" in entry:
                owner = next(s for s in stages.values() if s["source"] == entry["when"]["source"])
                assert type_ in {e["type"] for e in owner["emits"]}


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda s: s["worker"]["reads"]["attributes"].append("nosuch"), "unknown-attribute"),
        (
            lambda s: s["placement"]["reads"]["data"].update({"task.requested": ["cpu"]}),
            "unknown-data-field",
        ),
        (lambda s: s["placement"]["reads"]["data"].update({"task.nosuch": ["x"]}), "unknown-type"),
        (lambda s: s["dispatch"]["emits"][0].update(source="/wdf/control"), "source-mismatch"),
        (lambda s: s["dispatch"]["emits"].pop(), "unemitted-type"),
        (lambda s: s["control"]["emits"][4].pop("when"), "duplicate-emitter"),
        (lambda s: s.pop("placement"), "schema-violation"),
    ],
)
def test_a_broken_table_names_its_reason(change, reason):
    stages = load()
    change(stages)
    with pytest.raises(Invalid) as raised:
        check({"stages": stages})
    assert raised.value.reason == reason
