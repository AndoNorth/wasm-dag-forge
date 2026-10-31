"""The document models: shape, closed sets, placeholders and the code each fault gets."""

import pytest
from pydantic import ValidationError
from wdf_jobdoc import Placeholder


def test_defaults(load, good):
    document = load(good)
    assert document.max_parallel is None
    assert document.tasks["sim"].checkpoint is False
    assert document.tasks["agg"].allow_partial is False
    assert document.tasks["agg"].retries == 0
    assert document.tasks["sim"].depends_on == []


def test_a_placeholder_parses_to_its_model(load, good):
    document = load(good)
    assert document.tasks["sim"].params.seed == Placeholder(param="seed")


def test_a_placeholder_may_be_a_simulate_or_aggregate_argument(load, good):
    good["parameters"] = {"seed": "seed", "n": "integer", "cols": "string-list"}
    good["tasks"]["sim"]["params"]["args"] = {"n": {"param": "n"}}
    good["tasks"]["agg"]["params"]["args"] = {"columns": {"param": "cols"}}
    document = load(good)
    assert document.tasks["sim"].params.args["n"] == Placeholder(param="n")
    assert document.tasks["agg"].params.args["columns"] == Placeholder(param="cols")


def test_arguments_hold_scalars_and_lists_of_them(load, good):
    good["tasks"]["sim"]["params"]["args"] = {"a": 1, "b": "x", "c": True, "d": [1, "y", False]}
    assert load(good).tasks["sim"].params.args["d"] == [1, "y", False]


def test_a_template_may_have_no_parameters(load, good):
    del good["parameters"]
    good["tasks"]["sim"]["params"]["seed"] = 7
    assert load(good).parameters == {}


def test_an_aggregate_only_job_carries_an_index_reference(load, good):
    index = {**good["tasks"]["build"]["params"]["source"], "kind": "index"}
    good["tasks"] = {"agg": {**good["tasks"]["agg"], "inputs": []}}
    good["tasks"]["agg"]["params"]["index"] = index
    assert load(good).tasks["agg"].params.index.kind == "index"


def test_a_source_reference_is_not_an_index_reference(issues, good):
    good["tasks"]["agg"]["params"]["index"] = good["tasks"]["build"]["params"]["source"]
    assert issues(good) == [("bad-value", "/tasks/agg/params/index/kind")]


@pytest.mark.parametrize("name", ["a", "a1", "build-1", "x.y_z", "0start", "a" * 63])
def test_task_ids_follow_the_artifact_contract(load, good, name):
    good["tasks"][name] = good["tasks"].pop("agg")
    assert name in load(good).tasks


@pytest.mark.parametrize("name", ["", "A", "-a", ".a", "a b", "a/b", "a" * 64, "é"])
def test_bad_task_ids_are_refused(issues, good, name):
    good["tasks"][name] = good["tasks"].pop("agg")
    assert [code for code, _ in issues(good)] == ["bad-task-id"]


def test_a_dependency_id_follows_the_same_rule(issues, good):
    good["tasks"]["agg"]["depends_on"] = ["Sim"]
    assert issues(good) == [("bad-task-id", "/tasks/agg/depends_on/0")]


def test_an_unknown_field_is_refused_at_every_level(issues, good):
    good["extra"] = 1
    good["tasks"]["sim"]["extra"] = 1
    good["tasks"]["sim"]["params"]["extra"] = 1
    good["tasks"]["build"]["params"]["source"]["extra"] = 1
    assert [code for code, _ in issues(good)] == ["unknown-field"] * 4


@pytest.mark.parametrize("kind", ["shell", "Compile", "", "native"])
def test_a_task_type_outside_the_closed_set_is_refused(issues, good, kind):
    good["tasks"]["sim"]["type"] = kind
    assert issues(good) == [("unknown-task-type", "/tasks/sim/type")]


def test_a_missing_type_is_a_missing_field(issues, good):
    del good["tasks"]["sim"]["type"]
    assert issues(good) == [("missing-field", "/tasks/sim/type")]


@pytest.mark.parametrize(
    ("task", "field", "value"),
    [
        ("build", "cpu", 1),
        ("build", "memory_mib", 512),
        ("build", "run_timeout_s", 60),
        ("build", "retries", 1),
        ("build", "checkpoint", True),
        ("build", "allow_partial", True),
        ("sim", "allow_partial", True),
        ("agg", "checkpoint", True),
    ],
)
def test_a_field_not_allowed_for_its_type_is_refused(issues, good, task, field, value):
    good["tasks"][task][field] = value
    assert issues(good) == [("unknown-field", f"/tasks/{task}/{field}")]


def test_a_seed_in_an_aggregate_task_is_refused(issues, good):
    good["tasks"]["agg"]["params"]["seed"] = 7
    assert issues(good) == [("unknown-field", "/tasks/agg/params/seed")]


@pytest.mark.parametrize(
    ("task", "field"),
    [("sim", "cpu"), ("sim", "memory_mib"), ("sim", "run_timeout_s"), ("sim", "retries")]
    + [("agg", "cpu"), ("agg", "memory_mib"), ("agg", "run_timeout_s")],
)
def test_required_task_fields(issues, good, task, field):
    del good["tasks"][task][field]
    assert issues(good) == [("missing-field", f"/tasks/{task}/{field}")]


def test_a_simulate_task_needs_its_params(issues, good):
    del good["tasks"]["sim"]["params"]["seed"]
    del good["tasks"]["sim"]["params"]["workload"]
    assert issues(good) == [
        ("missing-field", "/tasks/sim/params/seed"),
        ("missing-field", "/tasks/sim/params/workload"),
    ]


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("memory_mib", 0, "bad-value"),
        ("memory_mib", "512", "bad-type"),
        ("memory_mib", True, "bad-type"),
        ("run_timeout_s", 0, "bad-value"),
        ("retries", -1, "bad-value"),
        ("retries", "1", "bad-type"),
        ("cpu", "1", "bad-type"),
        ("cpu", True, "bad-type"),
        ("cpu", None, "bad-type"),
        ("depends_on", "build", "bad-type"),
        ("inputs", {}, "bad-type"),
        ("checkpoint", 1, "bad-type"),
    ],
)
def test_resource_and_policy_fields_are_typed(issues, good, field, value, code):
    good["tasks"]["sim"][field] = value
    assert issues(good) == [(code, f"/tasks/sim/{field}")]


@pytest.mark.parametrize("seed", [0, 7, 2**53 - 1])
def test_a_literal_seed_in_range(load, good, seed):
    good["tasks"]["sim"]["params"]["seed"] = seed
    assert load(good).tasks["sim"].params.seed == seed


@pytest.mark.parametrize("seed", [-1, 2**53, 2**63])
def test_a_literal_seed_out_of_range_is_refused(issues, good, seed):
    good["tasks"]["sim"]["params"]["seed"] = seed
    assert issues(good) == [("bad-value", "/tasks/sim/params/seed")]


@pytest.mark.parametrize("seed", ["7", 7.0, True, None, [7]])
def test_a_seed_of_another_type_is_refused(issues, good, seed):
    good["tasks"]["sim"]["params"]["seed"] = seed
    assert [code for code, _ in issues(good)] in (["bad-type"], ["float-not-allowed"])


@pytest.mark.parametrize(
    "marker",
    [
        {"param": "seed", "x": 1},
        {"param": 7},
        {"param": "Seed"},
        {"param": ""},
        {"parm": "seed"},
        {},
    ],
)
def test_a_malformed_placeholder_is_refused(issues, good, marker):
    good["tasks"]["sim"]["params"]["args"]["n"] = marker
    assert issues(good) == [("placeholder-malformed", "/tasks/sim/params/args/n")]


def test_a_placeholder_with_a_second_key_is_refused_as_a_seed(issues, good):
    good["tasks"]["sim"]["params"]["seed"] = {"param": "seed", "default": 1}
    assert issues(good) == [("placeholder-malformed", "/tasks/sim/params/seed")]


def test_a_placeholder_is_not_a_list_element(issues, good):
    good["tasks"]["agg"]["params"]["args"]["blocks"] = ["pi", {"param": "b"}]
    assert issues(good) == [("placeholder-misplaced", "/tasks/agg/params/args/blocks")]


def test_a_placeholder_is_not_a_key(issues, good):
    good["tasks"]["sim"]["params"]["args"]['{"param": "n"}'] = 1
    assert [code for code, _ in issues(good)] == ["bad-value"]


@pytest.mark.parametrize(
    "where",
    [
        lambda t: t["sim"].update(depends_on=[{"param": "n"}]),
        lambda t: t["sim"]["inputs"].append({"param": "n"}),
        lambda t: t["sim"]["inputs"][0].update(task={"param": "n"}),
        lambda t: t["sim"].update(memory_mib={"param": "n"}),
        lambda t: t["sim"]["params"].update(workload={"param": "n"}),
        lambda t: t["agg"]["params"].update(aggregation={"param": "n"}),
    ],
)
def test_a_placeholder_never_fills_a_reference_a_resource_or_a_name(issues, good, where):
    where(good["tasks"])
    assert [code for code, _ in issues(good)] == ["placeholder-misplaced"]


@pytest.mark.parametrize(
    "where",
    [
        lambda c: c["params"].update(entry={"param": "n"}),
        lambda c: c["params"].update(source={"param": "n"}),
        lambda c: c["params"]["source"].update(sha256={"param": "n"}),
        lambda c: c.update(depends_on=[{"param": "n"}]),
        lambda c: c.update(inputs=[{"task": {"param": "n"}, "output": "x"}]),
    ],
)
def test_a_placeholder_in_a_compile_task_is_refused(issues, good, where):
    where(good["tasks"]["build"])
    assert {code for code, _ in issues(good)} == {"placeholder-in-compile"}


def test_a_parameter_declaration(load, good):
    good["parameters"] = {
        "seed": "seed",
        "n": "integer",
        "s": "string",
        "b": "boolean",
        "l": "string-list",
    }
    assert len(load(good).parameters) == 5


def test_more_than_one_seed_parameter_is_allowed(load, good):
    good["parameters"] = {"seed": "seed", "seed_b": "seed"}
    assert load(good).parameters["seed_b"] == "seed"


@pytest.mark.parametrize(
    "declarations",
    [
        {"Seed": "seed"},
        {"1a": "integer"},
        {"a" * 33: "integer"},
        {"a-b": "integer"},
        {"n": "float"},
        {"n": "string_list"},
        {"n": 1},
        {"n": {"param": "seed"}},
        {"seed": "integer"},
        {"seed": "string"},
        [],
        "seed",
    ],
)
def test_a_bad_parameter_declaration_is_refused(issues, good, declarations):
    good["parameters"] = declarations
    assert {code for code, _ in issues(good)} == {"bad-parameter-declaration"}


@pytest.mark.parametrize(
    "version", ["urn:wdf:schema:job:1.1", "urn:wdf:schema:job:2.0", "job", 1, None]
)
def test_a_schema_version_this_reader_does_not_read_is_refused(issues, good, version):
    good["schema"] = version
    assert issues(good) == [("unsupported-schema-version", "/schema")]


def test_a_missing_schema_member_is_a_missing_field(issues, good):
    del good["schema"]
    assert issues(good) == [("missing-field", "/schema")]


def test_a_document_needs_a_task(issues, good):
    good["tasks"] = {}
    assert issues(good) == [("bad-value", "/tasks")]


@pytest.mark.parametrize("value", [0, -1, "8", 1.5])
def test_max_parallel_is_a_positive_integer(issues, good, value):
    good["max_parallel"] = value
    assert issues(good)[0][1] == "/max_parallel"


def test_max_parallel_is_read(load, good):
    good["max_parallel"] = 4
    assert load(good).max_parallel == 4


def test_a_reference_is_a_content_address(issues, good):
    good["tasks"]["build"]["params"]["source"]["uri"] = f"s3://wdf-sources/sha256/bb/{'b' * 64}"
    assert issues(good) == [("bad-value", "/tasks/build/params/source")]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sha256", "A" * 64),
        ("sha256", "a" * 63),
        ("size", -1),
        ("name", "a/b"),
        ("name", ""),
        ("kind", "index"),
    ],
)
def test_a_malformed_reference_is_refused(issues, good, field, value):
    good["tasks"]["build"]["params"]["source"][field] = value
    assert issues(good)


@pytest.mark.parametrize("entry", ["sim", "pkg.mod", "_x", "A1.b2"])
def test_entry_modules(load, good, entry):
    good["tasks"]["build"]["params"]["entry"] = entry
    assert load(good).tasks["build"].params.entry == entry


@pytest.mark.parametrize("entry", ["", "1a", "a..b", "a.", "a-b", "a/b", "a" * 129])
def test_bad_entry_modules(issues, good, entry):
    good["tasks"]["build"]["params"]["entry"] = entry
    assert issues(good) == [("bad-value", "/tasks/build/params/entry")]


def test_models_are_frozen(load, good):
    document = load(good)
    with pytest.raises(ValidationError):
        document.max_parallel = 3
    with pytest.raises(ValidationError):
        document.tasks["sim"].retries = 3


def test_cpu_milli_is_exact(load, good):
    good["tasks"]["agg"]["cpu"] = 2
    document = load(good)
    assert (document.tasks["sim"].cpu_milli, document.tasks["agg"].cpu_milli) == (250, 2000)
