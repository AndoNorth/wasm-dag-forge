# Cluster host guide

How to prepare a WSL2 host so the local kind cluster stays up.
Host prerequisites, the doctor and the recipes live in [docs/dev-setup.md](dev-setup.md) and are not repeated here.
Breakdown: [docs/epics/02-cluster.md](epics/02-cluster.md)

Checks named below are either a `just doctor` check id, written as `id`, or a manual check, labelled as such.
The automated ones run with:

```sh
nix develop -c just doctor
```

## `.wslconfig`

The file is `%UserProfile%\.wslconfig` on the Windows side and applies to every WSL2 distribution.
Changes take effect only after `wsl --shutdown`.

```ini
[wsl2]
memory=<at least the preset memory_mib, plus headroom for Docker and the shell>
processors=<at least the preset cpus>
vmIdleTimeout=-1

[general]
instanceIdleTimeout=-1
```

### Memory and processors

The VM limit caps the sum of every kind node container, because nodes are containers inside the WSL VM.
The preset files under `deploy/presets/` carry `memory_mib` and `cpus`, so the VM must offer at least those figures.
`memory-vs-preset` compares the VM `MemTotal` with the selected preset and fails when the VM is smaller.
The `processors` figure has no doctor check, so `nproc` inside WSL2 is compared manually with the preset `cpus`.
A shared Docker daemon and the editor also draw on the same VM, so leave headroom above the preset figure.

### Idle timeouts

`vmIdleTimeout` is the time in milliseconds the WSL2 VM waits with no running work before it stops.
`instanceIdleTimeout` is the time in milliseconds a distribution instance waits before it stops.
Either one firing stops Docker and every kind node with it.
Setting both to `-1` disables idle shutdown.
Neither setting is visible to doctor, so reading them back from `.wslconfig` is a manual check.

## Inside the distribution

### systemd

Docker must be managed by systemd, which requires `systemd=true` under `[boot]` in `/etc/wsl.conf`.
`wsl-systemd` checks that PID 1 is systemd.

### inotify

Kind nodes and file watchers each consume inotify instances, and the kernel default is too low.
`inotify-instances` checks `fs.inotify.max_user_instances` against the minimum recorded in `docs/dev-setup.md`.
The value belongs in a file under `/etc/sysctl.d/`, because a value set with `sysctl -w` is lost at the next distribution start.

### ext4 location

The repository and the host data directory named by the preset belong on the WSL ext4 filesystem.
`repo-on-ext4` checks the repository path.
The preset `host.data_dir` is a manual check: `stat -f -c %T <data_dir>` must print `ext2/ext3`, which is how the kernel reports ext4.
Paths under `/mnt/c` use 9p, where file watching is unreliable and Postgres and object store data suffer.

### Docker and cgroups

`docker-engine` and `docker-version` check the daemon, and `cgroup-v2` checks the unified hierarchy kind requires.
The setup steps are in [docs/dev-setup.md](dev-setup.md).

## Host access map

`deploy/host/ports.yaml` is the one table of endpoints a host process can reach without Tilt running.
Each row maps `127.0.0.1:<host_port>` to a NodePort on the cluster, and the table shape is pinned in [contracts/preset/](../contracts/preset/README.md).
The renderer places the mappings on the control plane node as `extraPortMappings` with `listenAddress: 127.0.0.1`, so no row can widen the bind.

| Name | Host address | NodePort |
| --- | --- | --- |
| `control-api` | `127.0.0.1:18080` | 30080 |
| `dispatch-api` | `127.0.0.1:18081` | 30081 |

`deploy/host/dummy-endpoint.yaml` is a stand-in for the `control-api` endpoint, one busybox replica in `wdf-control`, until a real module serves it.
Port mappings are fixed when the cluster is created, so a changed table takes effect after `just nuke` and `just up`.
A mapped port answers only once the Service behind it has endpoints, and a request before that is reset.

```sh
kubectl --context kind-wdf apply -f deploy/host/dummy-endpoint.yaml
ss -ltn | grep -E ':(18080|18081)\b'
curl http://127.0.0.1:18080/
```

## Persistence

The infra node mounts the preset `host.data_dir` at `/var/local-path-provisioner`, where the default storage class keeps every volume.
A volume claim on the infra pool therefore lives on the host, and `deploy/host/dummy-statefulset.yaml` is a stand-in that proves it.
The renderer creates the directory so it belongs to the user, while the node containers write into it as root.

| Recipe | Effect on the data directory |
| --- | --- |
| `just down` | stops the node containers, the data stays and `just up` starts them again |
| `just nuke` | deletes the cluster and the registry, then empties and removes the data directory |

`just nuke` can be repeated, and a run with nothing left exits 0.
It removes only a directory that a preset in `deploy/presets/` names, and otherwise exits 4 before deleting anything.
`WDF_DATA_DIR` names another directory for it, which makes the refusal testable and is refused for every path outside the presets.

## What each setting protects

| Setting | Protects against | Doctor check |
| --- | --- | --- |
| `.wslconfig` `memory` | the VM being smaller than the preset, which gets nodes OOM-killed | `memory-vs-preset` |
| `.wslconfig` `processors` | CPU starvation of nodes whose limits sum above the VM | manual: `nproc` |
| `vmIdleTimeout=-1` | the VM stopping while nobody is typing | manual: read `.wslconfig` |
| `instanceIdleTimeout=-1` | the distribution stopping while nothing is attached | manual: read `.wslconfig` |
| `/etc/wsl.conf` `systemd=true` | Docker not starting with the distribution | `wsl-systemd` |
| `fs.inotify.max_user_instances` | watchers and nodes failing with "too many open files" | `inotify-instances` |
| repo and data on ext4 | slow and unreliable file watching, data on 9p | `repo-on-ext4` |

## What no setting covers

| Event | Effect | Recovery |
| --- | --- | --- |
| Windows sleep or hibernate | the VM is suspended, clocks jump and long-running connections drop | `just up` after wake, which is idempotent |
| Windows update reboot | the VM and every node stop without a graceful shutdown | `just up` after login |
| `wsl --shutdown` or `wsl --update` | same as a reboot for the VM | `just up` |

No `.wslconfig` setting prevents these.
Long runs need sleep disabled in the Windows power plan, and only the data on ext4 survives a reboot, not the running cluster.
The data directory persists across `just down`, and `just nuke` removes it.
