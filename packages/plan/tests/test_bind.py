import pytest
from wdf_plan import ParamsInvalid, bind, canonical_bytes, check_job_params, plan_hash

PARAMS = {"seed": 7, "parameters": {"n": 1000, "tags": ["pi", "e"]}}


@pytest.fixture
def template(good, plan_of):
    good["parameters"] = {"seed": "seed", "n": "integer", "tags": "string-list"}
    good["tasks"]["sim"]["params"]["args"] = {"n": {"param": "n"}}
    good["tasks"]["agg"]["params"]["args"] = {"blocks": {"param": "tags"}}
    return plan_of(good)


def differences(template, bound, path=()):
    """The paths where two JSON values differ, and the pair at each."""
    if isinstance(template, dict) and isinstance(bound, dict) and template.keys() == bound.keys():
        return {
            p: d
            for k in template
            for p, d in differences(template[k], bound[k], (*path, k)).items()
        }
    if isinstance(template, list) and isinstance(bound, list) and len(template) == len(bound):
        return {
            p: d
            for i, (a, b) in enumerate(zip(template, bound, strict=True))
            for p, d in differences(a, b, (*path, i)).items()
        }
    return {path: (template, bound)} if template != bound else {}


def test_bind_replaces_placeholders_by_whole_value(template, ci):
    bound = bind(template, PARAMS, ci)
    sim = next(t for t in bound.tasks if t.id == "sim")
    agg = next(t for t in bound.tasks if t.id == "agg")
    assert (sim.params.seed, sim.params.args) == (7, {"n": 1000})
    assert agg.params.args.blocks == ["pi", "e"]


def test_bind_replaces_only_placeholders(template, ci):
    before = template.model_dump(mode="json", exclude_none=True)
    after = bind(template, PARAMS, ci).model_dump(mode="json", exclude_none=True)
    changed = differences(before, after)
    placeholder = {"param": "seed"}
    assert {p: d for p, d in changed.items()} == {
        ("tasks", 1, "params", "seed"): (placeholder, 7),
        ("tasks", 1, "params", "args", "n"): ({"param": "n"}, 1000),
        ("tasks", 2, "params", "args", "blocks"): ({"param": "tags"}, ["pi", "e"]),
    }
    assert before["parameters"] == after["parameters"]


def test_bind_is_idempotent(template, ci):
    once = bind(template, PARAMS, ci)
    assert bind(template, PARAMS, ci) == once
    assert bind(once, PARAMS, ci) == once
    assert canonical_bytes(bind(once, PARAMS, ci)) == canonical_bytes(once)


def test_bind_leaves_the_template_unchanged(template, ci):
    before = plan_hash(template)
    for seed in range(5):
        bind(template, {**PARAMS, "seed": seed}, ci)
    assert plan_hash(template) == before


def test_row_seed_binds_the_reserved_parameter(template, ci):
    seeds = {
        s: next(t for t in bind(template, {**PARAMS, "seed": s}, ci).tasks if t.id == "sim")
        for s in (0, 41, 2**53 - 1)
    }
    assert {s: t.params.seed for s, t in seeds.items()} == {0: 0, 41: 41, 2**53 - 1: 2**53 - 1}


def test_a_template_without_the_seed_parameter_ignores_the_row_seed(good, plan_of, ci):
    good["parameters"] = {}
    good["tasks"]["sim"]["params"]["seed"] = 5
    plan = plan_of(good)
    assert bind(plan, {"seed": 99}, ci) == plan
    assert [i.code for i in check_job_params(plan, {"seed": 2**53}, ci)] == ["bad-seed"]


def test_different_values_give_different_job_plans(template, ci):
    assert plan_hash(bind(template, PARAMS, ci)) != plan_hash(
        bind(template, {**PARAMS, "seed": 8}, ci)
    )


def test_bind_refuses_a_block_that_does_not_fit(template, ci):
    with pytest.raises(ParamsInvalid) as info:
        bind(template, {"seed": 1, "parameters": {"n": "x", "tags": []}}, ci)
    assert [i.code for i in info.value.issues] == ["parameter-type-mismatch"]


def test_every_fault_is_reported_at_once(template, ci):
    params = {"seed": -1, "parameters": {"n": True, "colour": "red"}, "class_override": "medium"}
    assert [(i.code, i.path) for i in check_job_params(template, params, ci)] == [
        ("unknown-class", "/class_override"),
        ("unknown-parameter", "/parameters/colour"),
        ("parameter-type-mismatch", "/parameters/n"),
        ("unbound-parameter", "/parameters/tags"),
        ("bad-seed", "/seed"),
    ]


def test_a_long_value_is_refused_as_plan_too_large(template, ci):
    params = {**PARAMS, "parameters": {"n": 1, "tags": ["x" * ci.max_plan_bytes]}}
    (issue,) = check_job_params(template, params, ci)
    assert (issue.code, issue.path) == ("plan-too-large", "")
    with pytest.raises(ParamsInvalid):
        bind(template, params, ci)


def test_a_class_of_the_profile_is_accepted(template, ci):
    for name in ci.classes:
        assert check_job_params(template, {**PARAMS, "class_override": name}, ci) == ()


@pytest.mark.parametrize(
    ("params", "path"),
    [
        ({"seed": 1, "parameters": [1]}, "/parameters"),
        ({"seed": 1, "parameters": "x"}, "/parameters"),
        ({"seed": 1, "parameters": 0}, "/parameters"),
        ([], ""),
        (None, ""),
        ("seed", ""),
    ],
)
def test_a_malformed_row_block_is_reported_not_raised(template, ci, params, path):
    issues = check_job_params(template, params, ci)
    assert [(i.code, i.path) for i in issues] == [("params-malformed", path)]
    with pytest.raises(ParamsInvalid):
        bind(template, params, ci)


@pytest.fixture
def text_template(good, plan_of):
    good["parameters"] = {"seed": "seed", "note": "string", "tags": "string-list"}
    good["tasks"]["sim"]["params"]["args"] = {"note": {"param": "note"}}
    good["tasks"]["agg"]["params"]["args"] = {"blocks": {"param": "tags"}}
    return plan_of(good)


@pytest.mark.parametrize(
    ("value", "code", "path"),
    [
        ("é", "not-nfc", "/parameters/note"),
        ("\ud800", "lone-surrogate", "/parameters/note"),
        (["ok", "é"], "not-nfc", "/parameters/tags/1"),
        (["\udfff"], "lone-surrogate", "/parameters/tags/0"),
    ],
)
def test_a_string_value_the_loader_would_refuse_is_refused(text_template, ci, value, code, path):
    key = "tags" if isinstance(value, list) else "note"
    row = {"seed": 1, "parameters": {"note": "a", "tags": ["a"], key: value}}
    issues = check_job_params(text_template, row, ci)
    assert [(i.code, i.path) for i in issues] == [(code, path)]


def test_one_logical_text_binds_to_one_plan_hash(text_template, ci):
    row = {"seed": 1, "parameters": {"note": "\u00e9", "tags": ["a"]}}
    other = {"seed": 1, "parameters": {"note": "e\u0301", "tags": ["a"]}}
    assert check_job_params(text_template, row, ci) == ()
    with pytest.raises(ParamsInvalid):
        bind(text_template, other, ci)
