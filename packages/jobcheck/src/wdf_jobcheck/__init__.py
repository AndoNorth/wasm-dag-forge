"""The validator: every pass over a document, in a fixed order, as one report."""

from dataclasses import dataclass

from wdf_jobdoc import Document, DocumentInvalid, Issue, parse_document
from wdf_jobdoc.issues import ordered

from .graph import check_graph
from .limits import check_limits
from .references import check_references
from .resolver import (
    BundleInfo,
    BundleNotFound,
    SourceResolver,
    StaticResolver,
    check_bundles,
    resolver_contract,
)
from .resources import check_resources, pool_budget

__all__ = [
    "pool_budget",
    "BundleInfo",
    "BundleNotFound",
    "Report",
    "SourceResolver",
    "StaticResolver",
    "resolver_contract",
    "validate",
]


@dataclass(frozen=True)
class Report:
    ok: bool
    issues: tuple[Issue, ...]
    document: Document | None


def validate(data: bytes, profile, resolver=None) -> Report:
    """Every pass over `data`, as one report sorted by path then code and cut at the issue cap.

    A document that fails the schema pass reports only that, because the later passes need a typed
    document.
    Graph, references, resources, the counting limits and (with a resolver) bundles each run on the
    typed document independently, so one call reports every kind of fault.
    The plan limits need a plan, so they run only when graph and references found nothing.
    Without a resolver the bundle and index checks are skipped.
    """
    try:
        document = parse_document(data, profile.max_document_bytes)
    except DocumentInvalid as exc:
        return Report(False, exc.issues, None)
    structure = check_graph(document) + check_references(document)
    issues = list(structure)
    issues += check_resources(document, profile)
    issues += check_limits(document, profile, with_plan=not structure)
    if resolver is not None:
        issues += check_bundles(document, resolver)
    found = ordered(issues)
    return Report(not found, found, document)
