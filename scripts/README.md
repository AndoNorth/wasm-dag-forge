# Scripts

Shell entrypoints behind the `just` recipes: the quality gate, cluster lifecycle, host checks and test runners.
Each script is a thin driver that is run through `just` inside the Nix dev shell, so the same commands work locally and in CI.
Product logic belongs in `packages/`, and cluster manifests belong in `deploy/`.
