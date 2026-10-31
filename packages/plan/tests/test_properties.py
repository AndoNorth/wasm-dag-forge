import json

from hypothesis import given, settings
from hypothesis import strategies as st
from wdf_plan import canonical_bytes, plan_hash

HASH = "a" * 64
SOURCE = {
    "uri": f"s3://wdf-sources/sha256/aa/{HASH}",
    "sha256": HASH,
    "size": 2048,
    "name": "bundle.tar.gz",
    "kind": "source",
}
NAMES = st.from_regex(r"[a-z0-9][a-z0-9._-]{0,10}", fullmatch=True)


@st.composite
def documents(draw):
    """A valid document of one to two builds, simulations over them and aggregates over those."""
    builds, sims, aggs = (draw(st.integers(lo, hi)) for lo, hi in ((1, 2), (1, 4), (0, 2)))
    ids = draw(
        st.lists(NAMES, unique=True, min_size=builds + sims + aggs, max_size=builds + sims + aggs)
    )
    build_ids, sim_ids, agg_ids = ids[:builds], ids[builds : builds + sims], ids[builds + sims :]
    tasks = {
        b: {"type": "compile", "params": {"source": SOURCE, "entry": "sim"}} for b in build_ids
    }
    for index, sim in enumerate(sim_ids):
        tasks[sim] = {
            "type": "simulate",
            "inputs": [{"task": draw(st.sampled_from(build_ids)), "output": "component"}],
            "depends_on": draw(st.lists(st.sampled_from(["", *sim_ids[:index]]), unique=True)),
            "cpu": draw(st.sampled_from([0.25, 0.125, 1])),
            "memory_mib": draw(st.integers(1, 384)),
            "run_timeout_s": draw(st.integers(1, 3600)),
            "retries": draw(st.integers(0, 3)),
            "params": {
                "workload": "pi",
                "args": {"n": draw(st.integers(0, 99))},
                "seed": draw(st.integers(0, 99)),
            },
        }
        tasks[sim]["depends_on"] = [d for d in tasks[sim]["depends_on"] if d]
    for agg in agg_ids:
        tasks[agg] = {
            "type": "aggregate",
            "inputs": [
                {"task": s, "output": "result"}
                for s in draw(st.lists(st.sampled_from(sim_ids), min_size=1, unique=True))
            ],
            "cpu": 1,
            "memory_mib": 512,
            "run_timeout_s": 600,
            "params": {"aggregation": "digest_list"},
        }
    return {"schema": "urn:wdf:schema:job:1.0", "tasks": tasks}


def reshuffled(value, rnd):
    """The same JSON value with every object's keys in another order."""
    if isinstance(value, dict):
        keys = list(value)
        rnd.shuffle(keys)
        return {k: reshuffled(value[k], rnd) for k in keys}
    if isinstance(value, list):
        return [reshuffled(v, rnd) for v in value]
    return value


class TestProperty:
    """The property tests, so that `pytest -k property` selects them."""

    @settings(max_examples=60, deadline=None)
    @given(documents(), st.randoms(use_true_random=False), st.sampled_from([None, 0, 2, 4]))
    def test_hash_ignores_key_order_and_whitespace(self, plan_of, doc, rnd, indent):
        spelled = json.dumps(
            reshuffled(doc, rnd), indent=indent, separators=(", ", ": ") if indent is None else None
        )
        spaced = (" \n\t" + spelled + "\n ").encode()
        a, b = plan_of(doc), plan_of(spaced)
        assert canonical_bytes(a) == canonical_bytes(b)
        assert plan_hash(a) == plan_hash(b)

    @settings(max_examples=60, deadline=None)
    @given(documents(), st.randoms(use_true_random=False))
    def test_order_is_topological_and_stable(self, plan_of, doc, rnd):
        plan = plan_of(doc)
        position = {t.id: i for i, t in enumerate(plan.tasks)}
        assert sorted(position) == sorted(doc["tasks"])
        for task in plan.tasks:
            assert all(position[d] < position[task.id] for d in task.depends_on)
        # Equal inputs give equal order, however the tasks are listed.
        shuffled = {**doc, "tasks": reshuffled(doc["tasks"], rnd)}
        assert [t.id for t in plan_of(shuffled).tasks] == [t.id for t in plan.tasks]
        assert [t.id for t in plan_of(doc).tasks] == [t.id for t in plan.tasks]
