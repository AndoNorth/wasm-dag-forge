"""The resolver interface, its contract function and the bundle pass, over resolver.json."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from wdf_jobcheck import BundleInfo, BundleNotFound, StaticResolver, resolver_contract
from wdf_jobcheck.resolver import ResolverContractError, check_bundles
from wdf_jobdoc import parse_document

VECTORS = Path(__file__).resolve().parents[3] / "contracts/job/vectors"
TABLE = json.loads((VECTORS / "resolver.json").read_text())
FILES = sorted((VECTORS / "invalid/bundle").glob("*/*.json"))


@pytest.fixture
def resolver():
    return StaticResolver(TABLE)


def source(char):
    return type("Ref", (), {"sha256": char * 64})()


def test_the_static_resolver_passes_the_contract(resolver):
    resolver_contract(resolver, present=[source("a"), source("b")])


def test_the_good_bundle_has_a_lockfile_and_the_entry_modules(resolver):
    info = resolver.resolve_source(source("a"))
    assert info == BundleInfo("a" * 64, "1" * 64, frozenset({"echo", "sim"}))


def test_a_resolver_that_returns_a_bundle_with_the_wrong_hash_fails_the_contract(resolver):
    class Wrong:
        exists = resolver.exists

        def resolve_source(self, ref):
            return replace(resolver.resolve_source(ref), source_sha256="d" * 64)

    with pytest.raises(ResolverContractError, match="resolved to a bundle with the hash"):
        resolver_contract(Wrong(), present=[source("a")])


def test_a_resolver_that_invents_bundles_fails_the_contract(resolver):
    class Invents:
        exists = resolver.exists

        def resolve_source(self, ref):
            return BundleInfo(ref.sha256, None, frozenset())

    with pytest.raises(ResolverContractError, match="absent reference resolved"):
        resolver_contract(Invents())


def test_a_resolver_that_misses_a_known_bundle_fails_the_contract(resolver):
    with pytest.raises(ResolverContractError, match="was not found"):
        resolver_contract(resolver, present=[source("e")])


def test_a_resolver_whose_exists_says_yes_to_everything_fails_the_contract(resolver):
    class Yes:
        resolve_source = resolver.resolve_source

        def exists(self, ref):
            return True

    with pytest.raises(ResolverContractError, match="exists"):
        resolver_contract(Yes())


def test_the_static_resolver_raises_for_an_unknown_hash(resolver):
    with pytest.raises(BundleNotFound):
        resolver.resolve_source(source("e"))


def test_a_valid_document_has_no_bundle_issue(resolver, parse, good):
    assert check_bundles(parse(good), resolver) == ()


def test_the_pointers_name_the_source_and_the_entry(resolver, parse, good):
    good["tasks"]["build"]["params"]["entry"] = "ghost"
    assert [(i.code, i.path) for i in check_bundles(parse(good), resolver)] == [
        ("entry-not-in-bundle", "/tasks/build/params/entry")
    ]


@pytest.mark.parametrize("path", FILES, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_vectors_first_issue_has_the_code_of_its_directory(path):
    document = parse_document(path.read_bytes(), 1 << 20)
    assert check_bundles(document, StaticResolver(TABLE))[0].code == path.parent.name


def test_a_bundle_with_another_hash_reports_no_further_issue(resolver, parse, good):
    good["tasks"]["build"]["params"]["source"]["sha256"] = "c" * 64
    good["tasks"]["build"]["params"]["source"]["uri"] = f"s3://wdf-sources/sha256/cc/{'c' * 64}"
    assert [i.code for i in check_bundles(parse(good), resolver)] == ["bundle-hash-mismatch"]
