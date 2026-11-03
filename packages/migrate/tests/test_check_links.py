import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def check(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(tree / "scripts/check-links.sh")], capture_output=True, text=True
    )


def export(tmp_path: Path) -> Path:
    tree = tmp_path / "export"
    (tree / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts/check-links.sh", tree / "scripts")
    (tree / "README.md").write_text("[ok](docs/a.md)\n")
    (tree / "docs").mkdir()
    (tree / "docs/a.md").write_text("[back](../README.md#top)\n")
    return tree


def test_a_tree_outside_any_repo_passes(tmp_path):
    assert check(export(tmp_path)).returncode == 0


def test_a_dead_link_outside_any_repo_is_found(tmp_path):
    tree = export(tmp_path)
    (tree / "docs/a.md").write_text("[gone](missing.md)\n")
    run = check(tree)
    assert run.returncode == 1
    assert "docs/a.md:1: dead link missing.md" in run.stdout
