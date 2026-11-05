"""The fault hooks over an in-memory store, and the table of the scenario of each failure code."""

import ast
import hashlib
import io
import json
from pathlib import Path

import pytest
from wdf_artifactclient import faults
from wdf_artifactclient.contract import failure_codes
from wdf_artifactclient.keys import split_uri
from wdf_storecheck.s3 import Creds, dump_credentials

# Each code names the scenario of a test_faults_*isolated.py module that provokes it.
# One scenario can provoke two codes.
SCENARIOS = {
    "STORE_UNAVAILABLE": "test_a_stopped_store_gives_store_unavailable_within_the_deadline",
    "STORE_FORBIDDEN": "test_wrong_credentials_give_store_forbidden",
    "ARTIFACT_NOT_FOUND": "test_a_deleted_object_gives_not_found_then_lost",
    "ARTIFACT_CORRUPT": "test_a_flipped_byte_gives_corrupt_then_lost",
    "ARTIFACT_LOST": "test_a_flipped_byte_gives_corrupt_then_lost",
    "OUTPUT_UPLOAD_FAILED": "test_a_full_store_gives_output_upload_failed",
    "OUTPUT_MISSING": "test_a_missing_declared_output_gives_output_missing",
    "CHECKPOINT_CORRUPT": "test_a_corrupted_checkpoint_fails_and_the_previous_one_verifies",
}
SCENARIO_FILES = sorted(Path(__file__).parent.glob("test_faults_*isolated.py"))
BUCKET = "wdf-checkpoints"
FOLDER = "acme/b1/job/7/sim/r0a1/0/"
DATA = f"s3://{BUCKET}/{FOLDER}data.bin"
META = f"s3://{BUCKET}/{FOLDER}checkpoint.json"


class Memory:
    """The few calls of an S3 client that the hooks make, over a dict."""

    def __init__(self, objects: dict[str, bytes]):
        self.objects = {split_uri(uri)[1]: body for uri, body in objects.items()}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = Body

    def delete_object(self, Bucket, Key):
        del self.objects[Key]


def checkpoint_metadata() -> bytes:
    data = {
        "uri": DATA,
        "size": 4,
        "sha256": hashlib.sha256(b"data").hexdigest(),
        "kind": "checkpoint",
        "name": "data.bin",
        "producer": {
            "job": "123e4567-e89b-42d3-a456-426614174000",
            "task": "sim",
            "attempt": "r0a1",
        },
    }
    document = {
        "format_version": 1,
        "progress": None,
        "wasm_sha256": "0" * 64,
        "objects": [data],
    }
    return json.dumps(document).encode()


def test_every_code_of_the_taxonomy_has_a_scenario():
    assert set(SCENARIOS) == failure_codes()
    defined = {
        node.name
        for path in SCENARIO_FILES
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.FunctionDef)
    }
    assert set(SCENARIOS.values()) <= defined


def test_delete_removes_the_object():
    store = Memory({DATA: b"data"})
    assert faults.delete(store, DATA) == f"deleted {DATA}"
    assert store.objects == {}


def test_flip_byte_changes_exactly_one_byte_and_keeps_the_size():
    store = Memory({DATA: bytes(range(10))})
    message = faults.flip_byte(store, DATA)
    after = store.objects[split_uri(DATA)[1]]
    assert len(after) == 10
    assert [i for i, b in enumerate(after) if b != i] == [5]
    assert message == f"flipped byte 5 of {DATA} from 0x05 to 0xfa"


def test_flip_byte_refuses_an_empty_object():
    with pytest.raises(faults.FaultError):
        faults.flip_byte(Memory({DATA: b""}), DATA)


def test_corrupt_checkpoint_flips_the_data_object_and_leaves_the_metadata():
    store = Memory({META: checkpoint_metadata(), DATA: b"data"})
    faults.corrupt_checkpoint(store, META)
    assert store.objects[split_uri(DATA)[1]] != b"data"
    assert store.objects[split_uri(META)[1]] == checkpoint_metadata()


@pytest.mark.parametrize("uri", [DATA, f"s3://{BUCKET}/{FOLDER}"])
def test_corrupt_checkpoint_refuses_what_is_not_checkpoint_metadata(uri):
    with pytest.raises(faults.FaultError):
        faults.corrupt_checkpoint(Memory({DATA: b"data"}), uri)


@pytest.mark.parametrize(
    "extra",
    [["--role", "nobody", "--uri", DATA], ["--role", "simulate", "--uri", "not-a-uri"]],
)
def test_a_usage_error_exits_2_before_any_request(tmp_path, capsys, extra):
    path = tmp_path / "credentials.json"
    dump_credentials(path, {"simulate": Creds("a", "b")})
    code = faults.main(
        ["delete", "--endpoint", "http://127.0.0.1:9", "--credentials", str(path)] + extra
    )
    assert code == faults.USAGE
    assert capsys.readouterr().err.startswith("usage: ")
