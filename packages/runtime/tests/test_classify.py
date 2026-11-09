"""Each row of the simulate exit classes, fed its trigger as wasmtime 48 reports it."""

import os

import pytest
import yaml
from wdf_runtime.classify import Facts, classify, read_error_file
from wdf_runtime.models import CONTRACT

TRAP = """\
Error: failed to run main module `component.wasm`

Caused by:
    0: failed to invoke `run` function
    1: error while executing at wasm backtrace:
    0: 0xabf474 - libc.so!abort
   12: 0x1172194 - <unknown>!wasi:cli/run@0.2.9#run
    2: wasm trap: wasm `unreachable` instruction executed
"""
RAISED = (
    """\
Traceback (most recent call last):
  File "/0/app.py", line 18, in run
RuntimeError: boom

thread '<unnamed>' (1) panicked at runtime/src/lib.rs:1293:29:
Python function threw an unexpected exception
"""
    + TRAP
)
GROWN = (
    """\
Traceback (most recent call last):
  File "/0/app.py", line 22, in run
object type name: MemoryError
object repr     : MemoryError()
lost sys.stderr
"""
    + TRAP
)
INTERRUPTED = """\
Error: failed to run main module `component.wasm`

Caused by:
    0: failed to invoke `run` function
    1: error while executing at wasm backtrace:
    0: 0xd7b5fb - libpython3.14.so!_PyEval_EvalFrameDefault
    2: wasm trap: interrupt
"""
BELOW_DECLARED = """\
Error: failed to run main module `component.wasm`

Caused by:
    memory minimum size of 199 pages exceeds memory limits
"""
GROW_TRAPPED = TRAP.replace(
    "wasm trap: wasm `unreachable` instruction executed",
    "forcing trap when growing memory to 67633152 bytes",
)
MISSING = """\
Error: failed to open wasm module "/scratch/component.wasm"

Caused by:
    No such file or directory (os error 2)
"""
ERROR = {"code": "unknown-workload", "message": "no workload nope", "deterministic": True}
FLAKY = {"code": "store-busy", "message": "try again", "deterministic": False}

# class -> (exit code, stderr tail, error file, facts, expected reason)
CASES = {
    "ok": [(0, "HEARTBEAT {}\n", None, Facts(results_bytes=40), "ok")],
    "timeout": [(134, INTERRUPTED, None, Facts(), "timeout: 2: wasm trap: interrupt")],
    "guest_memory_cap": [
        (134, GROWN, None, Facts(), "guest_memory_cap: object type name: MemoryError"),
        (1, GROW_TRAPPED, None, Facts(), "guest_memory_cap: forcing trap when growing memory"),
        (1, BELOW_DECLARED, None, Facts(), "guest_memory_cap: exceeds memory limits"),
        (None, "", None, Facts(cap_refused="140 MiB"), "guest_memory_cap: 140 MiB"),
    ],
    "guest_failed": [
        (1, "", None, Facts(), "guest_failed: exit 1: no output"),
        (134, RAISED, None, Facts(), "guest_failed: exit 134: 2: wasm trap: wasm `unreachable`"),
        (1, "", ERROR, Facts(), "guest_failed: unknown-workload: no workload nope"),
        (1, "", FLAKY, Facts(), "guest_failed: store-busy: try again"),
        (0, "", None, Facts(results_bytes=67108865), "guest_failed: output-over-cap: 67108865"),
        (0, "", None, Facts(input_modified=True), "guest_failed: input-modified: /in changed"),
        (1, "Error: not wasmtime's\n", None, Facts(), "guest_failed: exit 1: Error: not"),
    ],
    "host_error": [
        (1, MISSING, None, Facts(), "host_error: exit 1: Error: failed to open wasm module"),
        (137, "", None, Facts(), "host_error: exit 137: no output"),
        (-9, "", None, Facts(), "host_error: exit -9: no output"),
        (None, "", None, Facts(host_error="wasmtime not found"), "host_error: wasmtime not found"),
    ],
    "oom_killed": [
        (137, "", None, Facts(oom_event=True), "oom_killed: exit 137 with a node OOM event"),
    ],
}
ROWS = yaml.safe_load((CONTRACT / "exit-classes.yaml").read_text())["pools"]["simulate"]["classes"]
CASE_LIST = [(cls, *case) for cls, cases in CASES.items() for case in cases]


def test_every_class_row_has_a_case():
    assert [row["class"] for row in ROWS] == list(CASES)
    for row in ROWS:
        for exit_code, tail, error, facts, reason in CASES[row["class"]]:
            got = classify(exit_code, tail, error, facts)
            print(f"{row['class']:<17} {got.deterministic!s:<5} {got.reason}")
            assert got.cls == row["class"]
            assert got.reason.startswith(reason)
            if row["deterministic"] == "from-error-file" and error is not None:
                assert got.deterministic is error["deterministic"]
            elif row["deterministic"] != "from-error-file":
                assert got.deterministic is row["deterministic"]


@pytest.mark.parametrize("cls,exit_code,tail,error,facts,reason", CASE_LIST)
def test_a_reason_is_one_line_led_by_its_class(cls, exit_code, tail, error, facts, reason):
    got = classify(exit_code, tail, error, facts)
    assert got.reason.split(":")[0] == cls
    assert "\n" not in got.reason and len(got.reason) <= 256


def test_guest_failed_without_an_error_file_is_not_deterministic():
    assert classify(1, "", None, Facts()).deterministic is False
    assert classify(134, RAISED, None, Facts()).deterministic is False


def test_error_file_reader(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    assert read_error_file(tmp_path) is None
    (out / "error.json").write_text('{"code":"bad-arg","message":"m","deterministic":true}')
    assert read_error_file(tmp_path) == {"code": "bad-arg", "message": "m", "deterministic": True}
    for broken in ('{"code":"Bad Code","message":"m","deterministic":true}', "not json"):
        (out / "error.json").write_text(broken)
        assert read_error_file(tmp_path) is None
    (out / "error.json").write_text(
        '{"code":"big","message":"' + "m" * 4096 + '","deterministic":true}'
    )
    assert read_error_file(tmp_path) is None
    (tmp_path / "elsewhere.json").write_text('{"code":"x","message":"m","deterministic":true}')
    (out / "error.json").unlink()
    os.symlink(tmp_path / "elsewhere.json", out / "error.json")
    assert read_error_file(tmp_path) is None
