# Spike: cold start of the workload component in the `ci` simulate slot (S6)

Run on 2026-11-08.
Owner: the [workload toolchain epic](https://github.com/AndoNorth/wasm-dag-forge/issues/11), lane S, filed as [spike issue 266](https://github.com/AndoNorth/wasm-dag-forge/issues/266).
Method: [docs/delivery/method.md](../../method.md).
The epic breakdown spec is not merged yet, and this spike gates its `wasmtime` behaviour pin tests.
Throwaway code: [code/](code/), run as `nix develop -c docs/delivery/spikes/S6/code/run.sh`.
Every number below is a `figure <name> <value>` line of that run, and the name is given in backticks beside it.

## Result

Diagram: [cold-start.drawio.svg](cold-start.drawio.svg), a worked example of the first run and the warm-up rule.

**Pass.**
On 3 of 3 runs, a first run of a `noop` guest with an empty module cache completed under `docker run --memory 384m --memory-swap 384m --cpus 0.25`, and so did a run with a warm cache, with no OOM kill.
A first run took 29.0 to 29.6 s and peaked at a working set of 290 to 326 MiB, and a warm run took 0.54 to 0.61 s and peaked at 69 to 70 MiB.
The guest overhead, the peak working set less the 12.9 MiB of guest linear memory, is 277 to 313 MiB on a first run and 57 MiB warm.

One finding changes the runtime library and the workers, and is not part of the pass line.
`wasmtime` does not give the memory of a first-run compile back before the guest starts.
After a cold compile, a guest that held 64 MiB more completed, and one that held 128 MiB more was OOM killed with exit 137.
With a warm cache, the same guest held 256 MiB and completed.
So a guest memory cap computed from the warm overhead is safe only when the guest never runs in the process that compiled it, and "What the numbers change" below proposes the rule.

The fail line was not reached, and its first remedy was measured anyway: neither `wasmtime` 48 option tried lowers the peak of a first run in the capped container.

## Question

Does a first run of the workload component fit the `ci` simulate slot of 250m CPU and 384 MiB (`deploy/presets/slots/ci.yaml`), and what is the guest overhead?
The epic measured about 1.4 s and about 0.4 GiB in `wasmtime` for the first run of an unseen 18 MB component, which is above the slot.

## Environment

| Item | Value |
| --- | --- |
| Host | WSL2, 20,002 MiB `MemTotal` (`host.mem_total_mib`), 8 CPUs (`host.cpus`), kernel 6.6.87.2 (`host.kernel`), Docker 29.1.3 (`host.docker`) |
| Other work on the host | 1 other container running (`host.other_containers`), not touched, 16,275 MiB `MemAvailable` before (`host.mem_available_mib.before`) and 16,603 MiB after (`host.mem_available_mib.after`) |
| Locks | one of the two `wdf-t1` lock files for the whole run, no cluster |
| Containers | one at a time, `--memory 384m --memory-swap 384m --cpus 0.25 --network none`, run as the invoking user, label `wdf.dev/verify-run`, each removed after its run |
| Left behind | none: `docker ps -a --filter label=wdf.dev/verify-run` lists 0 after the run (`containers.left.label_all`) |
| Image | `busybox:1.37.0@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec`, only for `sh`, `cp` and `sleep` |
| Runtime | `wasmtime` 48.0.7 (`tool.wasmtime`) from `nix/wasm-tools.nix`, with its Nix closure (glibc, libgcc) mounted read only |
| Build | `componentize-py` 0.25.1 (`tool.componentize_py`) in a throwaway venv in the run directory, never in `uv.lock`, `wasm-tools` 1.262.0 (`tool.wasm_tools`) |

## The stand-in guest

The workload library does not exist yet, so the guest is a stand-in of the same size class.

- World: `package wdf:task@1.0.0; world task { include wasi:cli/command@0.2.9; }`, the world the breakdown proposes, with the WASI 0.2.9 WIT packages from the hash-checked `WebAssembly/WASI` v0.2.9 release.
  `wasm-tools component targets` accepts the component against it.
- Source: one module, [code/app.py](code/app.py), with a `Run` export class that reads `/in/input.json`, imports `hashlib`, `json`, `struct` and `xml.etree.ElementTree` at the top, and writes one digest block to `/out/results.xml`.
  The argument `hold_mib` makes it write that many MiB first, for the probe below.
- Size: 18,992,219 bytes, 18.1 MiB (`build.component_bytes`, `build.component_mib`), built in 2.6 s on the host (`build.componentize_s`).
- Guest linear memory: 206 pages, 12.9 MiB (`guest.linear_memory_pages`, `guest.linear_memory_mib`), the smallest `-W max-memory-size` that runs the guest, found by bisection.
  The guest declares 206 pages and a `noop` run does not grow them.

How it differs from the real workload component:

- The real component is built from the `wdf_workloads` and `wdf_kernel` packages through the build library, so it holds more Python source than one module.
  The CPython runtime and the frozen standard library make up most of the 18 MB in both, so the size class holds, but the real component may be somewhat larger and take somewhat longer and more memory to compile.
- The real entry module dispatches on `params.workload` across all the workloads, and its level 1 workloads emit `HEARTBEAT` lines and grow linear memory.
  The stand-in has only `noop` and the `hold_mib` probe.
- The real build installs locked dependencies, while the stand-in has none, as the workload library will have none.

## Method

- One container per run: it starts idle (`sleep`), then the setup copies the component and the `wasmtime` binary into its scratch directory from inside the container, so their page cache is charged to the container as a pod's download would be.
  The binary is copied with `cp -p`, because `wasmtime` names its cache directory after its own modification time.
- A warm run copies in a module cache filled beforehand by one uncapped host run with the same flags, and its cache does not grow during the run, so every warm run was a cache hit.
- The guest runs through `docker exec` as `wasmtime run -C cache=y -C cache-config=<scratch> --dir in::/in --dir out::/out guest.wasm`.
- Peak container memory is read two ways from the container's cgroup:
  - `memory.peak`, which counts page cache and so reaches the cap in every first run, since the kernel fills the slot with reclaimable cache before it reclaims;
  - the peak working set, `memory.current` less `inactive_file` (what `docker stats` reports), and the peak anon, sampled every 10 ms during the run.
- The guest overhead is the peak working set less the guest linear memory, and the `memory.peak` form is recorded beside it.
- Wall time is taken on the host around `docker exec`.
- A run passes when `wasmtime` exits 0, `results.xml` is written and the cgroup records no OOM kill.
- Variants: `default`, `serial` (`-C parallel-compilation=n`) and `winch` (`-C compiler=winch`), each 3 times cold and 3 times warm.
- Probe: the default flags with `hold_mib` 64, 128, 192 and 256, each once cold and once warm.

## Measured numbers

### Pass line: default flags

| Run | Completed | Wall time | Peak working set | Peak anon | `memory.peak` | Guest overhead (working set) | Guest overhead (`memory.peak`) | Reclaim events at the cap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Cold 1 | yes, exit 0, no OOM kill | 29.63 s | 290 MiB | 286 MiB | 384 MiB | 277 MiB | 371 MiB | 13 |
| Cold 2 | yes, exit 0, no OOM kill | 29.52 s | 326 MiB | 312 MiB | 384 MiB | 313 MiB | 371 MiB | 148 |
| Cold 3 | yes, exit 0, no OOM kill | 29.03 s | 315 MiB | 309 MiB | 384 MiB | 302 MiB | 371 MiB | 134 |
| Warm 1 | yes, exit 0, no OOM kill | 0.61 s | 69 MiB | 59 MiB | 166 MiB | 57 MiB | 153 MiB | 0 |
| Warm 2 | yes, exit 0, no OOM kill | 0.54 s | 70 MiB | 60 MiB | 166 MiB | 57 MiB | 153 MiB | 0 |
| Warm 3 | yes, exit 0, no OOM kill | 0.54 s | 70 MiB | 66 MiB | 166 MiB | 57 MiB | 153 MiB | 0 |

Names: `default.<cold|warm>.<n>.completed`, `.exit`, `.oom_kill`, `.wall_s`, `.peak_ws_mib`, `.peak_anon_mib`, `.peak_mib`, `.guest_overhead_ws_mib`, `.guest_overhead_mib`, `.memory_max_events`.

Before the guest starts, the copied binary, component and cache already hold 89 MiB of page cache in a cold container and 99 to 100 MiB in a warm one (`default.<cold|warm>.<n>.before_run_mib`), and that page cache is most of the gap between `memory.peak` and the working set.
The module cache of one component is 10.6 MiB on scratch (`default.cold.<n>.cache_mib`).
The tightest first run left 58 MiB between its peak working set and the cap.
On the host with no CPU cap, the same first run takes about 1.6 s, so at 250m it is CPU quota bound: about 7 CPU seconds spread over 29 s.

### Remedy options for compile memory

| Variant | Cold wall time | Cold peak working set | Cold peak anon | Warm wall time | Warm peak working set | Names |
| --- | --- | --- | --- | --- | --- | --- |
| `default` | 29.03 to 29.63 s | 290 to 326 MiB | 286 to 312 MiB | 0.54 to 0.61 s | 69 to 70 MiB | `default.*` |
| `serial`, `-C parallel-compilation=n` | 28.62 to 29.02 s | 290 to 310 MiB | 274 to 304 MiB | 0.53 s | 69 to 70 MiB | `serial.*` |
| `winch`, `-C compiler=winch` | 7.83 to 7.93 s | 336 to 337 MiB | 320 MiB | 0.63 to 0.64 s | 90 to 94 MiB | `winch.*` |

All 18 runs of the two options completed with no OOM kill.
Serial compilation does not lower the first-run peak in the capped container, although on the uncapped host it lowered the resident size from about 440 to about 340 MiB, and at 250m it costs no wall time because the run is quota bound either way.
Winch compiles about 3.7 times faster at 250m but peaks higher, cold and warm, and its code runs slower than Cranelift's, which was not measured here.
Neither option is a remedy for memory, so neither is recorded for the runtime library.

### Probe: guest memory after a cold compile

| Guest holds | Guest linear memory | Cold | Cold peak working set | Warm | Warm peak working set | Names |
| --- | --- | --- | --- | --- | --- | --- |
| 64 MiB | about 77 MiB | completed | 368 MiB | completed | 117 MiB | `hold64.*` |
| 128 MiB | about 141 MiB | **OOM killed, exit 137** | 383 MiB | completed | 180 MiB | `hold128.*` |
| 192 MiB | about 205 MiB | **OOM killed, exit 137** | 382 MiB | completed | 243 MiB | `hold192.*` |
| 256 MiB | about 269 MiB | **OOM killed, exit 137** | 383 MiB | completed | 309 MiB | `hold256.*` |

The killed runs died after about 29 s, at the end of the compile, when the guest began to write.
A warm run's working set is about 40 MiB above the guest's linear memory (`hold<n>.warm.1.guest_overhead_ws_mib`), and a cold run's about 290 MiB.
The compile memory therefore stays with the process: the allocator keeps it, or `wasmtime` holds compile state for the life of the run.

## What the numbers change

1. The pass line holds: a pod in the `ci` simulate slot survives the first run of a component of this size, so no slot resize and no precompiled `.cwasm` are needed for the cold start itself.
2. `GUEST_OVERHEAD_MIB` in `wdf_runtime.pins` depends on whether the guest can run in the process that compiled it.
   - From the warm figure: 57 MiB peak plus a quarter in whole 64 MiB steps, the sizing rule of the [state footprint spike](../S8/state-footprint.md), is **128 MiB**, which leaves a `ci` guest cap of 256 MiB, and the warm probe held 256 MiB (269 MiB of linear memory) in this slot.
   - From the cold figure: 313 MiB plus a quarter is 448 MiB, above the slot, so a `ci` guest would get no memory at all by that rule, and the probe shows a first run has room for only between 64 and 128 MiB of guest data.
3. Proposal for the runtime library and the workers epic: the first run of a component on a pod is a separate warm-up process that runs the `noop` workload and fills the module cache, and the task's guest then runs warm in a new process, so the overhead is the warm figure of 128 MiB.
   The workload library already has `noop`, and the warm-up costs about 29 s at 250m once per component per pod.
   The runtime library adds this as a `warm_up` command line beside `command`, and the workers epic runs it when the cache has no entry for the component.
4. If that rule is refused, the remedies stay in the order of the fail line: a simulate slot resize as a cluster epic `revisit`, so that the cold overhead and the guest both fit, then precompiled `.cwasm`, which changes a pinned contract and returns to the captain.
   A `.cwasm` compiled per pod with `wasmtime compile` and run with `--allow-precompiled` never leaves the pod and would need no artifact kind, but it changes the runtime command line and was not measured here.
5. A first run spends 29 s at 250m before the guest starts, so its first `HEARTBEAT` line comes at least that late: the workers epic sets the heartbeat timeout of a first run above 30 s, and the runtime library's pin tests record whether `-W timeout` counts compile time.
6. `wasmtime` puts its executable's modification time in the name of its cache directory (`wasmtime-<commit>-<mtime in ms>`), so a cache filled by one copy of the binary misses for a copy with a new time.
   The Nix store fixes the time at 1 s, and the simulate image must keep the binary's time fixed too, or every image rebuild empties the cache.
7. The cache costs 10.6 MiB of scratch per component, with default flags.

## Caveats and what was not measured

- One host, one run of the full sequence for the tables above, after a trial run whose warm runs missed the cache (the copied binary had a new modification time) and a full run with the earlier guest without `hold_mib`.
  The earlier full run gave the same picture: cold 28.0 to 28.5 s and 289 to 316 MiB, warm 0.53 to 0.59 s and 68 to 69 MiB, all 18 runs completed.
- The first-run working set varied between 290 and 326 MiB across three runs, a spread of about 12 percent, and with 58 MiB to spare at the worst run, a real component somewhat larger than the stand-in may come close to the cap.
  The real component is the check: the runtime library's pin tests rerun this figure on it.
- `wasmtime` compiles with as many threads as the host has CPUs, 8 here, and the CPU quota does not change that, so a node with a different CPU count may peak differently.
- Docker on WSL2 stands in for a kind node, with the same cgroup v2 memory accounting and no kubelet eviction.
- The page cache of the Nix closure (glibc, libgcc) is charged to the host and not the container, a few MiB at most, and the `busybox` shell and `sleep` are in the container's figures.
- The guest overhead is measured for a guest that does not grow its memory, and the probe gives it only to the nearest 64 MiB step for a guest that does.
- The runtime speed of Winch code, `-W timeout` and `-W max-memory-size` against a first run, and a per-pod `.cwasm` were not measured.
