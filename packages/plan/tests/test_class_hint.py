from wdf_plan import class_hint, stats


def hint_for(good, plan_of, profile_of, timeout):
    good["tasks"]["agg"]["run_timeout_s"] = timeout
    ci = profile_of("ci")
    return class_hint(stats(plan_of(good), ci), ci)


def test_class_hint_boundary(good, plan_of, profile_of):
    # The chain is 1,200 + 1,200 s before the aggregate, and the threshold is 3,600 s.
    assert hint_for(good, plan_of, profile_of, 1200) == "short"
    assert hint_for(good, plan_of, profile_of, 1201) == "long"


def test_the_hint_is_a_class_of_the_profile(good, plan_of, profile_of):
    assert hint_for(good, plan_of, profile_of, 600) in profile_of("ci").classes
