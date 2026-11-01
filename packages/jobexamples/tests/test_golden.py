import subprocess
import sys

from wdf_jobexamples import golden

PLANS = golden.PLANS
CODE = (
    "from wdf_jobexamples import golden; print(golden.hashes_text(golden.golden_files()), end='')"
)


def test_golden_files_match_a_fresh_compile():
    fresh = golden.golden_files()
    assert sorted(p.name for p in PLANS.glob("*.json")) == sorted(fresh)
    for name, data in fresh.items():
        assert (PLANS / name).read_bytes() == data, name


def test_golden_hashes_line_up_with_the_files():
    assert golden.disagreements() == []


def test_golden_hashes_file_is_what_a_compile_writes():
    assert golden.HASHES.read_text() == golden.hashes_text(golden.golden_files())


def test_every_example_has_a_manifest_and_a_bound_plan():
    names = golden.examples()
    assert len(names) == 7
    for name in names:
        assert (PLANS / f"{name}.manifest.json").is_file()
        assert (PLANS / f"{name}.seed-7.plan.json").is_file()


def test_the_gate_fails_on_a_changed_file_a_file_without_a_line_and_a_line_without_a_file(tmp_path):
    golden.write(tmp_path)
    assert golden.disagreements(tmp_path) == []
    changed = tmp_path / "level0-echo.manifest.json"
    changed.write_bytes(changed.read_bytes() + b" ")
    (tmp_path / "extra.plan.json").write_bytes(b"{}")
    (tmp_path / "fan-out-8.manifest.json").unlink()
    assert golden.disagreements(tmp_path) == [
        "extra.plan.json has no HASHES line",
        "fan-out-8.manifest.json is listed in HASHES and missing",
        "level0-echo.manifest.json does not match its HASHES line",
    ]


def test_plan_hash_is_deterministic():
    runs = {
        subprocess.run(
            [sys.executable, "-I", "-c", CODE], capture_output=True, text=True, check=True
        ).stdout
        for _ in range(2)
    }
    assert runs == {golden.hashes_text(golden.golden_files())}
