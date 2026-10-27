# Versions policy

Epic: https://github.com/AndoNorth/wasm-dag-forge/issues/1
Breakdown: [docs/epics/01-dev-environment.md](epics/01-dev-environment.md)

## WASM toolchain pins

`wasmtime` and `wasm-tools` come from hash-checked release archives in `nix/wasm-tools.nix`, not from nixpkgs.
nixos-26.05 carries wasmtime 45.0.2, which is not an LTS line, and an older wasm-tools.

| Tool | Version | Asset |
| --- | --- | --- |
| wasmtime | 48.0.7 (LTS line) | `wasmtime-v48.0.7-x86_64-linux.tar.xz` |
| wasm-tools | 1.262.0 | `wasm-tools-1.262.0-x86_64-linux.tar.gz` |

A bump edits `version`, `url` and `hash` for that tool in `nix/wasm-tools.nix`.
The hash is the SHA-256 of the release asset, taken from the release page and re-checked by downloading the asset.
The compile image (#11) uses the same versions, so a local build and a cluster build run the same tools.

`componentize-py` is not pinned here.
`uv.lock` belongs to the Python bootstrap lane, and the package has no consumer before #11.
#11 adds a hash-pinned `componentize-py` entry to `uv.lock` (E7).

## Where each pin lives

| Pin | File | Bump |
| --- | --- | --- |
| Shell tools (kind, kubectl, helm, tilt, just, uv, python, linters) | `flake.lock` | `nix flake update`, review the `just versions` diff |
| `wasmtime`, `wasm-tools` | `nix/wasm-tools.nix` | see above |
| Python dependencies | `uv.lock` | `uv lock --upgrade-package <name>` |
| kind node image, charts, images | `deploy/versions.yaml` | one edit |

A tool bump is a `flake.lock` update.
A chart or image bump is one edit to `deploy/versions.yaml`, setting both `version` and `digest` of the entry.
Every entry is `{version, digest}` and the digest is `sha256:` plus 64 hex characters.
`just versions --check` validates the file against `contracts/versions.schema.json` and exits 1 on a missing digest.
`just versions` prints the tool versions from the shell followed by the file's entries, one key per line.
The kind node image digest is the one published in the kind release notes for the pinned kind version, not whatever the tag points at today.

## First-party charts

Prefer first-party charts and images, pinned, over Bitnami or community-maintained ones.
The product choices stay with the epics that consume the file (#3, #6, #7).
