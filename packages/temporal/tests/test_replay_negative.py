"""The gate can fail: a history recorded before a field was renamed no longer replays."""

import base64
import json
import shutil

import pytest
from wdf_temporal.corpus import CORPUS, CorpusError, replay_corpus
from wdf_temporal.standin import StandInJob


def rename_accepted_field(path):
    """Rewrites the recorded accepted event as an older event model would have written it."""
    document = json.loads(path.read_text())
    started = document["history"]["events"][0]["workflowExecutionStartedEventAttributes"]
    payload = started["input"]["payloads"][0]
    event = json.loads(base64.b64decode(payload["data"]))
    event["data"]["submissionkey"] = event["data"].pop("submission_key")
    payload["data"] = base64.b64encode(
        json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
    ).decode()
    path.write_text(json.dumps(document))


def test_a_renamed_event_field_fails_the_replay_and_names_the_history(tmp_path):
    shutil.copytree(CORPUS / "v1", tmp_path / "v1")
    shutil.copy(CORPUS / "owners.yaml", tmp_path / "owners.yaml")
    rename_accepted_field(tmp_path / "v1/standin-plain.json")
    with pytest.raises(CorpusError) as caught:
        replay_corpus([StandInJob], tmp_path)
    message = str(caught.value)
    assert "v1/standin-plain.json" in message
    assert "v1/standin-unicode.json" not in message
