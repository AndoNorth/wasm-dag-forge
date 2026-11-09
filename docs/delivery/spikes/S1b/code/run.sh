#!/usr/bin/env bash
# Heavy-compile spike: builds a stand-in workload bundle and generated heavy bundles with componentize-py in
# containers capped like the `ci` compile slot, after an install step that had network, with the build step at
# `--network none`, and probes `unshare --user --net` in that container. Prints one `figure <name> <value>` line
# per number of docs/delivery/spikes/S1b/heavy-compile.md. Run in the dev shell:
#   nix develop -c docs/delivery/spikes/S1b/code/run.sh [out-dir]
# componentize-py goes into a throwaway venv in the out dir, never into uv.lock. One container at a time, with the
# run label, each removed after its step. Exit 0 the run completed (the verdict is a figure), 1 a step failed,
# 2 usage.
set -uo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)
cd "$root" || exit 2
[ $# -le 1 ] || { echo "usage: run.sh [out-dir]" >&2; exit 2; }
out=$(realpath -m "${1:-$(mktemp -d "${TMPDIR:-/tmp}/wdf-s1b.XXXXXX")}")
mkdir -p "$out"

code=docs/delivery/spikes/S1b/code
image="python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72"
wasi_url=https://github.com/WebAssembly/WASI/archive/refs/tags/v0.2.9.tar.gz
wasi_sha256=8c52624aebed805e29aa1e64053fd3171159bbe8a195e90982f87edae789a6e8
label=wdf.dev/verify-run
run_id="s1b-$(date -u +%Y%m%dT%H%M%SZ)"
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

say "run $run_id, out $out"
figure host.mem_total_mib "$(awk '/^MemTotal:/ { print int($2 / 1024) }' /proc/meminfo)"
figure host.mem_available_mib.before "$(awk '/^MemAvailable:/ { print int($2 / 1024) }' /proc/meminfo)"
figure host.cpus "$(nproc)"
figure host.docker "$(docker version --format '{{.Server.Version}}')"
figure host.kernel "$(uname -r)"
figure host.seccomp "$(docker info --format '{{json .SecurityOptions}}' | tr -d ' ')"
figure host.other_containers "$(docker ps -q | wc -l)"
figure tool.wasm_tools "$(wasm-tools --version | awk '{ print $2 }')"
figure tool.image_python "$(docker run --rm --label "$label=$run_id" --network none "$image" python3 -V | awk '{ print $2 }')"

say "fetch the WASI 0.2.9 WIT packages"
mkdir -p "$out/wit/deps"
curl -sSfL -o "$out/wasi.tar.gz" "$wasi_url" || exit 1
echo "$wasi_sha256  $out/wasi.tar.gz" | sha256sum -c --quiet || exit 1
tar -xzf "$out/wasi.tar.gz" -C "$out" || exit 1
for p in cli clocks filesystem io random sockets; do cp -r "$out/WASI-0.2.9/proposals/$p/wit" "$out/wit/deps/$p"; done
printf 'package wdf:task@1.0.0;\n\nworld task {\n  include wasi:cli/command@0.2.9;\n}\n' >"$out/wit/task.wit"

say "capped container steps"
python3 "$code/measure.py" --image "$image" --label "$label" --run-id "$run_id" --out "$out" \
    --user "$(id -u):$(id -g)" --runs "${S1B_RUNS:-3}" --standin 0.25 \
    --heavy 1.0 1.25 1.5 1.75 2.0 2.25 2.5 --over 3.0 4.0 --split 4.0:4 6.0:3 --hold 64 256 |
    tee -a "$out/figures.txt"
rc=${PIPESTATUS[0]}
[ "$rc" -eq 0 ] || say "a measurement step failed"

for wasm in "$out"/standin.*.wasm; do
    n=$(basename "$wasm" .wasm)
    ok=no
    wasm-tools component targets "$out/wit" -w task "$wasm" >/dev/null 2>&1 && ok=yes
    figure "$n.targets_ok" "$ok"
done

figure host.mem_available_mib.after "$(awk '/^MemAvailable:/ { print int($2 / 1024) }' /proc/meminfo)"
figure containers.left.this_run "$(docker ps -aq --filter "label=$label=$run_id" | wc -l)"
figure containers.left.label_all "$(docker ps -aq --filter "label=$label" | wc -l)"
[ "$rc" -eq 0 ] || exit 1
