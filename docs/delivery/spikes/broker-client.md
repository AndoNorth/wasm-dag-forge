# Spike: AMQP 1.0 client wrapper over qpid-proton (S4)

Run on 2026-10-27.
Owner: the [broker epic](https://github.com/AndoNorth/wasm-dag-forge/issues/7).

## Question

Does a thin Python wrapper over `python-qpid-proton` give the broker behaviour the intake design relies on, over AMQP 1.0 with CloudEvents structured JSON messages, against RabbitMQ 4.4.0?
Five checks decide it: publish with the settlement outcome observed, exactly one message per credit, a counting retry, dead-letter on the sixth failed delivery with the reason set in the same settle, and a byte-identical body after dead-letter and replay.
If any check failed, the fallback was a thin AMQP 0-9-1 client behind the same interface.

## Environment and versions

| Item | Value |
| --- | --- |
| Broker | RabbitMQ 4.4.0, image `rabbitmq:4.4-management`, single node, 1 GiB memory cap, ports on 127.0.0.1 |
| Client | `python-qpid-proton` 0.40.0 |
| Python | 3.14.6 in a scratch venv created with `uv` 0.11.21 |
| Protocol | AMQP 1.0, SASL PLAIN, `guest` user on 127.0.0.1 |
| Peak broker memory | 154.6 MiB |

Topology: direct exchange `wdf.intake`, quorum queues `intake.t1.high` and `intake.t1.low` with `x-delivery-limit=5`, dead-letter exchange `wdf.dlx` (strategy `at-least-once`, overflow `reject-publish`) fanning out to quorum queue `intake.dlq`, plus a queue limited to three messages for the queue-full case.
Messages are CloudEvents in structured mode: one AMQP `data` section holding JSON with a non-ASCII field, `content-type: application/cloudevents+json; charset=UTF-8`, durable, no application-properties.

## Method

- A 130-line wrapper exposes `publish`, `consumer`, `pull(n)`, `drain(n)`, and per-delivery `ack`, `retry` and `dead_letter(reason)`.
- One script runs the five checks with 35 assertions, including a negative control that proves the byte-identity check can fail.
- Broker queue counts are polled because the management metrics lag by several seconds.
- Byte identity compares the raw encoded properties, application-properties and body sections, not decoded fields.

## Results

| Check | Result | Evidence |
| --- | --- | --- |
| 1 Publish outcome observed | pass | routable `accepted`, unroutable key `released`, full queue `rejected` (4 accepted at limit 3, then 4 rejected) |
| 2 One message per credit | pass | credit 1 gave 1 message, credit 2 gave 2, none extra after a 2 s wait, drain on an empty queue returned 0 immediately |
| 3 Retry counts toward the limit | pass | `delivery_count` over four retries: 0, 1, 2, 3, while the control with `delivery-failed=false` stayed 0, 0, 0, 0 |
| 4 Dead-letter on the sixth failed delivery | pass | six deliveries with counts 0 to 5, no seventh delivery, ack on the sixth still succeeds with an empty DLQ |
| 5 Byte-identical after dead-letter and replay | pass | 487 bytes identical as sent, at intake, in the DLQ and after replay |

Three consecutive runs gave 35 of 35 assertions, and runs 1 and 3 are identical after normalising timestamps.
No fallback is triggered.

## Findings that change the design

1. The stock proton receiver over-pulls.
   With `create_receiver(queue, credit=1)` the built-in flow control tops credit up after each message, and one accept led to two unrequested deliveries (broker state ready 2, unacked 2 where ready 3 and unacked 1 was correct).
   The client library creates the receiver with credit 0 and only calls `flow(n)` or `drain(n)`, and must not use `receive()` or `prefetch`.
2. A dead-letter by delivery limit carries no application reason, only the broker's `x-last-death-reason=delivery_limit`.
   The reason code reads `x-opt-wdf-reason` if present and `x-last-death-reason` otherwise.
   A consumer that wants an application reason on a limit death must check `delivery_count` and call `dead_letter(reason)` itself on the sixth delivery.
3. An application dead-letter has broker reason `rejected`, so the `x-opt-wdf-reason` annotation, not the broker reason, is the discriminator.
4. The broker adds `x-routing-key` and `x-exchange` to any message it routes, replayed ones included.
   Replay assertions must check for no `x-*death*` and no `x-opt-wdf-*` annotations, not for no annotations.
5. The replay builder rebuilds a message from the bare sections only, with no annotations carried.
   Resending the received object as it is (the negative control) carries stale `x-first-death-*`, `x-last-death-*`, `x-opt-deaths` and `x-opt-wdf-reason`.
6. A missing queue or exchange is a refused link attach (`LinkDetached`, `amqp:not-found`), not an outcome, so the client library must map it as a topology bug and not a retryable condition.
7. `modified` with `delivery-failed=false`, which the official client's `requeue_with_annotations` sends, never reaches the delivery limit.
   The wrapper sets `delivery.local.failed` itself.
8. Queue metrics lag, so any regression test asserting queue counts must poll.

## Caveats and what was not measured

- Connection drop or broker restart while a message is held, reconnect behaviour, and whether a restart counts as a failed delivery.
- Concurrent consumers on one queue and many queues on one connection.
- Throughput, large messages, TLS, and authentication other than the default `guest` user.
- Other RabbitMQ versions.
  An earlier scout showed 4.3.7 and 4.4.0 agree on the wire, and delivery counting changed in 4.3, so the scripts are the regression suite on every image bump.
- Other Python or proton versions.
  The last proton release was May 2025, so the wrapper's use of `delivery.local.failed`, `delivery.local.undeliverable` and `Handler.on_delivery` is the part to watch.
- Messages with application-properties, since only the structured shape ran.
- The AMQP 0-9-1 fallback was not exercised.
  An earlier scout showed that `basic.nack(requeue=true)` never counts toward the delivery limit there, so a counting retry would need a different mechanism.

## Decision

Proceed with AMQP 1.0 and the proton wrapper.
The wrapper is the starting point for the broker client library, and the check script is the starting point for the broker regression suite.
