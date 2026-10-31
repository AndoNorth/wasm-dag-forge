"""The compile cache key changes with each of its five inputs and rejects malformed inputs."""

import os
import subprocess
import sys

import pytest
import wdf_keys

SOURCE = "a" * 64
LOCKFILE = "b" * 64
ENTRY = "sim"
BASE = (SOURCE, LOCKFILE, "1.2.3", "0.1.0", ENTRY)


@pytest.mark.parametrize(
    "changed",
    [
        ("c" * 64, LOCKFILE, "1.2.3", "0.1.0", ENTRY),
        (SOURCE, "c" * 64, "1.2.3", "0.1.0", ENTRY),
        (SOURCE, LOCKFILE, "1.2.4", "0.1.0", ENTRY),
        (SOURCE, LOCKFILE, "1.2.3", "0.2.0", ENTRY),
        (SOURCE, LOCKFILE, "1.2.3", "0.1.0", "echo"),
    ],
    ids=["source_hash", "lockfile_hash", "componentize_py_version", "wit_version", "entry"],
)
def test_key_changes_when_any_one_input_changes(changed):
    assert wdf_keys.compile_cache_key(*changed) != wdf_keys.compile_cache_key(*BASE)


def test_compile_key_entry():
    sim = wdf_keys.compile_cache_key(SOURCE, LOCKFILE, "1.2.3", "0.1.0", "sim")
    echo = wdf_keys.compile_cache_key(SOURCE, LOCKFILE, "1.2.3", "0.1.0", "echo")
    assert sim != echo
    assert {wdf_keys.compile_cache_key(*BASE) for _ in range(3)} == {sim}


def test_version_boundary_is_part_of_the_key():
    assert wdf_keys.compile_cache_key(
        SOURCE, LOCKFILE, "1.2", "3", ENTRY
    ) != wdf_keys.compile_cache_key(SOURCE, LOCKFILE, "1", "2.3", ENTRY)


def test_the_boundary_between_wit_version_and_entry_is_part_of_the_key():
    assert wdf_keys.compile_cache_key(
        SOURCE, LOCKFILE, "1.2.3", "0.1.0", "sim"
    ) != wdf_keys.compile_cache_key(SOURCE, LOCKFILE, "1.2.3", "0.1.0s", "im")


def test_an_empty_entry_is_refused():
    with pytest.raises(ValueError, match="entry"):
        wdf_keys.compile_cache_key(SOURCE, LOCKFILE, "1.2.3", "0.1.0", "")


def test_equal_inputs_give_equal_keys_across_interpreters():
    program = f"import wdf_keys;print(wdf_keys.compile_cache_key{BASE!r})"
    outputs = {
        subprocess.run(
            [sys.executable, "-c", program],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for seed in ("0", "1")
    }
    assert outputs == {wdf_keys.compile_cache_key(*BASE)}


@pytest.mark.parametrize(
    "bad",
    ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, "sha256:" + "a" * 64, "a" * 64 + "\n"],
)
@pytest.mark.parametrize("position", [0, 1])
def test_malformed_hash_raises(bad, position):
    args = list(BASE)
    args[position] = bad
    with pytest.raises(ValueError):
        wdf_keys.compile_cache_key(*args)
