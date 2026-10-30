# Design spikes

Short durable summaries of the spikes run before the epics were executed.
Spikes S3 to S5 were run on 2026-10-27 and S2 on 2026-10-29, and the results were applied to the epics.

| Spike | Question | Result | Decision |
| --- | --- | --- | --- |
| [Temporal](temporal.md) (S3, S3b) | Does the workflow design hold on Temporal with the Python SDK, and what does it need at 100,000 jobs? | Five of six thresholds pass and memory growth fails, then traced to two history caches that never evict at default sizes | Keep the workflow design, set the two cache keys, size Temporal at 1 CPU and 1 GiB |
| [Broker client](broker-client.md) (S4) | Does a thin qpid-proton wrapper give the counting retry, dead-letter reason and byte-identical replay on RabbitMQ 4.4.0? | All five checks pass, 35 of 35 assertions | AMQP 1.0 with the proton wrapper, no fallback |
| [Object store](object-store.md) (S5) | Which S3-compatible store replaces MinIO? | SeaweedFS 4.51 and RustFS 1.0.4 both pass 14 of 14 and refuse a wrong-role write | SeaweedFS 4.51 by the ordering rule, RustFS 1.0.4 as the documented alternative |
| [Footprint](S2-footprint.md) (S2) | Does the `ci` preset fit a 7.8 GiB host with its nodes at their limits, and what host does each preset need? | Fail: the shipped `ci` nodes cannot admit the sized compile and aggregate pods, and 3.6 GiB of stand-ins leave the host at its 1 GiB floor | Resize the nodes (applied in `deploy/presets/`), `ci` runs on a CI runner, `dev` on a 16 GiB VM, `soak` on a 20 GiB VM, and this laptop runs only reduced clusters |
