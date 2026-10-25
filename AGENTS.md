# wasm-dag-forge

A proof of concept for durable distributed job execution: batch jobs built from templates
(compile, simulate, aggregate), run as WASM on a fixed-size local Kubernetes cluster.

## Shape
Temporal owns job state. RabbitMQ carries job intake only. Layer 1 admission releases jobs,
layer 2 placement picks a pool queue for each task. Postgres (TimescaleDB) and object storage hold the rest.
Detail: docs/architecture/.

## Where to look
| Need | Go to |
|------|-------|
| Why the system has this shape | docs/architecture/ (one note and diagram per area) |
| What a domain must deliver | the epic issue for that area (label `epic`, GitHub Project 2) |
| How to run and test | docs/dev-setup.md (written by the dev environment epic) |

## Working here
Epics are broad specs. Before executing a domain, align the design with the captain and update
the area's architecture note. During execution, rely on the architecture and the epic.
When a domain closes, refine the note and the epic with what was learned.

## Conventions
- This file stays shallow. Put detail in docs/ and link it.
- One source of truth per fact: link, never copy.
- Comments only where intent is ambiguous.
- Diagrams are draw.io files in docs/architecture/.
- Design specs use the spec template.

## Safety
Never commit secrets, and bind published ports to 127.0.0.1 only.
