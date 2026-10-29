# Spike: cluster footprint on a 7.8 GiB host (S2)

Run on 2026-10-29.
Owner: the [fixed-resource cluster epic](https://github.com/AndoNorth/wasm-dag-forge/issues/2), lane F.
Breakdown and method: [docs/epics/02-cluster.md](../epics/02-cluster.md), section 5.
Throwaway code: `spikes/S2/`.

## Result

**Fail.**
The `ci` preset does not fit this host with its components at the sized figures, and the shipped `ci` node sizes cannot admit those components in the first place.
Gate G1 is not passed.

## Question

Does the `ci` preset fit on this 7.8 GiB host with its nodes at their limits, and what host does each preset need?

## Environment

| Item | Value |
| --- | --- |
| Host | WSL2, 7,942 MiB `MemTotal`, 6 CPUs, 12 GiB swap |
| Other work on the host | about 2.2 to 2.6 GiB in use before each run (other containers, editor, agents) |
| `MemAvailable` before `just up` | 5,136 MiB (run A), 5,287 MiB (run B), 5,609 MiB (run C) |
| Cluster | `just up` on `ci`, six node containers and the registry, kindest/node v1.35.0 |
| Namespaces | `scripts/cluster/namespaces.sh apply ci`, so the quotas and limit ranges of the topology contract are in force |
| Stand-ins | a busybox pod per component holding its memory with `head -c N /dev/zero` into `tail -c N`, requests equal to limits, pinned to its pool |

## Method

Each run follows section 5 of the epic with the deviations listed under caveats.

- `just up` on `ci`, wait 60 s, record per-node memory and the `memory.events` counters of every node container.
- Start the stand-ins one at a time from a figures table, and record the phase and host `MemAvailable` after each.
- Hold, sampling host `MemAvailable` every 5 s, and remove the stand-ins if it falls below 1 GiB.
- Read the counters again, run `just up` a second time under load, and delete the cluster with `just nuke`.

The three runs differ only in the figures table.

| Run | Figures | Purpose |
| --- | --- | --- |
| A | `spikes/S2/figures-literal.tsv`: Temporal 1 CPU and 1,024 MiB, its Postgres 0.5 CPU and 1,024 MiB, object store 0.1 CPU and 266 MiB, one compile and one aggregate pod at 1 CPU and 1,024 MiB | the figures of the epic as written |
| B | `spikes/S2/figures-fit.tsv` with the compile and aggregate pods at 944 MiB and 0.9 CPU, the node allocatable | the largest pods the node numbers suggest will fit |
| C | the same table with compile and aggregate at 890 MiB and 0.8 CPU, and the control plane container limit raised to 1,280 MiB | the largest pods the nodes actually admit |

Temporal is held at its measured steady 686 MiB in runs B and C, and at its 1 GiB limit in run A.
The Postgres CPU request is a guess, because no CPU figure exists for it.

## Pass line against the results

| Check | Run A | Run B | Run C |
| --- | --- | --- | --- |
| Cluster Ready, every node Ready | pass, 6 of 6 | pass, 6 of 6 | pass, 6 of 6 |
| Every stand-in Running | **fail**, 2 of 5 | **fail**, 3 of 5 | pass, 5 of 5 |
| Host `MemAvailable` never below 1,024 MiB | pass, lowest 2,046 MiB, but two stand-ins never ran | pass, lowest 2,221 MiB, but two stand-ins never ran | **fail**, 1,000 MiB, run aborted by the watchdog |
| No node `oom_kill` above zero | pass | pass | pass |
| No node `memory.events` `max` rising | pass | **fail**, control plane 1,174 to 1,176 | not decided, aborted after 20 s |
| `just up` exits 0 again under load | pass, 4 s, containers unchanged | pass, 4 s, containers unchanged | not run, aborted |

The pass line is binary, so the verdict is fail.

## What each failure is

1. The compile and aggregate nodes cannot hold a 1 CPU and 1 GiB pod.
   Node allocatable is 944 MiB and 900m by the preset rule, and kindnet takes 50 MiB and 100m of it on every worker, so a pod must fit in 894 MiB and 800m.
   Both pods stay Pending with `Insufficient cpu` and `Insufficient memory`.
   `scripts/cluster/slots.sh` reports the slot against the allocatable of the node but does not subtract the daemon requests.
2. The `wdf-infra` quota rejects the third infra stand-in in run A.
   The quota is 2,252 MiB of requests and limits, and Temporal, its Postgres and the object store ask for 2,314 MiB.
   The same limits would be rejected for real components with limits at the sized figures.
3. The host cannot hold the five stand-ins at the sized figures above the 1 GiB floor.
   In run C the idle cluster left 4,617 MiB, and the stand-ins hold 3,692 MiB, so `MemAvailable` fell to 1,000 MiB within 20 s of the last start.
   The watchdog removed the stand-ins and the host recovered to 5,778 MiB after the teardown.
4. The control plane at 1,024 MiB runs against its limit.
   Its working set is 678 to 731 MiB, and `memory.current` reads 1,016 to 1,039 MiB because page cache fills the rest.
   `memory.events` `max` counted 802 and 1,174 reclaim events during boot in runs A and B, and two more during the hold in run B, with `oom_kill` at zero.
   Whether 1,280 MiB stops this was not measured.

## Measured numbers

| Figure | Value |
| --- | --- |
| Idle control plane node | 678 to 731 MiB working set (three runs) |
| Idle worker node | 143 to 173 MiB working set, mean about 158 MiB |
| Idle `ci` cluster, six nodes and the registry | 1,491, 1,560 and 1,512 MiB by `docker stats` |
| Host `MemAvailable` cost of the idle cluster | 978, 1,002 and 992 MiB, about 1 GiB, lower than the node sum because page cache is shared |
| Registry | 9 to 10 MiB |
| Infra node with Temporal, Postgres and object store held (run A, two stand-ins) | 2.1 GiB of its 2.5 GiB limit |
| Per-node daemon requests | workers 100m and 50 MiB, control plane 850m and 220 MiB |
| `just up` on a clean host | 40 to 41 s |
| `just up` again with the cluster loaded | 4 s, exit 0, same container ids |
| Resident cost of the five stand-ins at the sized figures | 3,692 MiB held, 3,852 MiB requested |

`oom_kill` stayed at zero on every node in every run.

## Host requirement table

Measured means taken from the runs above.
Computed means derived from preset numbers and measured figures, without starting the preset.

| Preset | Node limits sum | CPUs | Idle node overhead | Stand-in load | Host needs (`MemAvailable` at start) | Basis |
| --- | --- | --- | --- | --- | --- | --- |
| `ci` | 7,552 MiB | 6 | about 1.0 GiB of host memory, 1.5 GiB by node accounting, measured | 3.6 GiB held, measured | about 5.5 GiB, so that the 1 GiB floor holds | measured: 4,609 MiB consumed from the start of run C, plus the floor |
| `dev` | 19,456 MiB | 17 | about 1.8 GiB, computed from 0.73 GiB plus 7 workers at 0.158 GiB | not measured | about 20 GiB if every node reaches its limit, plus the floor and other work | computed |
| `soak` | 38,912 MiB | 30 | about 2.6 GiB, computed from 0.73 GiB plus 12 workers at 0.158 GiB | not measured | about 39 GiB if every node reaches its limit, plus the floor and other work | computed |

The `ci` figure is the one that decides this host.
This host offered between 5.0 and 5.5 GiB at the start of the three runs, so it misses the figure by a small margin when idle and by more with its usual load.
The `ci` preset also asks for 6 CPUs, which equals this host, so it leaves nothing for the other work, and CPU contention was not measured because the stand-ins hold memory only.

The limit-bound figures for `dev` and `soak` add the 1 GiB floor to the sum of node limits and the registry, and a shared Docker daemon adds its own use on top, which was 2.2 to 2.6 GiB here.
Neither preset was started.
This host holds neither, and no other host was available to measure, so the soak host choice is not settled by this result.

## Smallest `ci` numbers that pass admission

Computed from the preset rule (allocatable is the node size minus 256 MiB minus one sixteenth) and the measured daemon requests.
These are the smallest values that admit the sized components, not a tested configuration.

| Node | Now | Needed | Reason |
| --- | --- | --- | --- |
| compile | 1,280 MiB, 1 CPU | 1,536 MiB, 1.5 CPU | a 1 GiB and 1 CPU slot plus 50 MiB and 100m of daemons needs 1,424 MiB and 1.2 CPU |
| aggregate | 1,280 MiB, 1 CPU | 1,536 MiB, 1.5 CPU | the same slot |
| infra | 2,560 MiB, 2 CPU | 3,072 MiB | the limits of Temporal, Postgres and the object store sum to about 2,350 MiB, which needs about 2,830 MiB and must also clear the quota |
| control plane | 1,024 MiB | not decided | the limit is reached at boot, whether more is needed was not measured |

With those numbers the `ci` sum of node limits is 8,576 MiB, above the 7,942 MiB this host has in total.
So resizing the nodes cannot make `ci` fit this host at the sized component figures, and the alternatives are smaller component figures or a larger host.

## Decision

Gate G1 fails on this host.
Per section 5 of the epic, the next epic is not opened until the host decision (decision 1) is made, and epics that need no cluster continue.
The result gives three inputs to that decision and to the sizing issue that follows:

1. `ci` as shipped cannot admit its own compile and aggregate slots, so the node numbers change whichever host is chosen.
2. At the sized component figures, `ci` needs about 5.5 GiB of free host memory, so a host that keeps 5.5 GiB free while running 2.5 GiB of other work needs about 8 GiB or more in the VM, which is more than this 7.8 GiB laptop leaves.
3. `dev` and `soak` need hosts of about 20 GiB and 39 GiB at their limits, before other work, and nothing here measures a smaller figure.

The slot figures in `deploy/presets/slots/ci.yaml` stay marked as an estimate, and measuring real components belongs to characterization.

## Caveats and what was not measured

- The hold was 120 s (run A), 300 s (run B) and 20 s before the abort (run C), not 15 minutes.
  A stand-in holds a fixed amount, so slow growth is not modelled, and the hold only bounds how long the counters were watched.
- The sampling interval was 5 s instead of 15 s, which is stricter.
  The lowest value seen in run C is 1,000 MiB, and the true minimum between samples may be lower.
- No stand-in exists for the obs and simulate nodes, because the epic gives no figure for them, and neither node held a workload.
  The dummy module pods, the package mirror and Tilt were not started, so their small footprints are not in the numbers.
- The stand-ins hold memory and request CPU, and do not burn CPU, so throttling under load was not measured.
- Run C never reached its hold, so the `memory.events` `max` check for a 1,280 MiB control plane and the second `just up` under the full load have no result.
- Real Temporal, Postgres and SeaweedFS footprints were not re-measured, and the sizes come from the earlier spikes.
- One host, no Docker Desktop and no non-WSL Linux.
  Host memory moves with the other work on this laptop, so the three starting points differ by about 500 MiB.
- `dev` and `soak` were not started.
