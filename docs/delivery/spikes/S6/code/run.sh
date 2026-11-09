#!/usr/bin/env bash
# Cold-start spike: builds a stand-in noop component with componentize-py against the WASI 0.2.9 command world,
# finds its guest linear memory, then runs it with the pinned wasmtime in a container capped like the `ci` simulate
# slot, with an empty and with a warm module cache, and prints one `figure <name> <value>` line per number of
# docs/delivery/spikes/S6/cold-start.md. Run in the dev shell:
#   nix develop -c docs/delivery/spikes/S6/code/run.sh [out-dir]
# componentize-py goes into a throwaway venv in the out dir, never into uv.lock. One container at a time, no network,
# with the run label, each removed after its run. Exit 0 the run completed (the verdict is a figure), 1 a step failed,
# 2 usage.
set -uo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)
cd "$root" || exit 2
[ $# -le 1 ] || { echo "usage: run.sh [out-dir]" >&2; exit 2; }
out=$(realpath -m "${1:-$(mktemp -d "${TMPDIR:-/tmp}/wdf-s6.XXXXXX")}")
mkdir -p "$out"

code=docs/delivery/spikes/S6/code
image="busybox:1.37.0@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec"
wasi_url=https://github.com/WebAssembly/WASI/archive/refs/tags/v0.2.9.tar.gz
wasi_sha256=8c52624aebed805e29aa1e64053fd3171159bbe8a195e90982f87edae789a6e8
componentize_py=0.25.1
label=wdf.dev/verify-run
run_id="s6-$(date -u +%Y%m%dT%H%M%SZ)"
say() { echo "[$(date +%T)] $*" >&2; }
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
docker image inspect "$image" >/dev/null 2>&1 || docker pull -q "$image" >/dev/null || exit 1
wasmtime=$(readlink -f "$(command -v wasmtime)")
mapfile -t closure < <(nix-store -qR "$wasmtime")

say "run $run_id, out $out"
figure host.mem_total_mib "$(awk '/^MemTotal:/ { print int($2 / 1024) }' /proc/meminfo)"
figure host.mem_available_mib.before "$(awk '/^MemAvailable:/ { print int($2 / 1024) }' /proc/meminfo)"
figure host.cpus "$(nproc)"
figure host.docker "$(docker version --format '{{.Server.Version}}')"
figure host.kernel "$(uname -r)"
figure host.other_containers "$(docker ps -q | wc -l)"
figure tool.wasmtime "$(wasmtime --version | awk '{ print $2 }')"
figure tool.wasm_tools "$(wasm-tools --version | awk '{ print $2 }')"

say "build the stand-in component"
build=$out/build
mkdir -p "$build/wit/deps" "$build/app"
curl -sSfL -o "$build/wasi.tar.gz" "$wasi_url" || exit 1
echo "$wasi_sha256  $build/wasi.tar.gz" | sha256sum -c --quiet || exit 1
tar -xzf "$build/wasi.tar.gz" -C "$build" || exit 1
for p in cli clocks filesystem io random sockets; do cp -r "$build/WASI-0.2.9/proposals/$p/wit" "$build/wit/deps/$p"; done
printf 'package wdf:task@1.0.0;\n\nworld task {\n  include wasi:cli/command@0.2.9;\n}\n' >"$build/wit/task.wit"
cp "$code/app.py" "$build/app/app.py"
uv venv -q --python 3.13 "$build/venv" && VIRTUAL_ENV=$build/venv uv pip install -q "componentize-py==$componentize_py" ||
    exit 1
figure tool.componentize_py "$("$build/venv/bin/componentize-py" --version | awk '{ print $2 }')"
t0=$(date +%s.%N)
(cd "$build/app" && "$build/venv/bin/componentize-py" -q -d ../wit -w task componentize app -o ../guest.wasm) || exit 1
figure build.componentize_s "$(echo "$(date +%s.%N) - $t0" | bc | xargs printf %.1f)"
wasm-tools component targets "$build/wit" -w task "$build/guest.wasm" || exit 1
figure build.component_bytes "$(stat -c %s "$build/guest.wasm")"
figure build.component_mib "$(echo "scale=3; $(stat -c %s "$build/guest.wasm") / 1048576" | bc | xargs printf %.1f)"

# The smallest -W max-memory-size that runs the guest is its peak linear memory, because the flag caps each memory.
say "find the guest linear memory"
probe=$out/probe
mkdir -p "$probe/in" "$probe/out" "$probe/cache"
printf '{"args":{},"seed":7,"workload":"noop"}\n' >"$probe/in/input.json"
printf '[cache]\ndirectory = "%s"\n' "$probe/cache" >"$probe/cache.toml"
fits() {
    wasmtime run -C cache=y -C "cache-config=$probe/cache.toml" -W "max-memory-size=$1" \
        --dir "$probe/in::/in" --dir "$probe/out::/out" "$build/guest.wasm" >/dev/null 2>&1
}
page=65536 lo=$page hi=$((256 * 1048576))
fits "$hi" || { say "guest does not run in 256 MiB of linear memory"; exit 1; }
while [ $((hi - lo)) -gt "$page" ]; do
    mid=$(((lo + hi) / 2 / page * page))
    if fits "$mid"; then hi=$mid; else lo=$mid; fi
done
figure guest.linear_memory_pages $((hi / page))
guest_mib=$(echo "scale=3; $hi / 1048576" | bc | xargs printf %.1f)
figure guest.linear_memory_mib "$guest_mib"

say "capped container runs"
python3 "$code/measure.py" --component "$build/guest.wasm" --wasmtime "$wasmtime" --closure "${closure[@]}" \
    --image "$image" --label "$label" --run-id "$run_id" --out "$out" --user "$(id -u):$(id -g)" \
    --guest-mib "$guest_mib" --runs "${S6_RUNS:-3}" \
    --variant default --variant serial=parallel-compilation=n --variant winch=compiler=winch \
    --hold 64 128 192 256 |
    tee -a "$out/figures.txt"
rc=${PIPESTATUS[0]}
[ "$rc" -eq 0 ] || say "a measurement step failed"

figure host.mem_available_mib.after "$(awk '/^MemAvailable:/ { print int($2 / 1024) }' /proc/meminfo)"
figure containers.left.this_run "$(docker ps -aq --filter "label=$label=$run_id" | wc -l)"
figure containers.left.label_all "$(docker ps -aq --filter "label=$label" | wc -l)"
[ "$rc" -eq 0 ] || exit 1
