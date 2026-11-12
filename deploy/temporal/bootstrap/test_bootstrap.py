"""The bootstrap against a Temporal dev server that starts without namespace `wdf`: it creates every
item once, a second run changes nothing, and `check` tells the two states apart."""

import asyncio
import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest
from google.protobuf.duration_pb2 import Duration
from temporalio.api.namespace.v1 import NamespaceConfig
from temporalio.api.workflowservice.v1 import UpdateNamespaceRequest
from temporalio.client import Client, ScheduleUpdate
from wdf_artifactreaper.schedule import SCHEDULE_ID
from wdf_workflow.contract import naming

pytestmark = pytest.mark.integration

_spec = importlib.util.spec_from_file_location(
    "temporal_bootstrap", Path(__file__).with_name("bootstrap.py")
)
bootstrap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bootstrap)

ITEMS = (
    ["namespace: wdf", "retention: 24h"]
    + [f"search_attribute: {a} {t}" for a, t in naming().search_attributes.items()]
    + [f"schedule: {SCHEDULE_ID} in wdf"]
)


@pytest.fixture(scope="module")
def temporal(t1):
    """One dev server for this module, with only its default namespace."""
    with t1.container(
        "temporalio/temporal",
        ports={0: 7233},
        command=("server", "start-dev", "--ip", "0.0.0.0", "--headless"),
    ) as container:
        container.wait_ready(0)
        address = f"127.0.0.1:{container.host_ports[0]}"
        asyncio.run(answering(address))
        yield address


async def answering(address: str) -> None:
    """Waits until the server answers, which comes after its port opens."""
    for _ in range(120):
        try:
            await Client.connect(address)
            return
        except RuntimeError:
            await asyncio.sleep(0.5)
    raise AssertionError("the Temporal server did not answer")


def run(mode, address, capsys) -> tuple[int, list[str], list[str]]:
    code = asyncio.run(bootstrap.main(mode, address))
    out, err = capsys.readouterr()
    return code, out.splitlines(), err.splitlines()


def states(lines: list[str]) -> dict[str, str]:
    return {
        item: line[len(item) + 1 :] for line in lines for item in ITEMS if line.startswith(item)
    }


def test_apply_twice_then_check(temporal, capsys):
    code, out, err = run("check", temporal, capsys)
    assert (code, out, err) == (1, ["namespace: wdf missing"], ["differs: namespace: wdf"])

    code, out, _ = run("apply", temporal, capsys)
    assert code == 0
    assert states(out) == dict.fromkeys(ITEMS, "created")
    assert out[-1] == f"changed: {len(ITEMS)}"

    code, out, _ = run("apply", temporal, capsys)
    assert code == 0
    assert states(out) == dict.fromkeys(ITEMS, "unchanged")
    assert out[-1] == "changed: 0"

    assert run("check", temporal, capsys)[0] == 0


def test_drift_is_found_and_repaired(temporal, capsys):
    """A changed retention and a changed Schedule are reported by check and converged by apply,
    and a paused Schedule stays paused. It applies first, so it holds when run alone."""

    async def connect():
        client = await Client.connect(temporal, namespace="wdf")
        return client, client.get_schedule_handle(SCHEDULE_ID)

    async def drift():
        client, handle = await connect()
        await handle.pause()

        def slower(current):
            spec = current.description.schedule.spec
            spec.intervals[0].every = timedelta(hours=2)
            return ScheduleUpdate(schedule=current.description.schedule)

        await handle.update(slower)
        await client.workflow_service.update_namespace(
            UpdateNamespaceRequest(
                namespace="wdf",
                config=NamespaceConfig(
                    workflow_execution_retention_ttl=Duration(seconds=2 * 86400)
                ),
            )
        )

    async def paused() -> bool:
        return (await (await connect())[1].describe()).schedule.state.paused

    assert run("apply", temporal, capsys)[0] == 0
    asyncio.run(drift())
    code, _, err = run("check", temporal, capsys)
    assert code == 1
    assert err == ["differs: retention: 24h", f"differs: schedule: {SCHEDULE_ID} in wdf"]

    code, out, _ = run("apply", temporal, capsys)
    found = states(out)
    assert (found["retention: 24h"], found[f"schedule: {SCHEDULE_ID} in wdf"]) == (
        "updated from 48h",
        "updated",
    )
    assert out[-1] == "changed: 2"
    assert run("check", temporal, capsys)[0] == 0
    assert asyncio.run(paused())
