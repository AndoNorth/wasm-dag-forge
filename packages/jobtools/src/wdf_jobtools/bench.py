"""python -m wdf_jobtools.bench: the cost of checking and binding the jobs of one template.

The template is validated and compiled once, then each job's values are checked, bound and hashed.
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from wdf_jobcheck import validate
from wdf_jobdoc.profile_files import load_profile
from wdf_jobexamples import generate
from wdf_plan import bind, compile_plan, plan_hash

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_TASKS = 8
# A regression guard on the default run, not a contract.
SECONDS_LIMIT = 60


@dataclass(frozen=True)
class Result:
    template_validations: int
    jobs: int
    seconds: float


def template(tasks: int) -> bytes:
    """A fan-out document of `tasks` tasks whose simulate seeds are the row seed."""
    document = json.loads(generate("fan-out", tasks - 2))
    document["parameters"] = {"seed": "seed"}
    for task in document["tasks"].values():
        if task["type"] == "simulate":
            task["params"]["seed"] = {"param": "seed"}
    return json.dumps(document).encode()


def run(tasks: int, jobs: int, profile) -> Result:
    started = time.perf_counter()
    validations = 0
    report = validate(template(tasks), profile)
    validations += 1
    if not report.ok:
        raise SystemExit(f"the benchmark template is invalid: {report.issues[0]}")
    plan = compile_plan(report.document, profile)
    for seed in range(jobs):
        plan_hash(bind(plan, {"seed": seed}, profile))
    return Result(validations, jobs, time.perf_counter() - started)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m wdf_jobtools.bench")
    parser.add_argument("--jobs", type=int, default=100_000)
    parser.add_argument("--tasks", type=int, help="the template size, with no pass line")
    args = parser.parse_args(argv)
    if args.jobs < 1 or (args.tasks is not None and args.tasks < 3):
        parser.error("--jobs needs at least 1 and --tasks at least 3")
    tasks = args.tasks or DEFAULT_TASKS
    result = run(tasks, args.jobs, load_profile("ci", ROOT))
    print(f"template_validations: {result.template_validations}")
    print(f"tasks: {tasks}")
    print(f"jobs: {result.jobs}")
    print(f"seconds: {result.seconds:.2f}")
    if args.tasks is not None:
        return 0
    passed = result.seconds < SECONDS_LIMIT
    print(
        f"pass: seconds below {SECONDS_LIMIT}"
        if passed
        else f"FAIL: seconds not below {SECONDS_LIMIT}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
