"""The pool table read from a slots file, and the refusal of a file that lacks a pool."""

import shutil

import pytest
import yaml
from wdf_jobdoc.profile import SlotsIncomplete, parse_cpu_milli, parse_memory_mib
from wdf_jobdoc.profile_files import load_profile


@pytest.fixture
def fixture_dir(contract):
    return contract / "vectors/slots-fixture"


def test_pool_table_from_slots(root, fixture_dir):
    pools = load_profile("ci", root, slots_dir=fixture_dir).pools
    assert (pools.compile.cpu_milli, pools.compile.memory_mib) == (1000, 1024)
    assert (pools.simulate.cpu_milli, pools.simulate.memory_mib) == (250, 384)
    assert (pools.aggregate.cpu_milli, pools.aggregate.memory_mib) == (1000, 1024)


def test_the_fixture_has_the_shape_of_the_live_ci_file(root, fixture_dir):
    live = yaml.safe_load((root / "deploy/presets/slots/ci.yaml").read_text())
    fixed = yaml.safe_load((fixture_dir / "ci.yaml").read_text())
    assert fixed.keys() == live.keys()
    assert fixed["pools"].keys() == live["pools"].keys()


@pytest.mark.parametrize("preset", ["ci", "dev", "soak"])
def test_live_slots_files_are_complete(root, preset):
    pools = load_profile(preset, root).pools
    assert {pools.slot(t).cpu_milli for t in ("compile", "simulate", "aggregate")} == {1000, 250}


def test_missing_pool_is_refused(tmp_path, fixture_dir, root):
    slots = yaml.safe_load((fixture_dir / "ci.yaml").read_text())
    del slots["pools"]["simulate"]
    (tmp_path / "ci.yaml").write_text(yaml.safe_dump(slots))
    with pytest.raises(SlotsIncomplete, match="simulate") as info:
        load_profile("ci", root, slots_dir=tmp_path)
    assert info.value.pool == "simulate"


@pytest.mark.parametrize("pool", ["compile", "simulate", "aggregate"])
def test_each_missing_pool_is_named(tmp_path, fixture_dir, root, pool):
    slots = yaml.safe_load((fixture_dir / "ci.yaml").read_text())
    del slots["pools"][pool]
    (tmp_path / "ci.yaml").write_text(yaml.safe_dump(slots))
    with pytest.raises(SlotsIncomplete) as info:
        load_profile("ci", root, slots_dir=tmp_path)
    assert info.value.pool == pool


def test_the_loader_holds_no_stand_in_budget(tmp_path, root):
    (tmp_path / "ci.yaml").write_text("pools: {}\n")
    with pytest.raises(SlotsIncomplete, match="compile"):
        load_profile("ci", root, slots_dir=tmp_path)


def test_a_file_with_no_pools_key_is_refused(tmp_path, root):
    (tmp_path / "ci.yaml").write_text("estimate: true\n")
    with pytest.raises(SlotsIncomplete):
        load_profile("ci", root, slots_dir=tmp_path)


@pytest.mark.parametrize(
    ("quantity", "milli"),
    [("1", 1000), ("2", 2000), ("250m", 250), ("1500m", 1500), ("0.5", 500), ("1.25", 1250)],
)
def test_cpu_quantities(quantity, milli):
    assert parse_cpu_milli(quantity) == milli


@pytest.mark.parametrize("quantity", ["", "1.5m", "1e3", "-1", "1 ", "0.0001", "m"])
def test_bad_cpu_quantities_are_refused(quantity):
    with pytest.raises(ValueError):
        parse_cpu_milli(quantity)


@pytest.mark.parametrize(("quantity", "mib"), [("1Gi", 1024), ("384Mi", 384), ("2Gi", 2048)])
def test_memory_quantities(quantity, mib):
    assert parse_memory_mib(quantity) == mib


@pytest.mark.parametrize("quantity", ["", "1G", "1.5Gi", "512", "1Ki", "-1Mi"])
def test_bad_memory_quantities_are_refused(quantity):
    with pytest.raises(ValueError):
        parse_memory_mib(quantity)


def test_the_slots_directory_is_not_the_live_one(tmp_path, root, fixture_dir):
    shutil.copy(fixture_dir / "ci.yaml", tmp_path / "ci.yaml")
    text = (tmp_path / "ci.yaml").read_text().replace("250m", "500m")
    (tmp_path / "ci.yaml").write_text(text)
    assert load_profile("ci", root, slots_dir=tmp_path).pools.simulate.cpu_milli == 500
    assert load_profile("ci", root).pools.simulate.cpu_milli == 250
