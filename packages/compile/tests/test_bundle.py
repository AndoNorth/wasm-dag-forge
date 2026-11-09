"""The bundle format: the writer gives equal bytes for equal trees, and the reader refuses what the
format forbids before anything is unpacked."""

import gzip
import hashlib
import io
import os
import tarfile

import pytest
from wdf_compile import bundle


def tree_of(root, files: dict[str, str]):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


FILES = {
    "pyproject.toml": '[project]\nname = "app"\nversion = "0"\n',
    "uv.lock": "version = 1\n",
    "app.py": "X = 1\n",
    "pkg/__init__.py": "",
    "pkg/sub/__init__.py": "",
    "pkg/sub/mod.py": "",
    "ns/plain.py": "",
    "not-a-package/x.py": "",
    "data/table.txt": "",
}


def raw_tar(path, *entries: tarfile.TarInfo):
    with tarfile.open(path, "w") as tar:
        for entry in entries:
            tar.addfile(entry, io.BytesIO(b"x" * entry.size) if entry.isfile() else None)
    return path


def entry(name, kind=tarfile.REGTYPE, size=1, linkname=""):
    info = tarfile.TarInfo(name)
    info.type, info.size, info.linkname = kind, size if kind == tarfile.REGTYPE else 0, linkname
    return info


def test_equal_trees_give_equal_bytes(tmp_path):
    first = bundle.write(tree_of(tmp_path / "a", FILES), tmp_path / "out-a")
    second_tree = tree_of(tmp_path / "b", dict(reversed(FILES.items())))
    (second_tree / "pkg/__pycache__").mkdir()
    (second_tree / "pkg/__pycache__/mod.cpython-313.pyc").write_bytes(b"cache")
    os.utime(second_tree / "app.py", (1, 1))
    second = bundle.write(second_tree, tmp_path / "out-b")
    assert first.name == "bundle.tar"
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first) as tar:
        members = tar.getmembers()
    assert [m.name for m in members] == sorted(m.name for m in members)
    assert {(m.mtime, m.uid, m.gid, m.uname, m.gname) for m in members} == {(0, 0, 0, "", "")}
    assert not any("__pycache__" in m.name for m in members)


def test_info_names_the_hashes_and_modules(tmp_path):
    path = bundle.write(tree_of(tmp_path / "t", FILES), tmp_path / "out")
    found = bundle.info(path)
    assert found.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert found.lockfile_sha256 == hashlib.sha256(b"version = 1\n").hexdigest()
    assert found.modules == {"app", "pkg", "pkg.sub", "pkg.sub.mod", "ns.plain"}


def test_a_bundle_without_a_lockfile_has_none(tmp_path):
    files = {k: v for k, v in FILES.items() if k != "uv.lock"}
    path = bundle.write(tree_of(tmp_path / "t", files), tmp_path / "out")
    assert bundle.info(path).lockfile_sha256 is None


def test_unpack_restores_the_tree(tmp_path):
    path = bundle.write(tree_of(tmp_path / "t", FILES), tmp_path / "out")
    bundle.unpack(path, tmp_path / "u")
    for name, text in FILES.items():
        assert (tmp_path / "u" / name).read_text() == text


def test_the_writer_refuses_a_link(tmp_path):
    tree = tree_of(tmp_path / "t", FILES)
    (tree / "link.py").symlink_to("app.py")
    with pytest.raises(bundle.Refused, match="not a regular file"):
        bundle.write(tree, tmp_path / "out")


REFUSED = {
    "parent-path": ([entry("../evil.py")], "leaves the bundle root"),
    "nested-parent-path": ([entry("pkg/../../evil.py")], "leaves the bundle root"),
    "absolute-path": ([entry("/etc/evil.py")], "leaves the bundle root"),
    "symlink": ([entry("link.py", tarfile.SYMTYPE, linkname="app.py")], "link or a device"),
    "hardlink": (
        [entry("app.py"), entry("hard.py", tarfile.LNKTYPE, linkname="app.py")],
        "link or a device",
    ),
    "device": ([entry("dev", tarfile.CHRTYPE)], "link or a device"),
    "fifo": ([entry("pipe", tarfile.FIFOTYPE)], "link or a device"),
}


@pytest.mark.parametrize("case", REFUSED)
def test_a_forbidden_entry_is_refused(tmp_path, case):
    entries, message = REFUSED[case]
    path = raw_tar(tmp_path / "b.tar", entry("pyproject.toml"), *entries)
    with pytest.raises(bundle.Refused, match=message):
        bundle.info(path)
    with pytest.raises(bundle.Refused, match=message):
        bundle.unpack(path, tmp_path / "u")
    assert not (tmp_path / "u").exists() or not any((tmp_path / "u").iterdir())


def test_a_compressed_archive_is_refused(tmp_path):
    plain = bundle.write(tree_of(tmp_path / "t", FILES), tmp_path / "out")
    zipped = tmp_path / "b.tar.gz"
    zipped.write_bytes(gzip.compress(plain.read_bytes()))
    with pytest.raises(bundle.Refused, match="not an uncompressed tar"):
        bundle.info(zipped)


def test_the_limits_are_refused(tmp_path, monkeypatch):
    path = bundle.write(tree_of(tmp_path / "t", FILES), tmp_path / "out")
    monkeypatch.setattr(bundle, "MAX_ENTRIES", 3)
    with pytest.raises(bundle.Refused, match="more than 3 entries"):
        bundle.info(path)
    monkeypatch.setattr(bundle, "MAX_ENTRIES", 10_000)
    monkeypatch.setattr(bundle, "MAX_UNPACKED_BYTES", 8)
    with pytest.raises(bundle.Refused, match="unpacks to more than 8 bytes"):
        bundle.unpack(path, tmp_path / "u")
    monkeypatch.setattr(bundle, "SOURCE_CAP", 512)
    with pytest.raises(bundle.Refused, match="above the source cap of 512"):
        bundle.info(path)


def test_the_source_cap_is_the_staging_cap():
    assert bundle.SOURCE_CAP == 64 * 1024 * 1024
