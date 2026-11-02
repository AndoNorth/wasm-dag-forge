# Contracts

Machine-checkable interfaces shared between components: schemas, version files, error tables and test vectors.
A contract states what crosses a boundary, while the code that produces or consumes it lives in `packages/`.
Deployment configuration belongs in `deploy/`, and a contract change is reviewed as a change to the interface itself.
