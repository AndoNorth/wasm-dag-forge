"""The replay gate: every recorded history replays against the current code, and none is skipped."""

import json
import shutil

import pytest
from wdf_temporal.corpus import CORPUS, CorpusError, histories, replay_corpus
from wdf_temporal.standin import StandInJob


def test_the_whole_corpus_replays_against_the_current_workflow_and_decoding():
    assert len(histories(CORPUS)) >= 2
    assert replay_corpus([StandInJob]) == len(histories(CORPUS))


def copy_corpus(tmp_path, owners=True):
    shutil.copytree(CORPUS / "v1", tmp_path / "v1")
    if owners:
        shutil.copy(CORPUS / "owners.yaml", tmp_path / "owners.yaml")
    return tmp_path


def test_an_owned_history_with_no_class_fails_naming_the_history_and_its_owner(tmp_path):
    root = copy_corpus(tmp_path)
    with pytest.raises(CorpusError) as caught:
        replay_corpus([], root)
    message = str(caught.value)
    assert "v1/standin-plain.json" in message and "v1/standin-unicode.json" in message
    assert "owned by #4 (event envelope)" in message


def test_a_history_with_no_owner_and_no_class_fails_too(tmp_path):
    root = copy_corpus(tmp_path, owners=False)
    with pytest.raises(CorpusError, match=r"v1/standin-plain\.json: .*no owners\.yaml entry"):
        replay_corpus([], root)


def test_every_history_names_the_build_that_recorded_it():
    for path in histories(CORPUS):
        assert json.loads(path.read_text())["build"]
