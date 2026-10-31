import json
from pathlib import Path

import pytest
import yaml
from wdf_plan import check_job_params

VECTORS = Path(__file__).resolve().parents[3] / "contracts/job/vectors/job-params/invalid"
TEMPLATES = sorted(VECTORS.glob("*/*.template.json"))
CODES = (
    "unbound-parameter",
    "unknown-parameter",
    "parameter-type-mismatch",
    "bad-seed",
    "unknown-class",
)


def ident(path):
    return f"{path.parent.name}/{path.name.removesuffix('.template.json')}"


@pytest.mark.parametrize("template", TEMPLATES, ids=ident)
def test_params_vectors_report_the_code_of_their_directory(template, plan_of, ci):
    params = json.loads(
        template.with_name(template.name.replace(".template.", ".params.")).read_text()
    )
    issues = check_job_params(plan_of(json.loads(template.read_text())), params, ci)
    assert template.parent.name in {i.code for i in issues}


def test_every_params_pair_is_complete():
    for template in TEMPLATES:
        assert template.with_name(template.name.replace(".template.", ".params.")).is_file()
    assert len(list(VECTORS.glob("*/*.params.json"))) == len(TEMPLATES)


def test_every_listed_code_has_a_vector_and_is_in_the_error_table():
    table = yaml.safe_load((VECTORS.parents[2] / "errors.yaml").read_text())["codes"]
    assert {p.parent.name for p in TEMPLATES} == set(CODES) <= set(table)
    assert all(table[c]["class"] == "params" for c in CODES)
