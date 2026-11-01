"""The validate, plan and patch-check commands: files in, a verdict on stdout, an exit code out.

Exit codes are 0 ok, 1 invalid or incompatible, 2 usage or unreadable input.
Nothing is read from the network, and every message that is not a verdict is one line on stderr.
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from wdf_jobcheck import StaticResolver, validate
from wdf_jobdoc.profile import PROFILES, SlotsIncomplete
from wdf_jobdoc.profile_files import load_profile
from wdf_jobpatch import patch_check
from wdf_plan import (
    bind,
    canonical_bytes,
    check_job_params,
    class_hint,
    compile_plan,
    plan_hash,
    stats,
)

ROOT = Path(__file__).resolve().parents[4]
MANIFEST_NAME = "manifest.json"
SKIPPED = "the bundle and index checks were skipped: no --resolver given"


class Usage(Exception):
    """Unusable input: the message is the one line printed before exit 2."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Usage(message)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="python -m wdf_jobtools")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    def common(command, json_output=True):
        command.add_argument("--profile", choices=PROFILES, default="ci")
        command.add_argument("--slots-dir", type=Path, help="replaces deploy/presets/slots")
        if json_output:
            command.add_argument("--json", action="store_true", help="print one JSON object")

    check = commands.add_parser("validate", help="every check over one job document")
    check.add_argument("document", type=Path)
    check.add_argument("--resolver", type=Path, help="a table shaped like vectors/resolver.json")
    common(check)

    plan = commands.add_parser("plan", help="compile a document, optionally bound to one job")
    plan.add_argument("document", type=Path)
    plan.add_argument("--params", type=Path, help="the values of one job")
    plan.add_argument("--out", type=Path, help="a directory that receives manifest.json")
    common(plan)

    patch = commands.add_parser("patch-check", help="whether a new document may replace the old")
    patch.add_argument("old", type=Path)
    patch.add_argument("new", type=Path)
    patch.add_argument("--params", type=Path, required=True, help="the values of the old side")
    patch.add_argument("--new-params", type=Path, help="the values of the new side, else --params")
    patch.add_argument("--completed", type=Path, required=True, help="a JSON list of task ids")
    common(patch)
    return parser


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise Usage(f"cannot read {path}: {exc.strerror or exc}") from exc


def _read_json(path: Path, kind):
    try:
        value = json.loads(_read(path))
    except ValueError as exc:
        raise Usage(f"{path} is not JSON: {exc}") from exc
    if not isinstance(value, kind):
        raise Usage(f"{path} is not a JSON {kind.__name__}")
    return value


def _profile(args):
    try:
        return load_profile(args.profile, ROOT, args.slots_dir)
    except (OSError, SlotsIncomplete) as exc:
        raise Usage(f"cannot load the {args.profile} profile: {exc}") from exc


def _issues(issues, prefix="") -> list[dict]:
    return [asdict(i) | {"message": prefix + i.message} for i in issues]


def _emit(args, body: dict, text: list[str]) -> None:
    print(json.dumps(body, indent=2, sort_keys=True) if args.json else "\n".join(text))


def _failure(args, issues: list[dict]) -> int:
    lines = [f"{i['code']}: {i['path'] or '/'} {i['message']}" for i in issues]
    _emit(args, {"ok": False, "issues": issues}, lines)
    return 1


def _template(args, profile, side="", path=None):
    """The compiled template plan of a document, or the failure issues of its validation."""
    report = validate(_read(path or args.document), profile)
    if not report.ok:
        return None, _issues(report.issues, side)
    return compile_plan(report.document, profile), []


def _bound(template, params, profile, side=""):
    issues = check_job_params(template, params, profile)
    return (None, _issues(issues, side)) if issues else (bind(template, params, profile), [])


def run_validate(args) -> int:
    profile = _profile(args)
    resolver = None
    if args.resolver:
        resolver = StaticResolver(_read_json(args.resolver, dict))
    report = validate(_read(args.document), profile, resolver)
    issues = _issues(report.issues)
    if resolver is None:
        print(SKIPPED, file=sys.stderr)
    if not report.ok:
        return _failure(args, issues)
    _emit(args, {"ok": True, "issues": []}, [f"ok {args.document}"])
    return 0


def run_plan(args) -> int:
    profile = _profile(args)
    params = _read_json(args.params, dict) if args.params else None
    template, issues = _template(args, profile)
    bound = None
    if template is not None and params is not None:
        bound, issues = _bound(template, params, profile)
    if issues:
        return _failure(args, issues)
    summary = stats(template, profile)
    result = {
        "manifest_hash": plan_hash(template),
        "plan_hash": plan_hash(bound) if bound is not None else None,
        "stats": summary.model_dump(mode="json"),
        "class_hint": class_hint(summary, profile),
    }
    if args.out:
        try:
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / MANIFEST_NAME).write_bytes(canonical_bytes(template))
        except OSError as exc:
            raise Usage(f"cannot write {args.out}: {exc.strerror or exc}") from exc
    text = [
        f"manifest_hash: {result['manifest_hash']}",
        f"plan_hash: {result['plan_hash'] or 'none: no --params given'}",
        f"class_hint: {result['class_hint']}",
        *(f"stats.{k}: {json.dumps(v, sort_keys=True)}" for k, v in result["stats"].items()),
    ]
    _emit(args, {"ok": True, "issues": []} | result, text)
    return 0


def run_patch_check(args) -> int:
    profile = _profile(args)
    completed = _read_json(args.completed, list)
    if not all(isinstance(task, str) for task in completed):
        raise Usage(f"{args.completed} is not a JSON list of task ids")
    old_params = _read_json(args.params, dict)
    new_params = _read_json(args.new_params, dict) if args.new_params else old_params
    sides = {}
    issues = []
    for side, path, params in (("old", args.old, old_params), ("new", args.new, new_params)):
        template, found = _template(args, profile, f"{side}: ", path)
        if template is not None:
            sides[side], found = _bound(template, params, profile, f"{side}: ")
        issues += found
    if issues:
        return _failure(args, issues)
    report = patch_check(sides["old"], sides["new"], completed)
    found = _issues(report.issues)
    body = {
        "ok": report.compatible,
        "issues": found,
        "changed": list(report.changed),
        "recompile": list(report.recompile),
    }
    if report.compatible:
        text = [
            "compatible",
            f"recompile: {' '.join(report.recompile) or 'none'}",
            f"changed: {' '.join(report.changed) or 'none'}",
        ]
    else:
        text = [f"{i['code']}: {i['path'] or '/'} {i['message']}" for i in found]
    _emit(args, body, text)
    return 0 if report.compatible else 1


COMMANDS = {"validate": run_validate, "plan": run_plan, "patch-check": run_patch_check}


def main(argv: list[str]) -> int:
    try:
        args = _parser().parse_args(argv)
        return COMMANDS[args.command](args)
    except Usage as exc:
        print(f"wdf_jobtools: {exc}", file=sys.stderr)
        return 2
