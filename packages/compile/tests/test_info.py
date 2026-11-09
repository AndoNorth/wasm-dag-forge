"""bundle.info is what the dispatch resolver is built on, so a resolver over it meets the contract
the validator relies on, and `python -m wdf_compile info` prints the same facts."""

import json
from dataclasses import dataclass

from wdf_compile import bundle
from wdf_compile.__main__ import main
from wdf_jobcheck import BundleNotFound, resolver_contract


@dataclass(frozen=True)
class Ref:
    sha256: str


class BundleResolver:
    """A resolver over stored bundle files, keyed by the SHA-256 of their bytes."""

    def __init__(self, paths):
        self._paths = {bundle.info(p).source_sha256: p for p in paths}

    def resolve_source(self, ref):
        if ref.sha256 not in self._paths:
            raise BundleNotFound(ref.sha256)
        return bundle.info(self._paths[ref.sha256])

    def exists(self, ref) -> bool:
        return False


def write(root, text):
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "0"\n')
    (root / "uv.lock").write_text("version = 1\n")
    (root / "app.py").write_text(text)
    return bundle.write(root, root.parent / f"{root.name}-out")


def test_bundle_info_meets_resolver_contract(tmp_path):
    paths = [write(tmp_path / "a", "A = 1\n"), write(tmp_path / "b", "B = 2\n")]
    resolver = BundleResolver(paths)
    resolver_contract(resolver, present=[Ref(bundle.info(p).source_sha256) for p in paths])


def test_info_command_prints_the_bundle_facts(tmp_path, capsys):
    path = write(tmp_path / "a", "A = 1\n")
    assert main(["info", str(path)]) == 0
    printed = json.loads(capsys.readouterr().out)
    found = bundle.info(path)
    assert printed == {
        "lockfile_sha256": found.lockfile_sha256,
        "modules": ["app"],
        "source_sha256": found.source_sha256,
    }


def test_info_command_refuses_a_broken_bundle(tmp_path, capsys):
    path = tmp_path / "b.tar"
    path.write_bytes(b"not a tar")
    assert main(["info", str(path)]) == 3
    assert capsys.readouterr().err.startswith("reason: compile_failed: the bundle is not")
