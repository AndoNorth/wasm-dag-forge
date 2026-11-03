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
| Why the system has this shape | docs/architecture/ (draw.io diagrams today, a note per area is added as each domain executes) |
| How to run and test | docs/dev-setup.md |
| What a domain must deliver, and how delivery is run | the Delivery section below |

## Delivery
Only the captain, firstmate and agents managing the PoC need this material. General docs never describe it.
- [Delivery method](docs/delivery/method.md)
- [Delivery material](docs/delivery/): epic breakdown specs in epics/, spike results and throwaway spike code in spikes/, spec templates in templates/
- [Epic issues](https://github.com/AndoNorth/wasm-dag-forge/issues?q=label%3Aepic), tracked on [GitHub Project 2](https://github.com/users/AndoNorth/projects/2)

## Working here
Epics are broad specs. Before executing a domain, align the design with the captain and update
the area's architecture note. During execution, rely on the architecture and the epic breakdown spec in docs/delivery/epics/.
When a domain closes, refine the note and the epic with what was learned.

## Conventions
- This file stays shallow. Put detail in docs/ and link it.
- One source of truth per fact: link, never copy.
- Comments state intent only, and the code should speak for itself. Never reference issues, epics or tasks in comments.
- Diagrams are draw.io files in docs/architecture/.
- Design specs use the spec template.

## Running commands
Run project commands through `nix develop -c <command>`, so the pinned tools are used and the git hooks are installed.

## Safety
Never commit secrets, and bind published ports to 127.0.0.1 only.
