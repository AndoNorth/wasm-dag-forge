"""Per-job values: checking a row's parameter block against a template plan, and binding it.

A job row carries a seed, the values of the other declared parameters and an optional class
override.
Binding replaces each placeholder of the template plan by the job's value of that name, as a whole
value, and changes nothing else.
"""

from wdf_jobdoc import Issue
from wdf_jobdoc.issues import pointer
from wdf_jobdoc.models import SEED_LIMIT
from wdf_jobdoc.plan_models import Plan

from .canonical import canonical_bytes

RESERVED = "seed"


class ParamsInvalid(ValueError):
    """The row's block does not fit the template; `issues` says why."""

    def __init__(self, issues):
        self.issues = tuple(issues)
        first = self.issues[0]
        super().__init__(f"{len(self.issues)} issue(s), first: {first.code} at {first.path}")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _value_issue(declared: str, value, path: str) -> Issue | None:
    """The issue of one value against its declared type, or None when it fits."""
    if declared in ("seed",):
        if not _is_int(value):
            return Issue("parameter-type-mismatch", path, "a seed is an integer")
        if not 0 <= value < SEED_LIMIT:
            return Issue("bad-seed", path, "a seed is an integer from 0 below 2**53")
        return None
    fits = {
        "integer": _is_int(value),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "string-list": isinstance(value, list) and all(isinstance(v, str) for v in value),
    }[declared]
    if fits:
        return None
    return Issue(
        "parameter-type-mismatch", path, f"the value is not of the declared type {declared}"
    )


def _field_issues(plan: Plan, params, profile) -> list[Issue]:
    issues = []
    row_seed = params.get("seed")
    values = params.get("parameters") or {}
    if row_seed is not None:
        if (issue := _value_issue("seed", row_seed, "/seed")) is not None:
            issues.append(issue)
    elif RESERVED in plan.parameters:
        issues.append(
            Issue("unbound-parameter", "/seed", "the template declares seed, the row has none")
        )
    for name, value in values.items():
        path = pointer(("parameters", name))
        if name == RESERVED:
            issues.append(Issue("unknown-parameter", path, "seed is the row seed, not a parameter"))
        elif name not in plan.parameters:
            issues.append(Issue("unknown-parameter", path, "the template does not declare it"))
        elif (issue := _value_issue(plan.parameters[name], value, path)) is not None:
            issues.append(issue)
    for name in plan.parameters:
        if name != RESERVED and name not in values:
            issues.append(Issue("unbound-parameter", pointer(("parameters", name)), "no value"))
    override = params.get("class_override")
    if override is not None and override not in profile.classes:
        issues.append(
            Issue("unknown-class", "/class_override", f"not one of {', '.join(profile.classes)}")
        )
    return issues


def check_job_params(plan: Plan, params, profile) -> tuple[Issue, ...]:
    """Every fault of the row's block against the template plan, sorted by path then code.

    `params` is the block as a mapping: `seed`, `parameters` and `class_override`.
    A block that fits is also bound to see that the stored job stays within the plan size limit.
    """
    issues = _field_issues(plan, params, profile)
    if not issues:
        size = len(canonical_bytes(_bound(plan, params)))
        if size > profile.max_plan_bytes:
            issues.append(
                Issue(
                    "plan-too-large",
                    "",
                    f"the bound plan is {size} bytes, at most {profile.max_plan_bytes} are allowed",
                )
            )
    return tuple(sorted(issues, key=lambda i: (i.path, i.code)))


def _is_placeholder(value) -> bool:
    return isinstance(value, dict) and set(value) == {"param"}


def _bound(plan: Plan, params) -> Plan:
    values = {**(params.get("parameters") or {}), RESERVED: params.get("seed")}
    data = plan.model_dump(mode="json", exclude_none=True)
    for task in data["tasks"]:
        # A compile task holds no placeholder, and only a simulate seed and the args of the other
        # two types may.
        if task["type"] == "compile":
            continue
        slots = task["params"]["args"] if "args" in task["params"] else {}
        for key, value in slots.items():
            if _is_placeholder(value):
                slots[key] = values[value["param"]]
        if _is_placeholder(task["params"].get("seed")):
            task["params"]["seed"] = values[task["params"]["seed"]["param"]]
    return Plan.model_validate(data)


def bind(plan: Plan, params, profile) -> Plan:
    """The plan with each placeholder replaced by the job's value, or ParamsInvalid.

    The declarations stay in the bound plan, so binding it again with the same block changes
    nothing.
    """
    if issues := check_job_params(plan, params, profile):
        raise ParamsInvalid(issues)
    return _bound(plan, params)
