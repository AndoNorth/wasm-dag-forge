# Design spikes

Short durable summaries of the spikes run before the epics were executed.
Each spike was run on 2026-10-27, and its results were applied to the epics.

| Spike | Question | Result | Decision |
| --- | --- | --- | --- |
| [Temporal](temporal.md) (S3, S3b) | Does the workflow design hold on Temporal with the Python SDK, and what does it need at 100,000 jobs? | Five of six thresholds pass and memory growth fails, then traced to two history caches that never evict at default sizes | Keep the workflow design, set the two cache keys, size Temporal at 1 CPU and 1 GiB |
| [Broker client](broker-client.md) (S4) | Does a thin qpid-proton wrapper give the counting retry, dead-letter reason and byte-identical replay on RabbitMQ 4.4.0? | All five checks pass, 35 of 35 assertions | AMQP 1.0 with the proton wrapper, no fallback |
| [Object store](object-store.md) (S5) | Which S3-compatible store replaces MinIO? | SeaweedFS 4.51 and RustFS 1.0.4 both pass 14 of 14 and refuse a wrong-role write | SeaweedFS 4.51 by the ordering rule, RustFS 1.0.4 as the documented alternative |
