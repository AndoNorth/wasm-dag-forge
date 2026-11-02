"""Prints the digest and the sizes of the images under test, unpacked and compressed."""

import json
import subprocess
import sys
import urllib.request

from cg import figure

ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


def fetch(url, token=None, accept=ACCEPT):
    request = urllib.request.Request(url, headers={"Accept": accept})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def compressed_bytes(repository, digest):
    scope = f"repository:{repository}:pull"
    token = fetch(f"https://auth.docker.io/token?service=registry.docker.io&scope={scope}")["token"]
    base = f"https://registry-1.docker.io/v2/{repository}/manifests"
    manifest = fetch(f"{base}/{digest}", token)
    if "manifests" in manifest:
        pick = next(
            m
            for m in manifest["manifests"]
            if m["platform"]["os"] == "linux" and m["platform"]["architecture"] == "amd64"
        )
        manifest = fetch(f"{base}/{pick['digest']}", token)
    return manifest["config"]["size"] + sum(layer["size"] for layer in manifest["layers"])


def main():
    for name, repository, reference in (
        ("timescaledb", "timescale/timescaledb", sys.argv[1]),
        ("seaweedfs", "chrislusf/seaweedfs", sys.argv[2]),
    ):
        digest = reference.split("@", 1)[1]
        size = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Size}}", reference],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        figure(f"image.{name}.digest", digest)
        figure(f"image.{name}.unpacked_mib", f"{int(size) / 1048576:.0f}")
        figure(
            f"image.{name}.compressed_mib", f"{compressed_bytes(repository, digest) / 1048576:.0f}"
        )


if __name__ == "__main__":
    main()
