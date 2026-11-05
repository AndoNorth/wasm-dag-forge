"""The key builders produce the keys of the layout, refuse what it refuses, and never collide."""

import hashlib

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from wdf_artifactclient.keys import InvalidKey, attempt_key, bucket_of, content_key, uri_of
from wdf_artifactindex.reference import validate
from wdf_events import layout
from wdf_events.models import ProducerRef

JOB = "123e4567-e89b-42d3-a456-426614174000"
OTHER_JOB = "123e4567-e89b-42d3-a456-426614174001"
PRODUCER = ProducerRef(job=JOB, task="sim-0", attempt="r0a1")
SHA = hashlib.sha256(b"bytes").hexdigest()

KINDS = layout()["kinds"]
CONTENT_KINDS = [k for k, v in KINDS.items() if v["class"] == "content-addressed"]
ATTEMPT_KINDS = [k for k, v in KINDS.items() if v["class"] == "attempt-scoped"]


def build(kind: str, producer: ProducerRef = PRODUCER, name: str = "out.bin", **extra) -> str:
    wanted = KINDS[kind]["key"]
    values = {
        "tenant": "acme",
        "batch": "b1",
        "seed": "7" if "<seed>" in wanted else None,
        "segment": "s1" if "<segment>" in wanted else None,
    } | extra
    return attempt_key(kind, name, producer, **values)


def reference_of(kind: str, key: str, producer: ProducerRef | None = None, name: str = "out.bin"):
    data = {"uri": uri_of(kind, key), "sha256": SHA, "size": 5, "name": name, "kind": kind}
    return data if producer is None else data | {"producer": producer.model_dump()}


@pytest.mark.parametrize("kind", CONTENT_KINDS)
def test_a_content_key_is_the_layout_template_of_the_hash(kind):
    key = content_key(kind, SHA)
    assert key == f"sha256/{SHA[:2]}/{SHA}"
    assert validate(reference_of(kind, key)).uri == f"s3://{bucket_of(kind)}/{key}"


@pytest.mark.parametrize("kind", ATTEMPT_KINDS)
def test_every_attempt_scoped_key_passes_the_reference_validation(kind):
    key = build(kind)
    assert validate(reference_of(kind, key, PRODUCER)).kind == kind


def test_the_keys_of_each_class_follow_their_templates():
    assert build("result") == f"acme/b1/{JOB}/7/sim-0/r0a1/out.bin"
    assert build("summary") == f"acme/b1/aggregate/{JOB}/sim-0/r0a1/out.bin"
    assert build("checkpoint") == f"acme/b1/{JOB}/7/sim-0/r0a1/s1/out.bin"


def test_a_seed_may_be_a_number():
    assert build("result", seed=0) == f"acme/b1/{JOB}/0/sim-0/r0a1/out.bin"


REFUSED = [
    ("result", {"seed": None}),
    ("summary", {"seed": "7"}),
    ("result", {"segment": "s1"}),
    ("checkpoint", {"segment": None}),
    ("result", {"tenant": "a/b"}),
    ("result", {"tenant": ""}),
    ("result", {"batch": ".."}),
    ("result", {"seed": "7/8"}),
    ("result", {"name": "a/b"}),
    ("result", {"name": ".."}),
    ("result", {"name": ""}),
    ("result", {"name": "x" * 256}),
    ("nonsense", {}),
    ("component", {}),
]


@pytest.mark.parametrize(("kind", "extra"), REFUSED)
def test_what_the_layout_refuses_is_refused(kind, extra):
    values = {"name": "out.bin", "tenant": "acme", "batch": "b1", "seed": "7", "segment": None}
    values |= extra
    with pytest.raises(InvalidKey):
        attempt_key(kind, values.pop("name"), PRODUCER, **values)


@pytest.mark.parametrize(
    "producer",
    [
        {"job": "not-a-uuid", "task": "t", "attempt": "r0a1"},
        {"job": JOB, "task": "T/x", "attempt": "r0a1"},
        {"job": JOB, "task": "t", "attempt": "1"},
    ],
)
def test_a_producer_outside_the_pattern_is_refused(producer):
    with pytest.raises(InvalidKey):
        attempt_key("result", "n", producer, "t", "b", "1")


@pytest.mark.parametrize("sha256", ["", "A" * 64, "a" * 63, "g" * 64])
def test_a_content_key_needs_a_lowercase_hex_hash(sha256):
    with pytest.raises(InvalidKey):
        content_key("component", sha256)


def test_an_attempt_scoped_kind_has_no_content_key():
    with pytest.raises(InvalidKey):
        content_key("result", SHA)


text = st.text(
    alphabet=st.characters(blacklist_characters="/", blacklist_categories=("Cc", "Cs")),
    min_size=1,
    max_size=12,
).filter(lambda value: value not in (".", ".."))
producers = st.builds(
    ProducerRef,
    job=st.uuids().map(str),
    task=st.from_regex(r"[a-z0-9][a-z0-9._-]{0,15}", fullmatch=True),
    attempt=st.from_regex(r"r[0-9]{1,3}a[0-9]{1,3}", fullmatch=True),
)
writes = st.fixed_dictionaries(
    {
        "kind": st.sampled_from(ATTEMPT_KINDS),
        "producer": producers,
        "name": text,
        "tenant": text,
        "batch": text,
        "seed": text,
        "segment": text,
    }
)


def uri_for(write: dict) -> str:
    wanted = KINDS[write["kind"]]["key"]
    key = attempt_key(
        write["kind"],
        write["name"],
        write["producer"],
        write["tenant"],
        write["batch"],
        write["seed"] if "<seed>" in wanted else None,
        write["segment"] if "<segment>" in wanted else None,
    )
    return uri_of(write["kind"], key)


@settings(max_examples=300, deadline=None)
@given(first=writes, second=writes)
def test_distinct_producers_never_share_a_key(first, second):
    """Two writes by different jobs, tasks or attempts never land on one object."""
    if first["producer"] != second["producer"]:
        assert uri_for(first) != uri_for(second)
