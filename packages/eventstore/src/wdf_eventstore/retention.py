"""Loads retention.yaml and checks the retention rules.

Usage: python -m wdf_eventstore.retention <retention.yaml>
Exit 0 valid, 1 invalid with "reason: <code>: <detail>" on stderr, 2 usage.
"""

import sys
from pathlib import Path

import yaml
from pydantic import ValidationError
from wdf_compat.window import MAX_JOB_DURATION_DAYS

from .models import CONTRACT, Retention

JOB_TABLE = "accepted_job"
EVENT_TABLE = "event"


class Refused(Exception):
    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


def layout() -> dict:
    return yaml.safe_load((CONTRACT / "layout.yaml").read_text())


def check(retention: Retention) -> None:
    """Raises Refused with the first rule that the file breaks."""
    kinds = layout()["kinds"].values()
    buckets = layout()["buckets"]
    addressed = {k["bucket"] for k in kinds if k["class"] == "content-addressed"}
    floor = 2 * MAX_JOB_DURATION_DAYS
    for block in (retention.windows, retention.housekeeping):
        if JOB_TABLE in block:
            raise Refused(
                "retention-on-job-rows", f"{JOB_TABLE} is the dedup point and is never aged"
            )
        for target in block:
            if target != EVENT_TABLE and target not in buckets:
                raise Refused("schema-violation", f"{target} is neither {EVENT_TABLE} nor a bucket")
    for target, rule in retention.windows.items():
        if target in addressed:
            raise Refused(
                "age-rule-on-content-addressed", f"{target} holds content-addressed objects"
            )
        days = rule.drop_after_days if target == EVENT_TABLE else rule.expire_after_days
        if days is None:
            raise Refused("schema-violation", f"{target} has no window of the kind it takes")
        if days < floor:
            raise Refused(
                "window-below-twice-max-job-duration",
                f"{target} is {days} days, the floor is {floor} (twice {MAX_JOB_DURATION_DAYS})",
            )


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not Path(argv[0]).is_file():
        print("usage: retention.py <retention.yaml>", file=sys.stderr)
        return 2
    try:
        retention = Retention.model_validate(yaml.safe_load(Path(argv[0]).read_text()))
        check(retention)
    except (ValidationError, yaml.YAMLError) as error:
        print(f"reason: schema-violation: {' '.join(str(error).split())}", file=sys.stderr)
        return 1
    except Refused as refused:
        print(f"reason: {refused}", file=sys.stderr)
        return 1
    print(f"ok {argv[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
