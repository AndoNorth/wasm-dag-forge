"""Usage: python -m wdf_artifactclient.faults delete|flip-byte|corrupt-checkpoint
--endpoint URL --credentials FILE --role ROLE --uri URI

Hooks that damage one object of a store on purpose, so a scenario can watch the client fail in a
typed way. Each hook prints what it changed and exits 0, 1 when it could not change it, 2 for a
usage error. `delete` needs the delete grant of the bucket, `flip-byte` the get and put grants,
and `corrupt-checkpoint` takes the `checkpoint.json` uri and flips a byte of its first data object.
Filling a disk is the `disk-full` mode of `python -m wdf_storecheck`, and a store outage is
`scripts/artifacts/store-outage.sh`.
"""

import argparse
import json
import sys

from botocore.exceptions import BotoCoreError, ClientError
from wdf_storecheck.s3 import client, error_code, load_credentials, status

from .checkpoint import METADATA_CAP, METADATA_NAME, Metadata
from .keys import split_uri

STUBBED = ()

USAGE = 2
FLIP = 0xFF


class FaultError(Exception):
    """A hook that could not change the object, reported with exit status 1."""


def locate(uri: str) -> tuple[str, str]:
    bucket, key = split_uri(uri)
    if not uri.startswith("s3://") or not bucket or not key:
        raise ValueError(f"{uri} is not an s3://<bucket>/<key> uri")
    return bucket, key


def delete(s3, uri: str) -> str:
    bucket, key = locate(uri)
    s3.delete_object(Bucket=bucket, Key=key)
    return f"deleted {uri}"


def flip_byte(s3, uri: str) -> str:
    """Inverts the byte in the middle of the object and stores the object again under its key."""
    bucket, key = locate(uri)
    body = bytearray(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    if not body:
        raise FaultError(f"{uri} is empty, so there is no byte to flip")
    # ponytail: the whole object is held in memory, fine for the objects of a scenario
    at = len(body) // 2
    was = body[at]
    body[at] ^= FLIP
    s3.put_object(Bucket=bucket, Key=key, Body=bytes(body))
    return f"flipped byte {at} of {uri} from {was:#04x} to {body[at]:#04x}"


def corrupt_checkpoint(s3, uri: str) -> str:
    """Flips a byte of the data object that a checkpoint names, so its metadata still reads."""
    bucket, key = locate(uri)
    if key.rpartition("/")[2] != METADATA_NAME:
        raise FaultError(f"{uri} is not the {METADATA_NAME} of a checkpoint")
    raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read(METADATA_CAP)
    try:
        document = Metadata.model_validate(json.loads(raw))
    except ValueError as error:
        raise FaultError(f"{uri} is not checkpoint metadata: {error}") from error
    return flip_byte(s3, document.objects[0].uri)


HOOKS = {"delete": delete, "flip-byte": flip_byte, "corrupt-checkpoint": corrupt_checkpoint}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m wdf_artifactclient.faults")
    root.add_argument("hook", choices=sorted(HOOKS))
    root.add_argument("--endpoint", required=True)
    root.add_argument("--credentials", required=True, help="credentials file of the store roles")
    root.add_argument("--role", required=True)
    root.add_argument("--uri", required=True, help="s3://<bucket>/<key> of the object to damage")
    return root


def main(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    try:
        s3 = client(args.endpoint, load_credentials(args.credentials)[args.role])
        locate(args.uri)
    except (ValueError, KeyError) as error:
        print(f"usage: {type(error).__name__}: {error}", file=sys.stderr)
        return USAGE
    try:
        print(HOOKS[args.hook](s3, args.uri))
    except FaultError as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1
    except ClientError as error:
        print(f"failed: {error_code(error)} {status(error)} for {args.uri}", file=sys.stderr)
        return 1
    except BotoCoreError as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
