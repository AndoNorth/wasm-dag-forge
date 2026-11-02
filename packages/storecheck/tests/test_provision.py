"""The identities handed to each product follow the contract roles."""

import yaml
from wdf_storecheck.models import CONTRACT
from wdf_storecheck.provision import generate_credentials, policy, roles, seaweedfs_config

GRANTS = yaml.safe_load((CONTRACT / "roles.yaml").read_text())["roles"]


def test_every_role_of_the_contract_gets_a_distinct_credential():
    credentials = generate_credentials()
    assert sorted(credentials) == sorted(GRANTS)
    assert len({c.access_key for c in credentials.values()}) == len(GRANTS)
    assert all(len(c.secret_key) >= 8 for c in credentials.values())


def test_a_policy_names_only_the_actions_of_the_grants():
    document = policy(roles()["compile"])
    actions = {(s["Resource"][0], a) for s in document["Statement"] for a in s["Action"]}
    assert ("arn:aws:s3:::wdf-sources/*", "s3:GetObject") in actions
    assert ("arn:aws:s3:::wdf-sources", "s3:ListBucket") in actions
    assert ("arn:aws:s3:::wdf-components/*", "s3:PutObject") in actions
    assert not any("Delete" in action for _, action in actions)
    assert not any("wdf-results" in resource for resource, _ in actions)


def test_seaweedfs_identities_use_policies_by_default_and_coarse_actions_when_legacy():
    credentials = generate_credentials()
    exact = seaweedfs_config(credentials)
    assert {p["name"] for p in exact["policies"]} == set(GRANTS) - {"admin"}
    submit = next(i for i in exact["identities"] if i["name"] == "submit")
    assert submit["policyNames"] == ["submit"] and "actions" not in submit
    legacy = seaweedfs_config(credentials, legacy=True)
    assert legacy["policies"] == []
    submit = next(i for i in legacy["identities"] if i["name"] == "submit")
    assert submit["actions"] == ["Write:wdf-sources"]
    admin = next(i for i in legacy["identities"] if i["name"] == "admin")
    assert "Admin" in admin["actions"]
