"""The figures that invariants and inputs compare against, read from the contracts."""

import json
from datetime import timedelta
from importlib import resources

import yaml

CONTRACT = resources.files(__package__) / "contract"


def params() -> dict:
    """Named query parameters: the unreferenced grace, the windows per status, the hard expiries
    and the codes of the artifact failures.

    The windows travel as a JSON object keyed by status, so a query reads them with `->`.
    """
    artifact = yaml.safe_load((CONTRACT / "artifact-retention.yaml").read_text())
    state = yaml.safe_load((CONTRACT / "state-retention.yaml").read_text())["windows"]
    failures = yaml.safe_load((CONTRACT / "failures.yaml").read_text())["codes"]
    return {
        "unreferenced_grace": timedelta(days=artifact["content"]["unreferenced_grace_days"]),
        "windows": json.dumps(artifact["statuses"]),
        "results_expiry": timedelta(days=state["wdf-results"]["expire_after_days"]),
        "checkpoints_expiry": timedelta(days=state["wdf-checkpoints"]["expire_after_days"]),
        "failure_codes": sorted(entry["code"] for entry in failures),
    }
