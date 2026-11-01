import hashlib
import json
from pathlib import Path

import pytest
from wdf_plan import bind, canonical_bytes

VECTORS = Path(__file__).resolve().parents[3] / "contracts/job/vectors"
PLANS = VECTORS / "plans/1"
EXAMPLES = sorted(p.stem for p in (VECTORS / "valid").glob("*.json"))


def test_there_are_examples_to_compile():
    assert len(EXAMPLES) == 7


@pytest.mark.parametrize("name", EXAMPLES)
def test_golden_plans_still_compile(name, plan_of, ci):
    """A change to defaults, ordering or canonical form fails here and needs a contract change."""
    template = plan_of((VECTORS / "valid" / f"{name}.json").read_bytes())
    params = json.loads((VECTORS / "job-params/valid" / f"{name}.seed-7.json").read_text())
    assert canonical_bytes(template) == (PLANS / f"{name}.manifest.json").read_bytes()
    bound = canonical_bytes(bind(template, params, ci))
    assert bound == (PLANS / f"{name}.seed-7.plan.json").read_bytes()


def test_every_golden_file_has_a_matching_hashes_line():
    lines = dict(
        reversed(line.split("  ", 1)) for line in (PLANS / "HASHES").read_text().splitlines()
    )
    assert sorted(lines) == sorted(p.name for p in PLANS.glob("*.json"))
    for name, digest in lines.items():
        assert hashlib.sha256((PLANS / name).read_bytes()).hexdigest() == digest, name
