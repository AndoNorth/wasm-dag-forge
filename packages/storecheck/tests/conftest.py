"""A started store per product, with every role of the contract provisioned."""

import json
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from botocore.exceptions import BotoCoreError, ClientError
from wdf_storecheck.checks import Store
from wdf_storecheck.provision import (
    ADMIN,
    generate_credentials,
    provision_rustfs,
    write_seaweedfs_config,
)
from wdf_storecheck.s3 import Creds, client

PRODUCTS = {"seaweedfs": "chrislusf/seaweedfs", "rustfs": "rustfs/rustfs"}
IDENTITIES = "/etc/seaweedfs/s3.json"


@dataclass
class Running:
    product: str
    endpoint: str
    credentials: dict[str, Creds]
    container_id: str = ""

    def exec_prefix(self) -> list[str]:
        return ["docker", "exec", "-i", self.container_id]

    def store(self) -> Store:
        return Store(self.endpoint, self.credentials)


def wait_until_serving(endpoint: str, admin: Creds, timeout: float = 90) -> None:
    """The published port accepts connections before the store answers, so retry until it does."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            client(endpoint, admin).list_buckets()
            return
        except (BotoCoreError, ClientError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def anonymous_volumes(container_id: str) -> list[str]:
    """The volumes the image declares, which removing a container leaves behind."""
    out = subprocess.run(
        ["docker", "inspect", "--format", "{{json .Mounts}}", container_id],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [m["Name"] for m in json.loads(out) if m["Type"] == "volume"]


@contextmanager
def running(t1, product: str, directory, tmpfs: dict[str, int] | None = None):
    """One container of the product, yielding its endpoint and the credential of every role."""
    credentials = generate_credentials()
    if product == "seaweedfs":
        config = directory / "s3.json"
        write_seaweedfs_config(config, credentials)
        container = t1.container(
            PRODUCTS[product],
            ports={0: 8333},
            command=[
                "server",
                "-s3",
                f"-s3.config={IDENTITIES}",
                "-dir=/data",
                "-volume.max=0",
            ],
            mounts={config: IDENTITIES},
            tmpfs=tmpfs,
        )
    else:
        root = credentials[ADMIN]
        container = t1.container(
            PRODUCTS[product],
            ports={0: 9000},
            command=["/data"],
            env={"RUSTFS_ACCESS_KEY": root.access_key, "RUSTFS_SECRET_KEY": root.secret_key},
            tmpfs=tmpfs,
        )
    volumes: list[str] = []
    try:
        with container as started:
            volumes = anonymous_volumes(started.id)
            endpoint = f"http://127.0.0.1:{started.host_ports[0]}"
            wait_until_serving(endpoint, credentials[ADMIN])
            if product == "rustfs":
                provision_rustfs(endpoint, credentials)
            yield Running(product, endpoint, credentials, started.id)
    finally:
        if volumes:
            subprocess.run(["docker", "volume", "rm", *volumes], capture_output=True)


@pytest.fixture(scope="module", params=sorted(PRODUCTS))
def store(request, t1, tmp_path_factory):
    """One container per product for a test module, provisioned with exactly the contract grants."""
    with running(t1, request.param, tmp_path_factory.mktemp("store")) as started:
        yield started


@pytest.fixture
def start(t1, tmp_path):
    """Starts a container of a product for one test, as a context manager."""
    return lambda product, tmpfs=None: running(t1, product, tmp_path, tmpfs)


@pytest.fixture(scope="module")
def start_capped(t1, tmp_path_factory):
    """Starts a container of a product with a size-capped data volume, for a whole test module."""
    return lambda product, size_mib: running(
        t1, product, tmp_path_factory.mktemp(product), {"/data": size_mib}
    )
