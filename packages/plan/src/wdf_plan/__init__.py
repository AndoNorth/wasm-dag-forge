"""The plan library: compile a document to a plan, bind per-job values, and the plan identity."""

from wdf_jobdoc.plan_models import Plan, PlanStats

__all__ = ["Plan", "PlanStats", "bind", "compile_plan"]


def compile_plan(document, profile):
    raise NotImplementedError("not implemented: owned by lane P")


def bind(plan, params, profile):
    raise NotImplementedError("not implemented: owned by lane P")
