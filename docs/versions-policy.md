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
