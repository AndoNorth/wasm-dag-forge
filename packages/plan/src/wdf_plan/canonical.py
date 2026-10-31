"""The identity of a plan: canonical bytes, their hash, and the manifest reference."""

import hashlib
import json

from wdf_jobdoc.plan_models import Plan

# The one name of a stored manifest, as in the artifact contract's manifest vector.
MANIFEST_NAME = "manifest.json"


def canonical_bytes(plan: Plan) -> bytes:
    """The ASCII bytes of the plan with sorted keys and no whitespace.

    A plan holds integers, strings, booleans and lists and maps of them, so no float formatting
    enters, and equal plans give equal bytes whatever order their keys were written in.
    """
    return json.dumps(
        plan.model_dump(mode="json", exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def plan_hash(plan: Plan) -> str:
    """The SHA-256 of the canonical bytes, in lower case hex."""
    return hashlib.sha256(canonical_bytes(plan)).hexdigest()


def manifest_reference(plan: Plan) -> dict:
    """The content-addressed manifest reference to the canonical bytes of a template plan."""
    digest = plan_hash(plan)
    return {
        "uri": f"s3://wdf-sources/sha256/{digest[:2]}/{digest}",
        "sha256": digest,
        "size": len(canonical_bytes(plan)),
        "name": MANIFEST_NAME,
        "kind": "manifest",
    }
