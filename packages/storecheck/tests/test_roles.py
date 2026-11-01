"""The committed store contract agrees with the bucket layout, the actions and the pins."""

import json
from pathlib import Path

import yaml
from wdf_storecheck.models import ACTIONS, ROLES

ROOT = Path(__file__).resolve().parents[3]
STORE = ROOT / "contracts/store"


def load(path: Path):
    return yaml.safe_load(path.read_text())


BUCKETS = load(ROOT / "contracts/artifact/layout.yaml")["buckets"]


def schema_actions() -> set[str]:
    schema = json.loads((STORE / "store.schema.json").read_text())
    return set(
        schema["$defs"]["Roles"]["properties"]["roles"]["additionalProperties"][
            "additionalProperties"
        ]["items"]["enum"]
    )


def test_every_bucket_of_the_layout_has_a_rule():
    rules = load(STORE / "lifecycle.yaml")["buckets"]
    assert sorted(rules) == sorted(BUCKETS)
    assert all(entry["rules"] for entry in rules.values())


def test_every_grant_names_a_bucket_of_the_layout_and_an_action_of_the_schema_vocabulary():
    vocabulary = schema_actions()
    assert vocabulary == set(ACTIONS)
    for role, grants in load(STORE / "roles.yaml")["roles"].items():
        for bucket, actions in grants.items():
            assert bucket in BUCKETS, f"{role} names {bucket}"
            assert set(actions) <= vocabulary, f"{role} on {bucket}"


def test_the_nine_roles_are_all_present():
    assert sorted(load(STORE / "roles.yaml")["roles"]) == sorted(ROLES)
    assert len(ROLES) == 9


def test_only_the_reaper_deletes_and_only_admin_provisions():
    roles = load(STORE / "roles.yaml")["roles"]
    for role, grants in roles.items():
        actions = {a for acts in grants.values() for a in acts}
        assert ("delete" in actions) == (role == "reaper")
        assert bool(actions & {"bucket", "lifecycle"}) == (role == "admin")


def test_sources_and_components_carry_no_age_rule():
    rules = load(STORE / "lifecycle.yaml")["buckets"]
    for bucket in ("wdf-sources", "wdf-components"):
        assert {r["kind"] for r in rules[bucket]["rules"]} == {"abort-incomplete-multipart"}


def test_each_product_image_is_pinned_in_the_versions_file():
    images = load(ROOT / "deploy/versions.yaml")["images"]
    for name, product in load(STORE / "products.yaml")["products"].items():
        assert product["image"] in images, name


def test_seaweedfs_records_the_lifecycle_edit_gap_for_the_writing_roles():
    gaps = load(STORE / "products.yaml")["products"]["seaweedfs"]["known_gaps"]
    assert gaps[0]["gap"] == "lifecycle-edit-by-writer"
    roles = load(STORE / "roles.yaml")["roles"]
    writers = {r for r, g in roles.items() if any("put" in a for a in g.values())}
    assert set(gaps[0]["roles"]) == writers
