"""Reads a stored plan back into a `Plan`."""

import json
from collections.abc import Mapping
from typing import Any

from wdf_jobdoc.plan_models import Plan

READABLE_VERSION = 1


class UnsupportedPlanVersion(ValueError):
    """The plan names a version this reader has no branch for."""

    def __init__(self, version: Any):
        super().__init__(f"plan version {version!r} is not readable, this reader reads version 1")
        self.version = version


def read_plan(data: bytes | str | Mapping[str, Any]) -> Plan:
    """The plan in `data`, as JSON text or an already parsed mapping.

    Raises UnsupportedPlanVersion for any `plan_version` but 1, and pydantic's ValidationError for a
    version 1 plan that does not fit the model.
    """
    if isinstance(data, bytes | str):
        data = json.loads(data)
    version = data.get("plan_version") if isinstance(data, Mapping) else None
    if type(version) is not int or version != READABLE_VERSION:
        raise UnsupportedPlanVersion(version)
    return Plan.model_validate(data)
