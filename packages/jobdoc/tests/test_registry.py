"""The task type and aggregation registries: closed, and consistent with models and contracts."""

import json
import typing

import pytest
import yaml
from wdf_jobdoc import models
from wdf_jobdoc.registry import AGGREGATIONS, PARAMETER_TYPES, REGISTRY, TASK_TYPES


def tags(union):
    """The discriminator values of an annotated union of models, read from their literal field."""
    members = typing.get_args(typing.get_args(union)[0])
    field = typing.get_args(union)[1].discriminator
    return {typing.get_args(m.model_fields[field].annotation)[0]: m for m in members}


def test_the_committed_registry_is_the_registry(contract):
    assert json.loads((contract / "registry.json").read_text()) == REGISTRY


def test_the_task_types_are_the_three_of_the_model():
    assert set(TASK_TYPES) == set(tags(models.Task)) == {"compile", "simulate", "aggregate"}


def test_the_aggregations_are_the_ones_of_the_model():
    assert set(AGGREGATIONS) == set(tags(models.AggregateParams))


def test_aggregation_arguments_are_the_fields_of_their_model():
    for name, model in tags(models.AggregateParams).items():
        fields = model.model_fields["args"].annotation.model_fields
        declared = AGGREGATIONS[name]["args"]
        assert set(fields) == set(declared), name
        for arg, entry in declared.items():
            assert entry["type"] in PARAMETER_TYPES
            assert fields[arg].is_required() == entry["required"], (name, arg)


def test_registry_pools_exist_in_topology(root):
    topology = yaml.safe_load((root / "contracts/topology/topology.yaml").read_text())
    for name, entry in TASK_TYPES.items():
        assert entry["pool"] in topology["pools"], name


def test_a_pool_is_derived_from_the_type_and_a_document_cannot_name_one():
    assert {name: entry["pool"] for name, entry in TASK_TYPES.items()} == {
        name: name for name in TASK_TYPES
    }
    for task in tags(models.Task).values():
        assert "pool" not in task.model_fields


def test_registry_kinds_exist_in_artifact_layout(root):
    kinds = set(yaml.safe_load((root / "contracts/artifact/layout.yaml").read_text())["kinds"])
    for name, entry in TASK_TYPES.items():
        assert set(entry["outputs"].values()) <= kinds, name
        assert set(entry["inputs"]["accepts"]) <= kinds, name
    for name, entry in AGGREGATIONS.items():
        assert {entry["reads"], entry["writes"]} <= kinds, name


def test_every_accepted_kind_is_produced_by_some_type():
    produced = {kind for entry in TASK_TYPES.values() for kind in entry["outputs"].values()}
    for entry in TASK_TYPES.values():
        assert set(entry["inputs"]["accepts"]) <= produced


def test_input_counts_are_consistent():
    for name, entry in TASK_TYPES.items():
        counts = entry["inputs"]
        assert counts["max"] is None or counts["min"] <= counts["max"], name
        if not counts["accepts"]:
            assert counts["max"] == 0, name


def test_the_aggregations_read_and_write_what_the_aggregate_type_does():
    aggregate = TASK_TYPES["aggregate"]
    for entry in AGGREGATIONS.values():
        assert entry["reads"] in aggregate["inputs"]["accepts"]
        assert entry["writes"] in aggregate["outputs"].values()


@pytest.mark.parametrize("name", ["rm_rf", "Summary_Stats", "", "summary-stats"])
def test_an_aggregation_outside_the_registry_is_refused(issues, good, name):
    good["tasks"]["agg"]["params"]["aggregation"] = name
    assert issues(good) == [("unknown-aggregation", "/tasks/agg/params/aggregation")]


def test_an_aggregate_without_an_aggregation_is_a_missing_field(issues, good):
    del good["tasks"]["agg"]["params"]["aggregation"]
    assert issues(good) == [("missing-field", "/tasks/agg/params/aggregation")]


def test_digest_list_takes_no_arguments(load, issues, good):
    good["tasks"]["agg"]["params"] = {"aggregation": "digest_list"}
    assert load(good).tasks["agg"].params.aggregation == "digest_list"
    good["tasks"]["agg"]["params"]["args"] = {"blocks": ["pi"]}
    assert issues(good) == [("unknown-field", "/tasks/agg/params/args/blocks")]


def test_summary_stats_arguments_are_optional_lists_of_strings(load, issues, good):
    good["tasks"]["agg"]["params"]["args"] = {}
    assert load(good).tasks["agg"].params.args.blocks is None
    good["tasks"]["agg"]["params"]["args"] = {"blocks": ["a"], "columns": ["x", "y"]}
    assert load(good).tasks["agg"].params.args.columns == ["x", "y"]
    good["tasks"]["agg"]["params"]["args"] = {"blocks": "a"}
    assert issues(good) == [("bad-type", "/tasks/agg/params/args/blocks")]
    good["tasks"]["agg"]["params"]["args"] = {"columns": ["x", 1]}
    assert issues(good) == [("bad-type", "/tasks/agg/params/args/columns")]


def test_an_unknown_aggregate_argument_is_refused_with_its_path(issues, good):
    good["tasks"]["agg"]["params"]["args"] = {"window": 5}
    assert issues(good) == [("unknown-field", "/tasks/agg/params/args/window")]


def test_a_seed_in_an_aggregate_task_is_unknown_for_every_aggregation(issues, good):
    for name in AGGREGATIONS:
        good["tasks"]["agg"]["params"] = {"aggregation": name, "seed": 7}
        assert issues(good) == [("unknown-field", "/tasks/agg/params/seed")], name


def test_a_simulate_param_named_like_an_aggregation_keeps_its_path(issues, good):
    good["tasks"]["sim"]["params"]["summary_stats"] = 1
    assert issues(good) == [("unknown-field", "/tasks/sim/params/summary_stats")]
