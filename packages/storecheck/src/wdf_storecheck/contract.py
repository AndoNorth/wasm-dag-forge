"""Loads one store contract file and checks it against the models and the bucket layout.

Usage: python -m wdf_storecheck.contract <roles.yaml|lifecycle.yaml|products.yaml>
Exit 0 valid, 1 invalid with "reason: <code>: <detail>" on stderr, 2 usage.
"""

import re
import sys
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from .models import CONTRACT, ROLES, Lifecycle, Products, Roles

SECRET_KEY = re.compile(
    r"secret|password|passwd|token|credential|access[_-]?key|private[_-]?key", re.I
)
TOP_LEVEL = {"roles": Roles, "buckets": Lifecycle, "products": Products}


class Refused(Exception):
    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")


def buckets() -> list[str]:
    return yaml.safe_load((CONTRACT / "layout.yaml").read_text())["buckets"]


def windows() -> dict:
    return yaml.safe_load((CONTRACT / "retention.yaml").read_text())


def secret_keys(node, path: str = "") -> list[str]:
    """Every mapping key at any depth whose name reads as a secret."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if SECRET_KEY.search(str(key)):
                found.append(here)
            found += secret_keys(value, here)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += secret_keys(value, f"{path}[{index}]")
    return found


def check_roles(model: Roles) -> None:
    known = buckets()
    for role in model.roles:
        if role not in ROLES:
            raise Refused("role-outside-matrix", f"{role} is not one of the roles of the store")
    missing = [role for role in ROLES if role not in model.roles]
    if missing:
        raise Refused("role-outside-matrix", f"no grants for {', '.join(missing)}")
    for role, grants in model.roles.items():
        for bucket in grants:
            if bucket not in known:
                raise Refused(
                    "unknown-bucket", f"{role} names {bucket}, which is not in the layout"
                )


def check_lifecycle(model: Lifecycle) -> None:
    known = buckets()
    for bucket in model.buckets:
        if bucket not in known:
            raise Refused("unknown-bucket", f"{bucket} is not in the layout")
    for bucket in known:
        if not model.buckets.get(bucket) or not model.buckets[bucket].rules:
            raise Refused("bucket-without-rules", f"{bucket} has no lifecycle rule")
    retention = windows()
    for bucket, entry in model.buckets.items():
        for rule in entry.rules:
            block, target, name = rule.window.split(".")
            if name not in retention.get(block, {}).get(target, {}):
                raise Refused(
                    "schema-violation", f"{bucket} names {rule.window}, which retention.yaml lacks"
                )


def check_products(model: Products) -> None:
    for name, product in model.products.items():
        for gap in product.known_gaps:
            for role in gap.roles:
                if role not in ROLES:
                    raise Refused("role-outside-matrix", f"{name} gap {gap.gap} names {role}")


def check(document) -> None:
    """Raises Refused with the first rule that the document breaks."""
    found = secret_keys(document)
    if found:
        raise Refused(
            "secret-in-contract",
            f"{found[0]} reads as a secret, and credentials are never committed",
        )
    model: type[BaseModel] | None = None
    if isinstance(document, dict):
        model = next((m for key, m in TOP_LEVEL.items() if key in document), None)
    if model is None:
        raise Refused(
            "schema-violation", "the file has none of roles, buckets or products at its top level"
        )
    try:
        parsed = model.model_validate(document)
    except ValidationError as error:
        raise Refused("schema-violation", " ".join(str(error).split())) from error
    {Roles: check_roles, Lifecycle: check_lifecycle, Products: check_products}[model](parsed)


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not Path(argv[0]).is_file():
        print("usage: contract.py <roles.yaml|lifecycle.yaml|products.yaml>", file=sys.stderr)
        return 2
    try:
        check(yaml.safe_load(Path(argv[0]).read_text()))
    except yaml.YAMLError as error:
        print(f"reason: schema-violation: {' '.join(str(error).split())}", file=sys.stderr)
        return 1
    except Refused as refused:
        print(f"reason: {refused}", file=sys.stderr)
        return 1
    print(f"ok {argv[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
