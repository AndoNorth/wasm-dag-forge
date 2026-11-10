"""Pool table v0 holds the ci slots, and its pools and queues are those of the registry."""

from pathlib import Path

import yaml
from wdf_workflow.contract import naming, pools, registry_pools

ROOT = Path(__file__).resolve().parents[3]
SLOTS = ROOT / "deploy/presets/slots/ci.yaml"
POOLS = ROOT / "contracts/temporal/pools.yaml"


def milli(cpu: str) -> int:
    return int(cpu[:-1]) if cpu.endswith("m") else int(cpu) * 1000


def mib(memory: str) -> int:
    return int(memory[:-2]) * 1024 if memory.endswith("Gi") else int(memory.removesuffix("Mi"))


def slot_mismatches(table: dict, slots: dict) -> list[str]:
    """Each pool whose slot in the table differs from the requests and limits of the preset."""
    wrong = []
    for name, slot in slots["pools"].items():
        assert slot["requests"] == slot["limits"], f"{name} is not one pod per slot"
        want = (milli(slot["limits"]["cpu"]), mib(slot["limits"]["memory"]))
        row = table["pools"].get(name, {})
        if (row.get("cpu_milli"), row.get("memory_mib")) != want:
            wrong.append(name)
    return wrong + sorted(set(table["pools"]) - set(slots["pools"]))


def test_pools_match_ci_slots():
    slots = yaml.safe_load(SLOTS.read_text())
    table = yaml.safe_load(POOLS.read_text())
    assert slot_mismatches(table, slots) == []
    table["pools"]["simulate"]["memory_mib"] = 512
    assert slot_mismatches(table, slots) == ["simulate"]


def test_pools_are_estimates_with_the_v0_timeouts():
    table = pools()
    assert table.estimate is True
    assert table.schedule_to_start_ceiling_s == 43_200
    for pool in table.pools.values():
        assert (pool.schedule_to_start_s, pool.heartbeat_s, pool.start_to_close_margin_s) == (
            1800,
            60,
            120,
        )


def test_pools_and_queues_are_those_of_the_registry():
    assert set(pools().pools) == set(naming().pools) == registry_pools()
    assert naming().pools == {
        "compile": "wdf-compile",
        "simulate": "wdf-simulate",
        "aggregate": "wdf-aggregate",
    }
