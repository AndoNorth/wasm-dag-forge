"""FakePool passes the activity contract on each pool, the contract refuses pool workers that break
it, and the fake's lost-worker outcomes end their attempt in the Temporal timeout they stand for.
Runs on a dev server."""

import asyncio
import uuid

import pytest
from temporalio import activity
from temporalio.worker import Worker
from wdf_workflow.contract import naming
from wdf_workflow.models import TaskInput
from wdf_workflow.testing import (
    ContractCases,
    Die,
    Fail,
    FakePool,
    Hang,
    Interruptible,
    Succeed,
    Zombie,
    activity_contract,
    connect,
    run_task_once,
)

pytestmark = pytest.mark.integration

VECTOR = {"compile": "compile", "simulate": "simulate", "aggregate": "aggregate"}


def cases(task_input, pool: str) -> ContractCases:
    """Three inputs of the pool's vector, told apart by their task attempt."""
    given = TaskInput.model_validate(task_input(VECTOR[pool]))
    attempt = {n: given.model_copy(update={"task_attempt": n}) for n in (1, 2, 3)}
    return ContractCases(
        succeeds=attempt[1], fails=attempt[2], interrupts=attempt[3] if pool == "simulate" else None
    )


def by_attempt(outcomes):
    return lambda task: outcomes[task.task_attempt]


SCRIPT = {1: Succeed(), 2: Fail("STORE_UNAVAILABLE"), 3: Interruptible()}


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.mark.parametrize("pool", ["compile", "simulate", "aggregate"])
def test_fake_pool_passes_activity_contract(temporal, task_input, pool):
    async def body():
        client = await connect(temporal)
        async with FakePool(client, pool, by_attempt(SCRIPT)) as fake:
            await activity_contract(client, fake.queue, cases(task_input, pool))
        return fake.calls

    calls = run(body())
    assert [task.task_attempt for task in calls] == ([1, 2, 3] if pool == "simulate" else [1, 2])
    assert naming().pools[pool] == f"wdf-{pool}"


def contract_failure(temporal, task_input, script, worker=None) -> str:
    """The message of the activity contract run against a simulate worker on a queue of its own."""
    queue = f"wdf-simulate-{uuid.uuid4()}"

    async def body():
        client = await connect(temporal)
        if worker is None:
            serving = FakePool(client, "simulate", by_attempt(script), queue=queue)
        else:
            serving = Worker(client, task_queue=queue, activities=[worker])
        async with serving:
            await activity_contract(client, queue, cases(task_input, "simulate"))

    with pytest.raises(AssertionError) as refused:
        run(body())
    return str(refused.value)


@activity.defn(name="run_task")
async def returns_bytes(task: TaskInput) -> bytes:
    return b"<results/>"


def test_activity_contract_refuses_bytes_instead_of_references(temporal, task_input):
    message = contract_failure(temporal, task_input, None, worker=returns_bytes)
    assert "succeeds: the result is binary/plain, not json/plain" in message


def test_activity_contract_refuses_a_class_outside_the_table(temporal, task_input):
    script = {**SCRIPT, 2: Fail("disk_full")}
    message = contract_failure(temporal, task_input, script)
    assert "fails: unknown-class" in message


def test_activity_contract_refuses_a_cancel_without_operator_interrupt(temporal, task_input):
    script = {**SCRIPT, 3: Hang(checkpoint=True)}
    message = contract_failure(temporal, task_input, script)
    assert "interrupts: the worker raised no ApplicationError" in message


@pytest.mark.parametrize(
    ("outcome", "timeouts", "expected"),
    [
        (Die(), {"heartbeat_s": 2}, "HEARTBEAT"),
        (Hang(), {"start_to_close_s": 2}, "START_TO_CLOSE"),
        (Zombie(after_s=4), {"heartbeat_s": 30, "start_to_close_s": 2}, "START_TO_CLOSE"),
    ],
)
def test_fake_pool_lost_worker_outcomes_time_out(temporal, task_input, outcome, timeouts, expected):
    async def body():
        client = await connect(temporal)
        queue = f"wdf-simulate-{uuid.uuid4()}"
        async with FakePool(client, "simulate", lambda task: outcome, queue=queue):
            task = TaskInput.model_validate(task_input("simulate"))
            return await run_task_once(client, queue, task, **timeouts)

    assert run(body()) == {"timeout": expected}
