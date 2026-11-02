"""The deploy and contract trees never hold a credential."""

import re
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TREES = (
    "deploy/appdb",
    "deploy/temporaldb",
    "deploy/objectstore",
    "scripts/storage",
    "contracts/state",
    "contracts/store",
)
# Negative vectors that name a credential key on purpose, to prove the contract check refuses it.
EXEMPT = ("contracts/store/vectors/invalid/secret-in-contract/",)
SECRET_KEY = re.compile(
    r"\b(secret[_-]?key|access[_-]?key|password|passwd|api[_-]?key|token)\b[\"']?\s*[:=]\s*\S",
    re.IGNORECASE,
)
ACCESS_KEY_ID = re.compile(r"\bAKIA[0-9A-Z]{16}\b")


def files(root: Path) -> list[str]:
    """Tracked files inside a work tree, and a walk of the directories anywhere else."""
    inside = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    if inside.returncode == 0 and Path(inside.stdout.strip()).resolve() == root.resolve():
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--", *TREES],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return [f for f in out.split("\0") if f]
    return [
        str(p.relative_to(root))
        for tree in TREES
        if (root / tree).is_dir()
        for p in (root / tree).rglob("*")
        if p.is_file()
    ]


def offences(root: Path) -> list[str]:
    found = []
    for name in files(root):
        if name.startswith(EXEMPT) or not (root / name).is_file():
            continue
        text = (root / name).read_text(errors="replace")
        if SECRET_KEY.search(text) or ACCESS_KEY_ID.search(text):
            found.append(name)
    return found


def test_no_credentials_in_tracked_files():
    assert offences(ROOT) == []


def test_a_secret_key_in_the_appdb_tree_is_found(tmp_path):
    (tmp_path / "deploy/appdb").mkdir(parents=True)
    (tmp_path / "deploy/appdb/values.yaml").write_text("secretKey: x\n")
    assert offences(tmp_path) == ["deploy/appdb/values.yaml"]


def test_an_access_key_id_is_found(tmp_path):
    (tmp_path / "scripts/storage").mkdir(parents=True)
    (tmp_path / "scripts/storage/env").write_text("K=AKIAABCDEFGHIJKLMNOP\n")
    assert offences(tmp_path) == ["scripts/storage/env"]


def test_empty_trees_pass(tmp_path):
    assert offences(tmp_path) == []


def test_it_passes_from_an_export_outside_any_repo(tmp_path):
    archive = tmp_path / "export.tar"
    subprocess.run(
        ["git", "-C", str(ROOT), "archive", "-o", str(archive), "HEAD"],
        check=True,
        capture_output=True,
    )
    export = tmp_path / "export"
    with tarfile.open(archive) as tar:
        tar.extractall(export, filter="data")
    assert offences(export) == []
