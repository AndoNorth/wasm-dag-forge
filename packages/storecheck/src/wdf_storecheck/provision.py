"""Creates the role identities of contracts/store/roles.yaml on a store.

Both products take the same IAM policy document per role.
SeaweedFS reads its identities and policies from a file at start.
RustFS takes them through its signed admin API.
"""

import hashlib
import json
import secrets
from urllib.parse import urlencode

import urllib3
import yaml
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

from .models import CONTRACT
from .s3 import REGION, Creds

OBJECT_ACTIONS = {
    "get": ["s3:GetObject"],
    "put": ["s3:PutObject"],
    "multipart": ["s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
    "delete": ["s3:DeleteObject"],
}
BUCKET_ACTIONS = {"list": ["s3:ListBucket"], "multipart": ["s3:ListBucketMultipartUploads"]}
# The identity that provisions the buckets, which both products treat as a full administrator.
ADMIN = "admin"
SEAWEEDFS_ADMIN_ACTIONS = ["Admin", "Read", "Write", "List", "Tagging"]
LEGACY_ACTIONS = {
    "get": "Read",
    "list": "List",
    "put": "Write",
    "multipart": "Write",
    "delete": "Write",
}


def roles() -> dict[str, dict[str, list[str]]]:
    return yaml.safe_load((CONTRACT / "roles.yaml").read_text())["roles"]


def generate_credentials() -> dict[str, Creds]:
    """One fresh credential per role of the contract."""
    return {
        role: Creds(f"{role}-{secrets.token_hex(4)}", secrets.token_hex(16)) for role in roles()
    }


def policy(grants: dict[str, list[str]]) -> dict:
    """The IAM policy that allows exactly the object actions a role holds on each bucket."""
    statements = []
    for bucket, actions in sorted(grants.items()):
        for table, resource in ((OBJECT_ACTIONS, f"{bucket}/*"), (BUCKET_ACTIONS, bucket)):
            allowed = sorted({a for action in actions for a in table.get(action, [])})
            if allowed:
                statements.append(
                    {"Effect": "Allow", "Action": allowed, "Resource": [f"arn:aws:s3:::{resource}"]}
                )
    return {"Version": "2012-10-17", "Statement": statements}


def seaweedfs_config(credentials: dict[str, Creds], legacy: bool = False) -> dict:
    """The identities file of SeaweedFS.

    By default each role gets a policy of exactly its grants.
    With `legacy` each role gets the coarse Read, List and Write actions per bucket instead,
    where Write also covers deleting objects and replacing the lifecycle rules of the bucket.
    """
    identities, policies = [], []
    for role, creds in credentials.items():
        identity = {
            "name": role,
            "credentials": [{"accessKey": creds.access_key, "secretKey": creds.secret_key}],
        }
        grants = roles().get(role, {})
        if role == ADMIN:
            identity["actions"] = SEAWEEDFS_ADMIN_ACTIONS
        elif legacy:
            identity["actions"] = sorted(
                {f"{LEGACY_ACTIONS[a]}:{b}" for b, acts in grants.items() for a in acts}
            )
        else:
            identity["policyNames"] = [role]
            policies.append({"name": role, "document": policy(grants)})
        identities.append(identity)
    return {"identities": identities, "policies": policies}


def write_seaweedfs_config(path, credentials: dict[str, Creds], legacy: bool = False) -> None:
    path.write_text(json.dumps(seaweedfs_config(credentials, legacy), indent=2) + "\n")


def _admin_call(endpoint: str, root: Creds, path: str, params: dict, body: bytes = b"") -> None:
    url = f"{endpoint}/rustfs/admin/v3/{path}?{urlencode(params)}"
    request = AWSRequest(
        method="PUT",
        url=url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-amz-content-sha256": hashlib.sha256(body).hexdigest(),
        },
    )
    SigV4Auth(Credentials(*root), "s3", REGION).add_auth(request)
    reply = urllib3.PoolManager().request("PUT", url, headers=dict(request.headers), body=body)
    if reply.status != 200:
        raise RuntimeError(f"admin call {path} returned {reply.status}: {reply.data[:200]!r}")


def provision_rustfs(endpoint: str, credentials: dict[str, Creds]) -> None:
    """Creates a user and a canned policy per role, signing with the root credentials in `admin`."""
    root = credentials[ADMIN]
    for role, creds in credentials.items():
        if role == ADMIN:
            continue
        user = json.dumps({"secretKey": creds.secret_key, "status": "enabled"}).encode()
        _admin_call(endpoint, root, "add-user", {"accessKey": creds.access_key}, user)
        document = json.dumps(policy(roles()[role])).encode()
        _admin_call(endpoint, root, "add-canned-policy", {"name": role}, document)
        _admin_call(
            endpoint,
            root,
            "set-user-or-group-policy",
            {"policyName": role, "userOrGroup": creds.access_key, "isGroup": "false"},
        )
