"""What the checks share: a client per credential, the credentials file, the outcome of a call."""

import json
from pathlib import Path
from typing import NamedTuple

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError

REGION = "us-east-1"
ALLOWED = "allowed"
REFUSED = "refused"


class Creds(NamedTuple):
    access_key: str
    secret_key: str


def load_credentials(path: Path) -> dict[str, Creds]:
    """Reads `{"<role>": {"access_key": ..., "secret_key": ...}}`, or raises ValueError."""
    try:
        document = json.loads(Path(path).read_text())
        return {role: Creds(c["access_key"], c["secret_key"]) for role, c in document.items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise ValueError(f"{path}: not a credentials file ({error})") from error


def dump_credentials(path: Path, credentials: dict[str, Creds]) -> None:
    document = {role: c._asdict() for role, c in credentials.items()}
    Path(path).write_text(json.dumps(document, indent=2) + "\n")


def client(endpoint: str, creds: Creds | None):
    """A path-style client that signs with `creds`, or sends unsigned requests when it is None."""
    config = Config(
        signature_version=UNSIGNED if creds is None else "s3v4",
        s3={"addressing_style": "path"},
        retries={"max_attempts": 1},
        connect_timeout=5,
        read_timeout=60,
    )
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=REGION,
        aws_access_key_id=creds.access_key if creds else None,
        aws_secret_access_key=creds.secret_key if creds else None,
        config=config,
    )


def error_code(error: Exception) -> str:
    """The S3 error code, empty for an error that never reached the store."""
    return getattr(error, "response", {}).get("Error", {}).get("Code", "")


def status(error: ClientError) -> int:
    return error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)


def outcome(call) -> str:
    """`allowed` when the call returns, `refused` for a 403 AccessDenied, else `error:<code>`."""
    try:
        call()
    except ClientError as error:
        if error_code(error) == "AccessDenied" and status(error) == 403:
            return REFUSED
        return f"error:{error_code(error) or status(error)}"
    return ALLOWED
