"""The determinism gate of JobWorkflow: every recorded history replays against the current
workflow, each holds the behaviour it was recorded for, and a changed workflow fails the replay."""

import base64
import json
from pathlib import Path

import pytest
from wdf_temporal.corpus import CorpusError, histories, replay_corpus
from wdf_workflow.contract import naming
from wdf_workflow.job import JobWorkflow

CORPUS = Path(__file__).resolve().parents[2] / "corpus"
# The status each history's job ended with, the signals it took and the attempts it ran on a pool:
# a retry and a resume run their task twice, and a cache hit runs no compile.
RECORDED = {
    "v1/plain": ("succeeded", [], 4),
    "v1/retry": ("succeeded", [], 2),
    "v1/cache-hit": ("succeeded", [], 3),
    "v1/partial-aggregate": ("partial", [], 4),
    "v1/cancel": ("cancelled", ["cancel"], 1),
    "v1/pause-resume": ("succeeded", ["resume"], 2),
}


@pytest.fixture(autouse=True)
def build(monkeypatch):
    """A replay runs outside a worker, which takes its control queue from the build id."""
    monkeypatch.setenv(naming().workflow.build_id_env, "replay")


def events(name: str) -> list[dict]:
    return json.loads((CORPUS / f"{name}.json").read_text())["history"]["events"]


def result(name: str) -> str:
    [end] = [e for e in events(name) if e["eventType"] == "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED"]
    [payload] = end["workflowExecutionCompletedEventAttributes"]["result"]["payloads"]
    return json.loads(base64.b64decode(payload["data"]))


def signals(name: str) -> list[str]:
    return [
        e["workflowExecutionSignaledEventAttributes"]["signalName"]
        for e in events(name)
        if e["eventType"] == "EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED"
    ]


def attempts(name: str) -> int:
    return sum(
        e["activityTaskScheduledEventAttributes"]["activityType"]["name"] == "run_task"
        for e in events(name)
        if e["eventType"] == "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED"
    )


def test_the_workflow_corpus_replays_against_the_current_workflow():
    assert [f"{p.parent.name}/{p.stem}" for p in histories(CORPUS)] == sorted(RECORDED)
    assert replay_corpus([JobWorkflow], CORPUS) == len(RECORDED)


@pytest.mark.parametrize("name", RECORDED)
def test_each_history_holds_the_behaviour_it_was_recorded_for(name):
    document = json.loads((CORPUS / f"{name}.json").read_text())
    assert document["build"]
    assert document["workflow_type"] == naming().workflow.type
    assert (result(name), signals(name), attempts(name)) == RECORDED[name]


def test_replay_detects_reordered_activity(monkeypatch):
    # The sandbox of the replay runner imports a workflow's module by name, so the changed build is
    # a module on the path and not a class of this test module.
    monkeypatch.syspath_prepend(Path(__file__).parent)
    from reordered import Reordered, Unchanged

    assert replay_corpus([Unchanged], CORPUS) == len(RECORDED)
    with pytest.raises(CorpusError) as caught:
        replay_corpus([Reordered], CORPUS)
    failures = str(caught.value).splitlines()
    assert sorted(line.split(" ")[0] for line in failures) == sorted(
        f"{name}.json" for name in RECORDED
    )
    assert all("Nondeterminism" in line for line in failures)
