"""The compile cache key changes with each of its four inputs and rejects malformed hashes."""

import os
import subprocess
import sys

import pytest
import wdf_keys

SOURCE = "a" * 64
LOCKFILE = "b" * 64
BASE = (SOURCE, LOCKFILE, "1.2.3", "0.1.0")


@pytest.mark.parametrize(
    "changed",
    [
        ("c" * 64, LOCKFILE, "1.2.3", "0.1.0"),
        (SOURCE, "c" * 64, "1.2.3", "0.1.0"),
        (SOURCE, LOCKFILE, "1.2.4", "0.1.0"),
        (SOURCE, LOCKFILE, "1.2.3", "0.2.0"),
    ],
    ids=["source_hash", "lockfile_hash", "componentize_py_version", "wit_version"],
)
def test_key_changes_when_any_one_input_changes(changed):
    assert wdf_keys.compile_cache_key(*changed) != wdf_keys.compile_cache_key(*BASE)


def test_version_boundary_is_part_of_the_key():
    assert wdf_keys.compile_cache_key(SOURCE, LOCKFILE, "1.2", "3") != wdf_keys.compile_cache_key(
        SOURCE, LOCKFILE, "1", "2.3"
    )


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
