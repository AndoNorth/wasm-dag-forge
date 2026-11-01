#!/usr/bin/env bash
# Footprint spike for the state components: loads the app Postgres with TimescaleDB and the object store with its admin
# and lifecycle worker in capped containers, carries the Temporal and RabbitMQ figures of earlier spikes, and prints one
# `figure <name> <value>` line for every number of docs/spikes/S8-state-footprint.md. Run in the dev shell:
#   nix develop -c spikes/S8/run.sh [out-dir]
# Takes the cluster lock and one T1 lock file, runs one container at a time on the worker slot ports of 127.0.0.1 with
# the run label, and removes every container it started. Exit 0 the run completed (the verdict is a figure), 1 a step
# failed, 2 usage.
set -uo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root" || exit 2
[ $# -le 1 ] || { echo "usage: run.sh [out-dir]" >&2; exit 2; }
out=${1:-$(mktemp -d "${TMPDIR:-/tmp}/wdf-s8.XXXXXX")}
mkdir -p "$out"

# The cluster is the shared resource, so a measurement waits for whoever holds it.
if [ -z "${WDF_CLUSTER_LOCK_HELD:-}" ]; then
    exec flock /tmp/wdf-cluster.lock env WDF_CLUSTER_LOCK_HELD=1 "$0" "$out"
fi

timescale="timescale/timescaledb:2.31.0-pg16@sha256:322ae364eca3321cf2a48f1ef31213b87843413b3cf8feeebf54e999a26bb731"
seaweed="chrislusf/seaweedfs:4.51@sha256:f7a49e3f3972ebed5be2ed4befa4a0c68eb7bdddba6e692d42729ceb545134b8"
label=wdf.dev/verify-run
slot=${WDF_SLOT:-$(basename "$(dirname "$root")")}
[[ $slot =~ ^[0-9]$ ]] || slot=0
run_id="s8-$(date -u +%Y%m%dT%H%M%SZ)-$slot"
port() { echo $((19000 + slot * 10 + $1)); }
cap=1g
volume_mb=292
export PYTHONPATH="$root/spikes/S8"
say() { echo "[$(date +%T)] $*" >&2; }
avail() { awk '/^MemAvailable:/ { print int($2 / 1024) }' /proc/meminfo; }
figure() { echo "figure $1 $2" | tee -a "$out/figures.txt"; }
: >"$out/figures.txt"

# shellcheck disable=SC2329
cleanup() {
    docker ps -aq --filter "label=$label=$run_id" | xargs -r docker rm -fv >/dev/null 2>&1
    flock -u 9 2>/dev/null
}
trap cleanup EXIT

# One of the two T1 lock files of wdf_itest, so this run counts toward the two containers across the host.
deadline=$(($(date +%s) + 1800))
while :; do
    for i in 0 1; do
        exec 9>"/tmp/wdf-t1.$i.lock"
        flock -n 9 && { echo "pid=$$ run=$run_id" >"/tmp/wdf-t1.$i.lock"; break 2; }
    done
    [ "$(date +%s)" -lt "$deadline" ] || { say "no T1 lock file became free"; exit 1; }
    sleep 2
done

docker info >/dev/null 2>&1 || { say "docker daemon unreachable"; exit 1; }
for ref in "$timescale" "$seaweed"; do docker image inspect "$ref" >/dev/null 2>&1 || docker pull -q "$ref" >/dev/null || exit 1; done

say "run $run_id slot $slot ports $(port 0) and $(port 1), out $out"
figure host.mem_total_mib "$(awk '/^MemTotal:/ { print int($2 / 1024) }' /proc/meminfo)"
figure host.mem_available_mib.before "$(avail)"
figure host.cpus "$(nproc)"
figure host.docker "$(docker version --format '{{.Server.Version}}')"
figure host.kernel "$(uname -r)"
figure host.other_containers "$(docker ps -q | wc -l)"

uv run --frozen python spikes/S8/images.py "$timescale" "$seaweed" | tee -a "$out/figures.txt"
[ "${PIPESTATUS[0]}" -eq 0 ] || exit 1

# Credentials are generated for the run and live only in the run directory and the container environment.
password=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')
python3 - "$out/creds.json" <<'PY'
import json
import secrets
import sys

names = ["admin", "submit", "compile", "simulate", "aggregate"]
json.dump({n: {"ak": f"wdf{n}ak", "sk": secrets.token_hex(16)} for n in names}, open(sys.argv[1], "w"))
PY

run_pg() {
    say "app Postgres with TimescaleDB"
    local t0
    t0=$(date +%s.%N)
    docker run -d --name "wdf-$run_id-pg" --label "$label=$run_id" --memory "$cap" --memory-swap "$cap" \
        -p "127.0.0.1:$(port 0):5432" -e "POSTGRES_PASSWORD=$password" "$timescale" \
        postgres -c timescaledb.telemetry_level=off >/dev/null || return 1
    S8_CONTAINER="wdf-$run_id-pg" S8_PORT=$(port 0) S8_PASSWORD=$password S8_T0=$t0 \
        uv run --frozen python spikes/S8/pg_measure.py | tee -a "$out/figures.txt"
    local rc=${PIPESTATUS[0]}
    docker logs "wdf-$run_id-pg" >"$out/pg.log" 2>&1
    docker rm -fv "wdf-$run_id-pg" >/dev/null
    return "$rc"
}

# The variant `default` runs the store as shipped, `tuned` bounds the Go heap with GOMEMLIMIT and skips the contract.
run_store() {
    local variant=$1 prefix=store gomem="" skip=""
    [ "$variant" = default ] || { prefix=store_tuned; gomem=${S8_GOMEMLIMIT:-512MiB}; skip=1; }
    say "object store with admin and lifecycle worker, variant $variant"
    local t0 identities
    identities=$(python3 - "$out/creds.json" spikes/S8/roles.json <<'PY'
import json
import sys

creds = json.load(open(sys.argv[1]))
roles = json.load(open(sys.argv[2]))["roles"]
admin = {"name": "admin", "credentials": [{"accessKey": creds["admin"]["ak"], "secretKey": creds["admin"]["sk"]}],
         "actions": ["Admin", "Read", "Write", "List", "Tagging"]}
ids = [admin]
for name, role in roles.items():
    acts = [f"{a}:{b}" for b in role["read"] for a in ("Read", "List")] + [f"Write:{b}" for b in role["write"]]
    ids.append({"name": name, "credentials": [{"accessKey": creds[name]["ak"], "secretKey": creds[name]["sk"]}],
                "actions": acts})
print(json.dumps({"identities": ids}))
PY
    )
    t0=$(date +%s.%N)
    docker run -d --name "wdf-$run_id-$prefix" --label "$label=$run_id" --memory "$cap" --memory-swap "$cap" \
        -p "127.0.0.1:$(port 1):8333" -e "S3_JSON=$identities" ${gomem:+-e "GOMEMLIMIT=$gomem"} --entrypoint sh "$seaweed" -c '
printf %s "$S3_JSON" >/tmp/s3.json
mkdir -p /data /tmp/admin /tmp/worker
weed server -dir=/data -s3 -s3.config=/tmp/s3.json -master.volumeSizeLimitMB='"$volume_mb"' -volume.max=0 &
sleep 12
weed admin -port=12535 -master=localhost:9333 -dataDir=/tmp/admin >/tmp/admin.log 2>&1 &
sleep 4
weed worker -admin=localhost:12535 -jobType=s3_lifecycle -workingDir=/tmp/worker >/tmp/worker.log 2>&1 &
wait' >/dev/null || return 1
    S8_CONTAINER="wdf-$run_id-$prefix" S8_PORT=$(port 1) S8_CREDS="$out/creds.json" S8_T0=$t0 \
        S8_VOLUME_MB=$volume_mb S8_PREFIX=$prefix S8_SKIP_CONTRACT=$skip \
        uv run --no-project --quiet --with boto3==1.43.119 --with pyyaml python spikes/S8/store_measure.py |
        tee -a "$out/figures.txt"
    local rc=${PIPESTATUS[0]}
    docker logs "wdf-$run_id-$prefix" >"$out/$prefix.log" 2>&1
    docker exec "wdf-$run_id-$prefix" sh -c 'tail -n 30 /tmp/admin.log /tmp/worker.log' >>"$out/$prefix.log" 2>&1
    docker inspect --format 'oom_killed={{.State.OOMKilled}} status={{.State.Status}} exit={{.State.ExitCode}}' \
        "wdf-$run_id-$prefix" >>"$out/$prefix.log" 2>&1
    docker rm -fv "wdf-$run_id-$prefix" >/dev/null
    return "$rc"
}

rc=0
case "${S8_ONLY:-all}" in
    all) run_pg && run_store default && run_store tuned || rc=1 ;;
    pg) run_pg || rc=1 ;;
    store) run_store default && run_store tuned || rc=1 ;;
    tuned) run_store tuned || rc=1 ;;
esac
[ "$rc" -eq 0 ] || say "a measurement step failed"
if [ "${S8_ONLY:-all}" = all ] && [ "$rc" -eq 0 ]; then
    uv run --frozen python spikes/S8/node.py "$out/figures.txt" | tee -a "$out/figures.txt"
    [ "${PIPESTATUS[0]}" -eq 0 ] || rc=1
fi

figure host.mem_available_mib.after "$(avail)"
figure containers.left.this_run "$(docker ps -aq --filter "label=$label=$run_id" | wc -l)"
figure containers.left.label_all "$(docker ps -aq --filter "label=$label" | wc -l)"
exit "$rc"
