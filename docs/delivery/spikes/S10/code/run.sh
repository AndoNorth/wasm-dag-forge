#!/usr/bin/env bash
# Chart spike: brings up the ci preset with its ci profile modules, installs the official chart temporal 1.8.0 into
# wdf-infra from the manifests of `helm template --skip-tests`, runs the stand-in load of load.py with host workers
# through a port-forward on 127.0.0.1, samples every Temporal pod's cgroup and the history service's cache series,
# and prints one `figure <name> <value>` line for every number of docs/delivery/spikes/S10/helm-install.md.
# Run in the dev shell from the repository root:
#   nix develop -c docs/delivery/spikes/S10/code/run.sh [out-dir]
# Takes the cluster lock and destroys the cluster at the end, unless WDF_S10_KEEP=1.
# Exit 0 the run completed (the verdict is a figure), 1 a step failed, 2 usage, 4 refused: too little memory.
set -uo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)
cd "$root" || exit 2
[ $# -le 1 ] || { echo "usage: run.sh [out-dir]" >&2; exit 2; }
out=${1:-$(mktemp -d "${TMPDIR:-/tmp}/wdf-s10.XXXXXX")}
mkdir -p "$out"

# The 10,880 MiB of the ci preset plus 1,024 MiB for the host workers and the port-forwards.
need_kb=12189696
grep -E '^(MemTotal|MemAvailable):' /proc/meminfo
docker ps --format '{{.Names}}'
have_kb=$(awk '/^MemAvailable:/ { print $2 }' /proc/meminfo)
[ "$have_kb" -ge "$need_kb" ] || { echo "refused: MemAvailable $have_kb kB, need $need_kb kB" >&2; exit 4; }

if [ -z "${WDF_CLUSTER_LOCK_HELD:-}" ]; then
    exec flock /tmp/wdf-cluster.lock env WDF_CLUSTER_LOCK_HELD=1 "$root/docs/delivery/spikes/S10/code/run.sh" "$out"
fi

chart_version=1.8.0
chart_digest=sha256:e65b454d620129f3d7da2c16611a7f754e2d89b20b7a135dc5f0916a062f3a9e
jobs=${WDF_S10_JOBS:-3000}
ctx=kind-wdf
ns=wdf-infra
services=(frontend history matching worker)
slot=$(basename "$(dirname "$root")")
[[ $slot =~ ^[0-9]$ ]] || slot=0
fport=$((19000 + slot * 10 + 1))
mport=$((19000 + slot * 10 + 2))
k() { kubectl --context "$ctx" "$@"; }
say() { echo "[$(date +%T)] $*" >&2; }
figure() { echo "figure $1 $2" | tee -a "$out/figures.txt"; }
: >"$out/figures.txt"
pids=()
figure host_mem_total_mib "$(awk '/^MemTotal:/ { print int($2 / 1024) }' /proc/meminfo)"
figure host_mem_available_mib "$(awk '/^MemAvailable:/ { print int($2 / 1024) }' /proc/meminfo)"
figure docker_version "$(docker version --format '{{.Server.Version}}')"

# The port-forwards run as kubectl itself, not through k, so the pid killed is the process that holds the port.
# shellcheck disable=SC2329
cleanup() {
    for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done
    if [ "${WDF_S10_KEEP:-}" != 1 ]; then
        say "destroying the cluster"
        scripts/nuke.sh >"$out/nuke.log" 2>&1 || say "nuke failed, see $out/nuke.log"
    fi
}
trap cleanup EXIT

say "cluster up"
scripts/up.sh >"$out/up.log" 2>&1 || { say "up failed, see $out/up.log"; exit 1; }
say "ci profile modules"
scripts/tilt/run.sh ci --timeout 30m >"$out/tilt.log" 2>&1 || { say "tilt failed, see $out/tilt.log"; exit 1; }

say "chart $chart_version"
chart=$out/chart
rm -rf "$chart" && mkdir -p "$chart"
helm pull temporal --repo https://go.temporal.io/helm-charts --version "$chart_version" -d "$chart" >/dev/null || exit 1
got=sha256:$(sha256sum "$chart/temporal-$chart_version.tgz" | cut -d' ' -f1)
[ "$got" = "$chart_digest" ] || { say "chart digest $got, expected $chart_digest"; exit 1; }
figure chart_digest "$got"
helm template temporal "$chart/temporal-$chart_version.tgz" --namespace "$ns" --skip-tests \
    -f docs/delivery/spikes/S10/code/values.yaml >"$out/manifests.yaml" || exit 1
yq 'select(.kind == "Job")' "$out/manifests.yaml" >"$out/schema-job.yaml"
yq 'select(.kind != "Job")' "$out/manifests.yaml" >"$out/server.yaml"

k -n "$ns" apply -f "$out/schema-job.yaml" || exit 1
job=$(yq '.metadata.name' "$out/schema-job.yaml")
[ "$(wc -l <<<"$job")" -eq 1 ] || { say "expected one Job, got: $job"; exit 1; }
k -n "$ns" wait --for=condition=complete "job/$job" --timeout=600s || { k -n "$ns" logs "job/$job" --all-containers; exit 1; }
k -n "$ns" apply -f "$out/server.yaml" || exit 1
for s in "${services[@]}"; do
    k -n "$ns" rollout status "deployment/temporal-$s" --timeout=600s || exit 1
done
k -n "$ns" get pods -o wide | tee "$out/pods.txt"
for f in used hard; do
    figure "quota_$f" "$(k -n "$ns" get resourcequota pool-capacity -o jsonpath="{.status.$f.limits\.cpu}/{.status.$f.limits\.memory}")"
done

pod() { k -n "$ns" get pod -l "app.kubernetes.io/component=$1" -o jsonpath='{.items[0].metadata.name}'; }
declare -A pods
for s in "${services[@]}"; do pods[$s]=$(pod "$s"); done
# The memory and CPU limits come from the live pods, so the figures are what the scheduler admitted.
mem_sum=0 cpu_sum=0
for s in "${services[@]}"; do
    read -r mem cpu < <(k -n "$ns" get pod "${pods[$s]}" -o jsonpath='{.spec.containers[0].resources.limits.memory} {.spec.containers[0].resources.limits.cpu}')
    figure "limit_${s}" "$mem/$cpu"
    mem_sum=$((mem_sum + ${mem%Mi}))
    cpu_sum=$((cpu_sum + ${cpu%m}))
done
for s in "${services[@]}"; do
    figure "replicas_${s}" "$(k -n "$ns" get deployment "temporal-$s" -o jsonpath='{.status.readyReplicas}')"
done
# Anything of the release beyond the four services and the finished schema job, such as web or admin tools.
others=$(k -n "$ns" get pods -l app.kubernetes.io/name=temporal --no-headers \
    -o custom-columns=C:.metadata.labels.app\\.kubernetes\\.io/component | grep -cvxE 'frontend|history|matching|worker|database')
figure other_release_pods "$others"
figure limit_memory_sum_mib "$mem_sum"
figure limit_cpu_sum_m "$cpu_sum"
image=$(k -n "$ns" get pod "${pods[history]}" -o jsonpath='{.status.containerStatuses[0].imageID}')
figure server_image "$image"
k -n "$ns" exec "${pods[history]}" -- cat /etc/temporal/dynamic_config/dynamic_config.yaml | tee "$out/dynamic_config.yaml"

events() { k -n "$ns" exec "$1" -- cat /sys/fs/cgroup/memory.events; }
for s in "${services[@]}"; do events "${pods[$s]}" >"$out/events-before-$s.txt"; done

kubectl --context "$ctx" -n "$ns" port-forward --address 127.0.0.1 svc/temporal-frontend "$fport:7233" \
    >"$out/pf-frontend.log" 2>&1 &
pids+=($!)
kubectl --context "$ctx" -n "$ns" port-forward --address 127.0.0.1 "pod/${pods[history]}" "$mport:9090" \
    >"$out/pf-metrics.log" 2>&1 &
pids+=($!)
sleep 3

# One line per pod and sample: time, service, memory.current, CPU usage in microseconds, throttled periods.
# The time is read inside the container next to cpu.stat, so the exec latency does not skew the CPU rate.
# One line per sample for the cache: time, cache, the mutable state cache value.
sample() {
    while :; do
        for s in "${services[@]}"; do
            timeout 10 kubectl --context "$ctx" -n "$ns" exec "${pods[$s]}" -- \
                sh -c 'cat /proc/uptime /sys/fs/cgroup/memory.current /sys/fs/cgroup/cpu.stat' |
                awk -v t="$(date +%s)" -v s="$s" 'NR == 1 { up = $1 } NR == 2 { m = $1 } /^usage_usec/ { u = $2 }
                    /^nr_throttled/ { n = $2 } END { if (m != "") print t, s, m, u, n, up }'
        done
        now=$(date +%s)
        curl -s -m 5 "http://127.0.0.1:$mport/metrics" | grep '^cache_usage' >"$out/cache-last.txt"
        v=$(awk '/cache_type="mutablestate"/ { f = 1; if ($NF > m) m = $NF } END { if (f) print m + 0 }' "$out/cache-last.txt")
        echo "$now cache ${v:-none}"
        sleep 5
    done
}
sample >"$out/samples.txt" 2>"$out/sample.err" &
pids+=($!)

say "load: $jobs jobs"
figure load_start "$(date +%s)"
uv run --frozen python docs/delivery/spikes/S10/code/load.py "127.0.0.1:$fport" "$jobs" | tee "$out/load.txt"
load_rc=${PIPESTATUS[0]}
sleep 10
kill "${pids[2]}" 2>/dev/null

grep -h figure "$out/load.txt" >>"$out/figures.txt"
grep '^cache_usage' "$out/cache-last.txt" | sed 's/ [0-9.e+]*$//' | sort -u | while read -r series; do figure cache_series "$series"; done
cache_max=$(awk '$2 == "cache" && $3 != "none" { if ($3 > m) m = $3 } END { print m + 0 }' "$out/samples.txt")
figure cache_mutablestate_max "$cache_max"
figure cache_samples "$(awk '$2 == "cache" && $3 != "none"' "$out/samples.txt" | wc -l)"
figure cache_first_at_max "$(awk -v m="$cache_max" '$2 == "cache" && $3 == m { print $1; exit }' "$out/samples.txt")"
figure cache_samples_at_max "$(awk -v m="$cache_max" '$2 == "cache" && $3 == m' "$out/samples.txt" | wc -l)"

pass=1
[ "$mem_sum" -le 1024 ] && [ "$cpu_sum" -le 1000 ] && [ "$others" -eq 0 ] || pass=0
for s in "${services[@]}"; do grep -qx "figure replicas_$s 1" "$out/figures.txt" || pass=0; done
[ "$load_rc" -eq 0 ] || pass=0
[ "$cache_max" -gt 0 ] && [ "$cache_max" -le 1000 ] || pass=0
for s in "${services[@]}"; do
    p=${pods[$s]}
    events "$p" >"$out/events-after-$s.txt"
    before=$(awk '$1 == "max" { print $2 }' "$out/events-before-$s.txt")
    after=$(awk '$1 == "max" { print $2 }' "$out/events-after-$s.txt")
    oom=$(awk '$1 == "oom_kill" { print $2 }' "$out/events-after-$s.txt")
    figure "memory_events_max_${s}" "$before->$after"
    figure "oom_kill_${s}" "$oom"
    restarts=$(k -n "$ns" get pod "$p" -o jsonpath='{.status.containerStatuses[0].restartCount}')
    figure "restarts_${s}" "$restarts"
    [ "$before" = 0 ] && [ "$after" = 0 ] && [ "$oom" = 0 ] && [ "$restarts" = 0 ] || pass=0
    figure "memory_peak_mib_${s}" "$(k -n "$ns" exec "$p" -- cat /sys/fs/cgroup/memory.peak | awk '{ printf "%.0f", $1 / 1048576 }')"
    # Rates use the container's uptime clock. The peak is the highest rate between two samples, the mean is over every
    # sample, and the throttled figure counts the CFS periods throttled over the samples.
    awk -v s="$s" '$2 == s { if (t != "") { r = ($4 - u) / ($6 - t) / 1000; if (r > m) m = r } else { t0 = $6; u0 = $4; n0 = $5 }
            t = $6; u = $4; n = $5 }
        END { printf "figure cpu_peak_m_%s %.0f\nfigure cpu_mean_m_%s %.0f\nfigure cpu_throttled_periods_%s %d\nfigure sample_seconds_%s %.0f\n",
            s, m, s, (u - u0) / (t - t0) / 1000, s, n - n0, s, t - t0 }' \
        "$out/samples.txt" | tee -a "$out/figures.txt"
done
k -n "$ns" logs "${pods[history]}" >"$out/history.log" 2>&1
if [ "$pass" -eq 1 ]; then figure verdict pass; else figure verdict fail; fi
