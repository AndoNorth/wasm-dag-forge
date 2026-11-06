# Contracts

Machine-checkable interfaces shared between components: schemas, version files, error tables and test vectors.
A contract states what crosses a boundary, while the code that produces or consumes it lives in `packages/`.
Deployment configuration belongs in `deploy/`, and a contract change is reviewed as a change to the interface itself.

The map of every contract, with its owner, consumers and role, is [docs/contracts.md](../docs/contracts.md).
Adding a contract, or changing its owner or consumers, updates its row in that map and the diagram in the same PR, and `scripts/check-contracts-map.sh` fails the build when a row is missing or stale.
