"""The plan library: compile a document to a plan, bind per-job values, and the plan identity."""

from wdf_jobdoc.plan_models import Plan, PlanStats

from .canonical import canonical_bytes, manifest_reference, plan_hash
from .compiler import compile_plan, task_request_fields

__all__ = [
    "Plan",
    "PlanStats",
    "bind",
    "canonical_bytes",
    "compile_plan",
    "manifest_reference",
    "plan_hash",
    "task_request_fields",
]


def bind(plan, params, profile):
    raise NotImplementedError("not implemented: owned by lane P")
