"""What the validator asks of the caller about content that lives outside the document.

A source bundle and an aggregate index object are content-addressed and stored elsewhere, so the
caller supplies a resolver and the validator never reads storage itself.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from wdf_jobdoc import Issue
from wdf_jobdoc.issues import pointer

__all__ = [
    "BundleInfo",
    "BundleNotFound",
    "ResolverContractError",
    "SourceResolver",
    "StaticResolver",
    "check_bundles",
    "resolver_contract",
]

# A reference that no resolver may hold: the contract probes it for the not-found behaviour.
ABSENT_SHA256 = "0" * 64


class BundleNotFound(LookupError):
    """The resolver holds no source bundle for the reference."""


@dataclass(frozen=True)
class BundleInfo:
    """The facts of a source bundle that the validator checks: its hash, lockfile and modules."""

    source_sha256: str
    lockfile_sha256: str | None
    modules: frozenset[str]


class SourceResolver(Protocol):
    """References are the `source` and `index` objects of a document, which carry a `sha256`."""

    def resolve_source(self, ref: Any) -> BundleInfo:
        """The bundle of a `source` reference, or raises BundleNotFound."""
        ...

    def exists(self, ref: Any) -> bool:
        """Whether the object of an `index` reference exists."""
        ...


class StaticResolver:
    """A resolver over a fixed table, shaped like contracts/job/vectors/resolver.json.

    `bundles` maps a SHA-256 to `lockfile_sha256` (null for none), `modules`, and optionally a
    `source_sha256` that differs from its key, which is how a table holds a wrong-hash bundle.
    `indexes` lists the SHA-256 of the index objects that exist.
    """

    def __init__(self, table: Mapping[str, Any]):
        self._bundles = dict(table.get("bundles", {}))
        self._indexes = frozenset(table.get("indexes", ()))

    def resolve_source(self, ref: Any) -> BundleInfo:
        entry = self._bundles.get(ref.sha256)
        if entry is None:
            raise BundleNotFound(ref.sha256)
        return BundleInfo(
            source_sha256=entry.get("source_sha256", ref.sha256),
            lockfile_sha256=entry.get("lockfile_sha256"),
            modules=frozenset(entry.get("modules", ())),
        )

    def exists(self, ref: Any) -> bool:
        return ref.sha256 in self._indexes


class ResolverContractError(AssertionError):
    """A resolver breaks the contract; the message lists every violation."""


@dataclass(frozen=True)
class _Probe:
    sha256: str


def resolver_contract(resolver: SourceResolver, present: Iterable[Any] = ()) -> None:
    """Raises ResolverContractError unless `resolver` behaves as the validator relies on.

    `present` holds source references that the resolver is known to hold.
    A reference it holds resolves to a bundle of the same hash, and one it lacks raises
    BundleNotFound from `resolve_source` and is not an existing index object.
    """
    violations = []
    probe = _Probe(ABSENT_SHA256)
    try:
        resolver.resolve_source(probe)
        violations.append("an absent reference resolved instead of raising BundleNotFound")
    except BundleNotFound:
        pass
    if resolver.exists(probe) is not False:
        violations.append("exists() is not False for an absent reference")
    for ref in present:
        try:
            info = resolver.resolve_source(ref)
        except BundleNotFound:
            violations.append(f"{ref.sha256} is known to be present and was not found")
            continue
        if info.source_sha256 != ref.sha256:
            violations.append(
                f"{ref.sha256} resolved to a bundle with the hash {info.source_sha256}"
            )
    if violations:
        raise ResolverContractError("; ".join(violations))


def check_bundles(document, resolver: SourceResolver) -> tuple[Issue, ...]:
    """The bundle and index object checks of a document, through the caller's resolver."""
    issues: list[Issue] = []
    for task_id, task in document.tasks.items():
        base = ["tasks", task_id, "params"]
        if task.type == "compile":
            issues += _bundle(resolver, task.params, base)
        elif task.type == "aggregate" and task.params.index is not None:
            if not resolver.exists(task.params.index):
                issues.append(
                    Issue(
                        "index-unresolved",
                        pointer(base + ["index"]),
                        f"no index object {task.params.index.sha256}",
                    )
                )
    return tuple(issues)


def _bundle(resolver: SourceResolver, params, base: list) -> list[Issue]:
    source = pointer(base + ["source"])
    try:
        info = resolver.resolve_source(params.source)
    except BundleNotFound:
        return [Issue("bundle-unresolved", source, f"no source bundle {params.source.sha256}")]
    if info.source_sha256 != params.source.sha256:
        return [
            Issue(
                "bundle-hash-mismatch",
                source,
                f"{params.source.sha256} resolved to a bundle with the hash {info.source_sha256}",
            )
        ]
    issues = []
    if not info.lockfile_sha256:
        issues.append(Issue("bundle-without-lockfile", source, "the bundle has no lockfile"))
    if params.entry not in info.modules:
        issues.append(
            Issue(
                "entry-not-in-bundle",
                pointer(base + ["entry"]),
                f"{params.entry} is not a module of the bundle",
            )
        )
    return issues
