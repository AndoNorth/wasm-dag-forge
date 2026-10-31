import json

from wdf_keys import compile_cache_key
from wdf_plan import bind


def test_batch_shares_one_cache_key(good, plan_of, ci):
    good["parameters"] = {"seed": "seed", "n": "integer"}
    good["tasks"]["sim"]["params"]["args"] = {"n": {"param": "n"}}
    template = plan_of(good)
    builds = set()
    keys = set()
    for job in range(1000):
        bound = bind(template, {"seed": job, "parameters": {"n": job * 3}}, ci)
        build = next(t for t in bound.tasks if t.type == "compile")
        builds.add(json.dumps(build.model_dump(mode="json"), sort_keys=True))
        source = build.params.source
        keys.add(compile_cache_key(source.sha256, "c" * 64, "0.1.0", "wit-1", build.params.entry))
    assert len(builds) == 1
    assert len(keys) == 1
