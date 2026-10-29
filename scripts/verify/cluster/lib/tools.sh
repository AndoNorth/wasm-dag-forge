# Sourced by the contract loaders: puts check-jsonschema and yq on PATH.
# Both come from the flake's nixpkgs pin, so the validator is pinned with the shell.
# When either is missing the calling script re-runs itself once inside a nix shell that provides them.
if ! command -v check-jsonschema >/dev/null || ! command -v yq >/dev/null; then
    exec nix shell --inputs-from "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)" \
        nixpkgs#check-jsonschema nixpkgs#yq-go -c "$0" "$@"
fi
