"""Bootstraps the Temporal server for the platform: namespace `wdf` with its retention, the search
attributes of the naming contract, and the reaper's Schedule in that namespace.
Usage: bootstrap.py apply|check ADDRESS
`apply` is idempotent and prints one line per item with what it did, ending with the count of
changes, and `check` exits 1 when the server differs.
"""

import asyncio
import dataclasses
import sys
from collections.abc import Awaitable, Callable
from datetime import timedelta

from google.protobuf.duration_pb2 import Duration
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.namespace.v1 import NamespaceConfig
from temporalio.api.operatorservice.v1 import (
    AddSearchAttributesRequest,
    ListSearchAttributesRequest,
)
from temporalio.api.workflowservice.v1 import (
    DescribeNamespaceRequest,
    RegisterNamespaceRequest,
    UpdateNamespaceRequest,
)
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode
from wdf_artifactreaper.schedule import SCHEDULE_ID, definition, ensure_schedule
from wdf_workflow.contract import naming

RETENTION = timedelta(days=1)
TYPES = {
    "keyword": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    "datetime": IndexedValueType.INDEXED_VALUE_TYPE_DATETIME,
}
# A new namespace reaches the frontend's namespace cache some seconds after it is registered.
NAMESPACE_WAIT_S = 60


def hours(span: timedelta) -> str:
    return f"{int(span.total_seconds()) // 3600}h"


async def known(call: Callable[[], Awaitable]):
    """Retries a call that fails only because the namespace is not cached yet."""
    for _ in range(NAMESPACE_WAIT_S - 1):
        try:
            return await call()
        except RPCError as error:
            if error.status != RPCStatusCode.NOT_FOUND:
                raise
        await asyncio.sleep(1)
    return await call()


async def namespace(client: Client, name: str, apply: bool) -> list[tuple[str, str]]:
    service = client.workflow_service
    retention = Duration(seconds=int(RETENTION.total_seconds()))
    try:
        described = await service.describe_namespace(DescribeNamespaceRequest(namespace=name))
    except RPCError as error:
        if error.status != RPCStatusCode.NOT_FOUND:
            raise
        if not apply:
            return [(f"namespace: {name}", "missing")]
        await service.register_namespace(
            RegisterNamespaceRequest(namespace=name, workflow_execution_retention_period=retention)
        )
        return [(f"namespace: {name}", "created"), (f"retention: {hours(RETENTION)}", "created")]
    current = described.config.workflow_execution_retention_ttl.ToTimedelta()
    if current == RETENTION:
        state = "unchanged"
    elif apply:
        await service.update_namespace(
            UpdateNamespaceRequest(
                namespace=name, config=NamespaceConfig(workflow_execution_retention_ttl=retention)
            )
        )
        state = f"updated from {hours(current)}"
    else:
        state = f"differs: {hours(current)}"
    return [(f"namespace: {name}", "unchanged"), (f"retention: {hours(RETENTION)}", state)]


async def search_attributes(client: Client, name: str, apply: bool) -> list[tuple[str, str]]:
    service = client.operator_service
    listed = await known(
        lambda: service.list_search_attributes(ListSearchAttributesRequest(namespace=name))
    )
    current = listed.custom_attributes
    wanted = naming().search_attributes
    missing = {a: TYPES[t] for a, t in wanted.items() if a not in current}
    if missing and apply:
        await known(
            lambda: service.add_search_attributes(
                AddSearchAttributesRequest(search_attributes=missing, namespace=name)
            )
        )
    lines = []
    for attribute, kind in wanted.items():
        if attribute in missing:
            state = "created" if apply else "missing"
        elif current[attribute] == TYPES[kind]:
            state = "unchanged"
        else:
            # A search attribute's type cannot change in place, so both modes report it.
            state = f"differs: {IndexedValueType.Name(current[attribute])}"
        lines.append((f"search_attribute: {attribute} {kind}", state))
    return lines


async def schedule(client: Client, name: str, apply: bool) -> list[tuple[str, str]]:
    """The reaper's Schedule, compared with its definition while keeping its paused state.

    A namespace that is not cached yet reads as no Schedule, and the create then waits for it."""
    try:
        current = (await client.get_schedule_handle(SCHEDULE_ID).describe()).schedule
    except RPCError as error:
        if error.status != RPCStatusCode.NOT_FOUND:
            raise
        current = None
    if current is not None and current == dataclasses.replace(definition(), state=current.state):
        state = "unchanged"
    elif apply:
        await known(lambda: ensure_schedule(client))
        state = "created" if current is None else "updated"
    else:
        state = "missing" if current is None else "differs"
    return [(f"schedule: {SCHEDULE_ID} in {name}", state)]


CHANGED = ("created", "updated")


async def main(mode: str, address: str) -> int:
    apply = mode == "apply"
    name = naming().namespace
    lines = await namespace(await Client.connect(address), name, apply)
    if lines[0][1] != "missing":
        client = await Client.connect(address, namespace=name)
        lines += await search_attributes(client, name, apply)
        lines += await schedule(client, name, apply)
    for item, state in lines:
        print(f"{item} {state}")
    wrong = [item for item, state in lines if state.startswith(("missing", "differs"))]
    if apply:
        print(f"changed: {sum(state.startswith(CHANGED) for _, state in lines)}")
    for item in wrong:
        print(f"differs: {item}", file=sys.stderr)
    return 1 if wrong else 0


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("apply", "check"):
        print("usage: bootstrap.py apply|check ADDRESS", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main(*sys.argv[1:])))
