# Spike: heavy compile in the `ci` compile slot (S1b)

Run on 2026-11-09.
Owner: the [workload toolchain epic](https://github.com/AndoNorth/wasm-dag-forge/issues/11), lane S, filed as [spike issue 274](https://github.com/AndoNorth/wasm-dag-forge/issues/274).
Method: [docs/delivery/method.md](../../method.md).
Spec: the S1b block of [docs/delivery/epics/07-workload-toolchain.md](../../epics/07-workload-toolchain.md), and this spike gates C.2.
Throwaway code: [code/](code/), run as `nix develop -c docs/delivery/spikes/S1b/code/run.sh`.
Every number below is a `figure <name> <value>` line of that run, and the name is given in backticks beside it.

## Result

**Fail, on part (2) of the pass line only.**

1. Pass: on 3 of 3 runs, the stand-in workload bundle built under `docker run --memory 1g --memory-swap 1g --cpus 1` in 7.56 to 7.67 s, with a peak working set of 414 to 417 MiB and a `memory.peak` of 431 to 433 MiB, both under the 768 MiB budget.
2. Fail: on 3 of 3 runs, no generated heavy bundle was killed with exit 137.
   The peak rises with the size of the generated module up to 900 to 909 MiB of working set at 2.25 MiB of source, and at 2.5 MiB `componentize-py` 0.25.1 stops with exit 1 and `too much data is being copied between the host and the guest: fuel allocated for hostcalls has been exhausted`, before memory reaches the cap.
   No smallest killed size exists (`heavy.<n>.smallest_killed_mib` is `none`).
3. Pass: on 3 of 3 runs, the `componentize-py` step ran with `--network none` after an install step in its own container with the default network.

Recorded for D9: `unshare --user --net` fails with `Operation not permitted` in the build container under the default seccomp profile, and works with `seccomp=unconfined`.

The fail consequences, in the order of the issue:

1. Part (1) passed, so no compile slot resize goes to the cluster epic.
2. Part (2) failed, so C.2 keeps only the bundle that exceeds a short `--timeout`, and the compile OOM at the slot is left to the failure scenarios epic ([#15](https://github.com/AndoNorth/wasm-dag-forge/issues/15)) on the cluster.
3. Part (3) passed, so D9 does not fall back on account of the network check, though the `unshare` probe above means D9 (a) never takes effect in a default-profile container, as "What the numbers change" states.

## Question

Does the workload library bundle build inside the `ci` compile slot of 1 CPU and 1 GiB (`deploy/presets/slots/ci.yaml`), which generated bundle size is killed at that slot, and can the build step run with no network?

## Environment

| Item | Value |
| --- | --- |
| Host | WSL2, 20,002 MiB `MemTotal` (`host.mem_total_mib`), 8 CPUs (`host.cpus`), kernel 6.6.87.2 (`host.kernel`), Docker 29.1.3 (`host.docker`), builtin seccomp profile and cgroup namespaces (`host.seccomp`) |
| Other work on the host | 1 other container running (`host.other_containers`), not touched, 16,540 MiB `MemAvailable` before (`host.mem_available_mib.before`) and 16,326 MiB after (`host.mem_available_mib.after`) |
| Locks | one of the two `wdf-t1` lock files for the whole run, no cluster |
| Containers | one at a time, `--memory 1g --memory-swap 1g --cpus 1`, run as the invoking user, label `wdf.dev/verify-run`, each removed after its step, no published port |
| Left behind | none: `docker ps -a --filter label=wdf.dev/verify-run` lists 0 after the run (`containers.left.label_all`) |
| Image | `python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72`, Python 3.13.18 (`tool.image_python`) |
| Build | `componentize-py` 0.25.1 (`install.<n>.componentize_py`) installed with `pip` into a throwaway venv in the run directory, never in `uv.lock`, `wasm-tools` 1.262.0 (`tool.wasm_tools`) on the host for the target check |
| WIT | `package wdf:task@1.0.0; world task { include wasi:cli/command@0.2.9; }`, with the WASI 0.2.9 WIT packages from the hash-checked `WebAssembly/WASI` v0.2.9 release, as in the [cold start spike](../S6/cold-start.md) |

## The stand-in bundle

The workload library and the kernel do not exist yet, so the bundle is a stand-in of both, written by [code/gen.py](code/gen.py).

- Entry: `wdf_workloads.main` with the `Run` export class, which imports `hashlib`, `json`, `struct`, `time`, `xml.etree.ElementTree` and `xml.sax.saxutils` at the top, reads `/in/input.json` and writes one digest block.
- Kernel: `wdf_kernel.gen0`, one generated module of 262,346 bytes of source (`bundle-0.25-1-0.source_bytes`), 564 functions of 64-bit arithmetic with a loop, a branch and a table lookup each, imported at the top of the entry.
- Output: a component of 29.4 to 29.5 MiB (`standin.<n>.component_bytes`), which `wasm-tools component targets` accepts against the world on 3 of 3 runs (`standin.<n>.targets_ok`).

How it differs from the real workload library bundle:

- The real bundle holds `wdf_kernel` and `wdf_workloads` as their issues define them, which are hand-written modules and not one generated module.
  The 256 KiB of generated source is an estimate set above the size of those packages as the spec lists them, and the build peak grows with the source and with the number of functions, so the real bundle should build at or below these figures.
- The real build installs locked dependencies with `uv pip install --target` from the wheelhouse mirror, while the stand-in has none, as the workload library will have none, so the install step here is only the `pip` install of `componentize-py` itself.
- The real build runs inside the compile image with the build library (`python -m wdf_compile build`), not a bare `componentize-py` call in a stock Python image.

The heavy bundles use the same generator with a larger kernel module (`--size-mib N`), as the spec's `python -m wdf_compile.heavy --size-mib N` proposes, and three probes vary the shape: the same source split across several modules (`--modules K`) and a module that fills memory at import time and keeps only its length (`--hold-mib H`).

## Method

- Per run, an install container with the default network creates a venv on the shared work directory and installs `componentize-py==0.25.1` into it with `pip`, and is removed.
- Each build then gets its own container with `--network none` that starts idle (`sleep`), copies the bundle tree and the WIT tree into its own `/tmp` from inside the container, so their page cache is charged to it, and runs `componentize-py -d wit -w task componentize wdf_workloads.main` through `docker exec`.
- Peak container memory is read from the container's cgroup two ways, as in the cold start spike:
  - `memory.peak`, which counts page cache;
  - the peak working set, `memory.current` less `inactive_file` (what `docker stats` reports), and the peak anon, sampled every 10 ms.
- Wall time is taken on the host around `docker exec`.
- The pass line uses the peak working set and checks `memory.peak` beside it, and for the stand-in both are under 768 MiB.
- A build passes when `componentize-py` exits 0 and writes the component, and the cgroup records no OOM kill.
- Heavy scan per run: one module of 1.0, 1.25, 1.5, 1.75, 2.0, 2.25 and 2.5 MiB of source, in ascending order, stopping at the first exit 137.
- Probes once each, with the venv of run 1: one module of 3.0 and 4.0 MiB, 4.0 MiB in 4 modules, 6.0 MiB in 3 modules, and the stand-in with 64 and 256 MiB filled at import.
- D9: in a build container, `unshare --user --net --map-root-user true` as the invoking user, once with the default seccomp profile and once with `--security-opt seccomp=unconfined`.

## Measured numbers

### Pass line part (1): the stand-in bundle

| Run | Install | Build | Wall time | Peak working set | Peak anon | `memory.peak` | Component |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | exit 0, 3.52 s, 83 MiB | exit 0, no OOM kill | 7.67 s | 414 MiB | 409 MiB | 431 MiB | 30,859,019 bytes |
| 2 | exit 0, 3.22 s, 84 MiB | exit 0, no OOM kill | 7.56 s | 417 MiB | 411 MiB | 433 MiB | 30,877,219 bytes |
| 3 | exit 0, 3.44 s, 84 MiB | exit 0, no OOM kill | 7.62 s | 416 MiB | 410 MiB | 432 MiB | 30,902,562 bytes |

Names: `install.<n>.exit`, `.wall_s`, `.peak_ws_mib`, and `standin.<n>.exit`, `.oom_kill`, `.wall_s`, `.peak_ws_mib`, `.peak_anon_mib`, `.peak_mib`, `.component_bytes`.

The build leaves 351 MiB of the 768 MiB budget, and 607 MiB of the slot, at its worst run.
Two builds of one bundle differ in size by a few tens of KiB, so the component is not byte-stable, which the spec allows.

### Pass line part (2): heavy bundles, one generated module

| Source | Run 1 | Run 2 | Run 3 | Peak working set | `memory.peak` | Component |
| --- | --- | --- | --- | --- | --- | --- |
| 1.0 MiB | exit 0, 8.47 s | exit 0, 8.64 s | exit 0, 8.14 s | 597 to 599 MiB | 617 to 618 MiB | 69.8 MiB |
| 1.25 MiB | exit 0, 8.51 s | exit 0, 8.44 s | exit 0, 8.51 s | 714 to 721 MiB | 737 to 742 MiB | 93.5 to 93.6 MiB |
| 1.5 MiB | exit 0, 8.95 s | exit 0, 8.63 s | exit 0, 8.97 s | 748 to 758 MiB | 776 to 777 MiB | 101.5 MiB |
| 1.75 MiB | exit 0, 9.64 s | exit 0, 8.79 s | exit 0, 9.08 s | 804 to 841 MiB | 830 to 859 MiB | 111.4 MiB |
| 2.0 MiB | exit 0, 9.45 s | exit 0, 9.02 s | exit 0, 8.99 s | 854 to 855 MiB | 876 to 877 MiB | 120.5 to 120.6 MiB |
| 2.25 MiB | exit 0, 10.20 s | exit 0, 9.42 s | exit 0, 9.21 s | 900 to 909 MiB | 926 to 929 MiB | 131.0 MiB |
| 2.5 MiB | **exit 1**, 8.53 s | **exit 1**, 8.30 s | **exit 1**, 8.25 s | 425 to 426 MiB | 444 to 445 MiB | none |

Names: `heavy.<n>.<MiB>.exit`, `.wall_s`, `.peak_ws_mib`, `.peak_mib`, `.oom_kill`, `.component_bytes`, `.stderr_tail`, and `heavy.<n>.smallest_killed_mib`.

No build recorded an OOM kill (`.oom_kill` is 0 throughout).
Every exit 1 ends with `too much data is being copied between the host and the guest: fuel allocated for hostcalls has been exhausted` (`.stderr_tail`).
On the host, the same error names its step as `retrieving memory with component-init-get-memory`: `componentize-py` runs the entry's imports in the guest and then copies the guest's linear memory out to snapshot it, and that copy has a fixed hostcall fuel budget.
So the guest state that a build can snapshot is capped, and the build peak with it, at about 930 MiB of `memory.peak` in this slot, below the 1 GiB cap.

### Probes: other shapes

| Probe | Exit | Wall time | Peak working set | `memory.peak` | Component | Names |
| --- | --- | --- | --- | --- | --- | --- |
| one module, 3.0 MiB | **1**, fuel exhausted | 8.68 s | 447 MiB | 466 MiB | none | `over.3.0.*` |
| one module, 4.0 MiB | **1**, fuel exhausted | 9.48 s | 484 MiB | 505 MiB | none | `over.4.0.*` |
| 4.0 MiB in 4 modules | 0 | 10.68 s | 640 MiB | 666 MiB | 78.2 MiB | `split.4.0.4.*` |
| 6.0 MiB in 3 modules | **1**, fuel exhausted | 9.40 s | 393 MiB | 416 MiB | none | `split.6.0.3.*` |
| stand-in, 64 MiB filled at import | 0 | 8.41 s | 710 MiB | 732 MiB | 93.0 MiB | `hold.64.*` |
| stand-in, 256 MiB filled at import | **1**, fuel exhausted | 7.68 s | 544 MiB | 561 MiB | none | `hold.256.*` |

Splitting the source does not get past the budget, and memory filled at import and released counts against it like source, because the snapshot copies the whole linear memory.
The largest component built in the run is 131.0 MiB, from the 2.25 MiB module, so the budget sits a little above that snapshot.

### Pass line part (3) and D9

| Check | Result | Names |
| --- | --- | --- |
| `componentize-py` with `--network none` after the install step with network | exit 0 on 3 of 3 runs, every build in the run had no network | `standin.<n>.exit` |
| `unshare --user --net --map-root-user`, default seccomp profile | exit 1, `unshare: unshare failed: Operation not permitted` | `userns.default.*` |
| the same with `--security-opt seccomp=unconfined` | exit 0 | `userns.unconfined.*` |

A manual probe before the run gave the same `Operation not permitted` as root in the container, so the default profile refuses the call and the user does not matter.

## What the numbers change

1. Compile slot: no resize, the stand-in uses 417 MiB of the slot at most.
2. Heavy-compile bundles (C.2): the spec's row asks S1b for the smallest N whose build exceeds the slot, and none exists with `componentize-py` 0.25.1, so by the fail consequence C.2 keeps only the bundle that exceeds a short `--timeout`.
   Every build here took 7.56 to 10.68 s at one CPU, so the stand-in itself exceeds a `--timeout` of 5 s, and a 2.25 MiB module, the largest that builds, gives the widest margin.
3. A proposal for C.2, not part of the fail consequence: a single generated module of 2.5 MiB fails in about 8.5 s on 3 of 3 runs with exit 1 and the fuel message, so it is a deterministic `compile_failed` bundle that `python -m wdf_compile.heavy --size-mib 2.5` would give for free.
4. Compile OOM (#15): with `componentize-py` 0.25.1, a bundle cannot drive the `componentize-py` step past about 930 MiB of `memory.peak`, so a `compile_oom` at the 1 GiB slot needs another heavy step or a lower memory limit.
   By inference from the peak anon, not measured, a 2.0 MiB module (848 to 849 MiB anon) would be killed under a 768 MiB limit, because anon memory cannot be reclaimed with no swap.
5. D9: option (a) as written runs the step under `unshare` only when the profile allows it, and the default seccomp profile does not, so in a default-profile pod the build library always takes its fallback and the step runs without in-process network isolation.
   The no-network guarantee then rests on the compile pod having no egress, which is the workers epic's to provide, and C.2's tests of the D9 path must cover the `Operation not permitted` fallback as the normal case.
6. Import-time state: the fuel budget also refuses a bundle whose imports grow the guest's memory by somewhere between 64 and 256 MiB, so the workload library keeps its import-time work small, which "every import at the top of a module" with no import-time data already implies.

## Caveats and what was not measured

- One host, one run of the full sequence for the tables above, after a one-run trial that gave the same picture: the stand-in at 418 MiB and 9.2 s, the heavy scan built up to 2.25 MiB at 900 MiB and failed at 2.5 MiB with the same fuel message, and the same D9 results.
- The memory per MiB of source depends on the shape of the generated code: on the uncapped host, 1 MiB of very small functions reached a resident size of 1,064 MiB, against 727 MiB for this generator, and its 2 MiB failed on the same fuel budget.
  The fuel budget caps every shape, so the verdict does not depend on it, but the exact sizes in the table belong to this generator.
- `componentize-py` was installed with `pip` from PyPI and not with `uv` from the wheelhouse mirror, and the hostcall fuel budget belongs to version 0.25.1, so a bump of `componentize-py` reruns this spike.
- Docker on WSL2 stands in for a kind node, with the same cgroup v2 memory accounting and no kubelet eviction.
- The `docker exec` of each build adds a few tens of milliseconds to its wall time.
- The stand-in is not the real workload library: the real bundle's build figures belong to its own build tests once W.1 exists.
