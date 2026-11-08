# wasm-dag-forge

A proof of concept for durable distributed job execution: batch jobs built from templates
(compile, simulate, aggregate), run as WASM on a fixed-size local Kubernetes cluster.

![System context](docs/architecture/01-system-context.drawio.svg)

## Worked example

One job from submission to completion.
Solid boxes have code or a contract today, dashed boxes are designed only.

![One job, end to end](docs/animations/media/job-walkthrough.gif)

Source and re-render steps: [docs/animations/](docs/animations/README.md).

## Getting started

See [docs/dev-setup.md](docs/dev-setup.md).
