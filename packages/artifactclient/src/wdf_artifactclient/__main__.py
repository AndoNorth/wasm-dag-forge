"""Usage: python -m wdf_artifactclient verify --endpoint URL --credentials FILE --role ROLE REF

Exit 0 verified, 1 with `code: <CODE>` (and `cause: <CODE>` for a lost artifact) on stderr, 2 usage.
"""

import argparse
import json
import sys

from pydantic import ValidationError
from wdf_storecheck.s3 import load_credentials

from .client import ArtifactClient, verify
from .errors import ArtifactError, ArtifactLost

STUBBED = ()

USAGE = 2


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m wdf_artifactclient")
    commands = root.add_subparsers(dest="command", required=True)
    one = commands.add_parser(
        "verify", help="read an object through the hash check of its reference"
    )
    one.add_argument("--endpoint", required=True)
    one.add_argument("--credentials", required=True, help="credentials file of the store roles")
    one.add_argument("--role", required=True)
    one.add_argument("--deadline", type=float, help="seconds the read may take")
    one.add_argument("reference", help="a JSON file holding one artifact reference")
    return root


def main(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    try:
        with open(args.reference) as handle:
            reference = json.load(handle)
        credentials = load_credentials(args.credentials)[args.role]
        client = ArtifactClient(args.endpoint, credentials)
    except (OSError, ValueError, KeyError) as error:
        print(f"usage: {type(error).__name__}: {error}", file=sys.stderr)
        return USAGE
    try:
        verify(client, reference, args.deadline)
    except ValidationError as error:
        print(f"usage: not an artifact reference: {error}", file=sys.stderr)
        return USAGE
    except ArtifactError as error:
        print(f"code: {error.code}\ndetail: {error}", file=sys.stderr)
        if isinstance(error, ArtifactLost):
            print(f"cause: {error.cause.code}", file=sys.stderr)
        return 1
    print(f"verified {reference['uri']} {reference['size']} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
