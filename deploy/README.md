# Deploy

Everything that runs on the local Kubernetes cluster: cluster presets, namespaces, per-component modules and the pinned versions of charts and images.
Each component has a module that Tilt loads, and the presets size the cluster those modules run on.
Application code belongs in `packages/`, the interfaces between components in `contracts/`, and the tooling that drives the cluster in `scripts/`.
