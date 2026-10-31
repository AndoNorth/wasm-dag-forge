"""The validator: every pass over a document, in a fixed order, as one report."""

from dataclasses import dataclass

from wdf_jobdoc import Document, DocumentInvalid, Issue, parse_document

from .resolver import (
    BundleInfo,
    BundleNotFound,
    SourceResolver,
    StaticResolver,
    resolver_contract,
)
from .resources import pool_budget

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
    """The schema pass is real, and every later pass is still a stub."""
    try:
        parse_document(data, profile.max_document_bytes)
    except DocumentInvalid as exc:
        return Report(False, exc.issues, None)
    raise NotImplementedError("not implemented: owned by lane V")
