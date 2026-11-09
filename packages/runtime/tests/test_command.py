"""The wasmtime command line of a guest run: guest cap, deadline, mounts and module cache."""

import pytest
from wdf_runtime.command import MIB, PAGE, CapRefused, command, warm_up
from wdf_runtime.pins import GUEST_MIN_PAGES, GUEST_OVERHEAD_MIB


def test_guest_cap_is_the_memory_less_the_overhead(tmp_path):
    argv = command(tmp_path / "c.wasm", tmp_path / "s", 384, 2.5)
    assert argv == [
        "wasmtime",
        "run",
        "-W",
        f"max-memory-size={(384 - GUEST_OVERHEAD_MIB) * MIB}",
        "-W",
        "timeout=2500ms",
        "--dir",
        f"{tmp_path}/s/guest-input::/in",
        "--dir",
        f"{tmp_path}/s/output::/out",
        f"{tmp_path}/c.wasm",
    ]
    assert not any(arg.startswith("--env") or arg == "--inherit-env" for arg in argv)


def test_a_run_without_a_deadline_gets_one_that_never_fires(tmp_path):
    assert "timeout=31536000s" in command(tmp_path / "c.wasm", tmp_path, 384, None)


def test_the_smallest_cap_that_holds_the_declared_pages(tmp_path):
    smallest = GUEST_OVERHEAD_MIB + -(-GUEST_MIN_PAGES * PAGE // MIB)
    command(tmp_path / "c.wasm", tmp_path, smallest, 1)
    with pytest.raises(CapRefused, match=f"below the {GUEST_MIN_PAGES} pages"):
        command(tmp_path / "c.wasm", tmp_path, smallest - 1, 1)
    with pytest.raises(CapRefused, match="cap of 0 pages"):
        command(tmp_path / "c.wasm", tmp_path, 64, 1)


def test_cache_option_names_a_config_beside_the_cache(tmp_path):
    cache = tmp_path / "cache"
    argv = command(tmp_path / "c.wasm", tmp_path, 384, 1, cache)
    assert argv[6:10] == ["-C", "cache=y", "-C", f"cache-config={cache}/cache.toml"]
    assert (cache / "cache.toml").read_text() == f'[cache]\ndirectory = "{cache}/wasmtime"\n'


def test_warm_up_shares_the_task_flags_that_enter_the_cache_key(tmp_path):
    cache = tmp_path / "cache"
    task = command(tmp_path / "c.wasm", tmp_path / "s", 384, 30, cache)
    warm = warm_up(tmp_path / "c.wasm", tmp_path / "w", cache)
    assert [a for a in warm if a.startswith(("timeout=", "cache"))] == [
        "timeout=31536000s",
        "cache=y",
        f"cache-config={cache}/cache.toml",
    ]
    assert len(task) == len(warm) + 2
    assert (tmp_path / "w/guest-input/input.json").read_text() == (
        '{"args":{},"seed":0,"workload":"noop"}\n'
    )
