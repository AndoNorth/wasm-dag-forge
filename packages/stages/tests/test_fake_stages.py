"""Each fake does what its table declares, and a stage that reads more fails."""

import pytest
import yaml
from wdf_stages import FAKES, ContractViolation, Facts, run_stage_contract
from wdf_stages.check import TABLE
from wdf_stages.contract import default_cases, invoke
from wdf_stages.recording import type_name

STAGES = yaml.safe_load(TABLE.read_text())["stages"]


@pytest.mark.parametrize("name", FAKES)
def test_each_fake_passes_the_contract_runner(name):
    run_stage_contract(name, FAKES[name])


@pytest.mark.parametrize("name", FAKES)
def test_each_fake_emits_every_declared_type_and_only_those(name):
    declared = {e["type"] for e in STAGES[name]["emits"]}
    seen = set()
    for inbound, facts in default_cases(name):
        seen |= {type_name(e.type) for e in invoke(name, FAKES[name], inbound, facts)}
    assert seen == declared


def test_the_worker_writes_references_into_the_heartbeat():
    facts = Facts(taskid="sim", attempt=1)
    (inbound, _), *_ = default_cases("worker")
    invoke("worker", FAKES["worker"], inbound, facts)
    (details,) = facts.heartbeat
    assert details["references"] == inbound.data["inputs"]
    assert all(isinstance(r, dict) and "uri" in r for r in details["references"])


def read_undeclared_attribute(inbound, facts):
    _ = inbound.subject
    return []


def read_undeclared_data_field(inbound, facts):
    inbound.data["inputs"]
    return []


def emit_from_another_source(inbound, facts):
    events = FAKES["dispatch"](inbound, facts)
    return [e.model_copy(update={"source": "/wdf/control"}) for e in events]


def emit_an_undeclared_type(inbound, facts):
    return FAKES["control"](inbound, Facts(emit=("job.admitted",)))


@pytest.mark.parametrize(
    ("stage", "callable_", "message"),
    [
        ("placement", read_undeclared_attribute, "undeclared attributes \\['subject'\\]"),
        ("placement", read_undeclared_data_field, "undeclared data fields \\['inputs'\\]"),
        ("dispatch", emit_from_another_source, "from /wdf/control"),
        ("placement", emit_an_undeclared_type, "does not declare"),
    ],
)
def test_a_stage_that_does_more_than_it_declares_fails(stage, callable_, message):
    with pytest.raises(ContractViolation, match=message):
        run_stage_contract(stage, callable_)
