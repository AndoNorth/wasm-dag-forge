"""`python -m wdf_workflow.harness (--address HOST:PORT | --kube-context NAME) COMMAND`.

`describe` prints the namespace, its retention, its search attributes and the reaper's Schedule.
`smoke`, `restart` and `rollout` run `--jobs N` smoke jobs and print `finished:`, `failed:` and
`infra_retries:`, plus one line per scenario counter. `restart --target POD,...` restarts the named
Temporal Deployments' pods or `workflow-worker` once tasks are in flight, and `--scenario` adds
`pause-resume`, `zombie` or `worker-death` with `--deaths N`. `rollout` deploys the build of
WDF_BUILD_ID beside the current one.

Exit 0 when every job finished, 1 when one did not, 2 for a usage error or a command this tier
refuses, with the reason on stderr.
"""

import argparse
import asyncio
import os
import sys

from ..contract import naming
from . import SCENARIOS, Local, Refused, describe, rollout, run_jobs


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="python -m wdf_workflow.harness")
    where = top.add_mutually_exclusive_group(required=True)
    where.add_argument("--address", metavar="HOST:PORT", help="T1: a dev server")
    where.add_argument("--kube-context", metavar="NAME", help="T2: the cluster of a kube context")
    commands = top.add_subparsers(dest="command", required=True)
    commands.add_parser("describe")
    for name in ("smoke", "restart", "rollout"):
        command = commands.add_parser(name)
        command.add_argument("--jobs", type=int, default=1, metavar="N")
        if name == "rollout":
            continue
        command.add_argument("--scenario", choices=SCENARIOS)
        command.add_argument("--deaths", type=int, default=1, metavar="N")
        if name == "restart":
            command.add_argument(
                "--target",
                required=True,
                type=lambda value: [t for t in value.split(",") if t],
                metavar="POD,...",
            )
    return top


async def run(args: argparse.Namespace) -> int:
    if args.address:
        tier = Local(args.address)
    else:
        from .cluster import Cluster

        tier = Cluster(args.kube_context)
    scenario = getattr(args, "scenario", None)
    targets = getattr(args, "target", [])
    tier.refuse(args.command, scenario, targets)
    async with tier:
        if args.command == "describe":
            for line in await describe(tier.client):
                print(line)
            return 0
        if args.command == "rollout":
            build = os.environ.get(naming().workflow.build_id_env)
            if not build:
                raise Refused(f"rollout deploys the build of {naming().workflow.build_id_env}")
            counters = await rollout(tier, args.jobs, build)
        else:
            counters = await run_jobs(tier, args.command, args.jobs, scenario, args.deaths, targets)
    if not counters.printed:
        for line in counters.lines():
            print(line)
    return 0 if counters.finished == args.jobs * (2 if args.command == "rollout" else 1) else 1


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if getattr(args, "jobs", 1) < 1 or getattr(args, "deaths", 1) < 1:
        print("harness: --jobs and --deaths take a positive count", file=sys.stderr)
        return 2
    try:
        return asyncio.run(run(args))
    except Refused as refused:
        print(f"harness: refused: {refused}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
