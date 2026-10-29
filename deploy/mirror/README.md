# Package mirror

A static file server in `wdf-infra` that serves the `wheelhouse/` directory, enabled by the `ci` profile only.
The directory reaches the pod as a ConfigMap, so the fixture stays under 1 MiB and wheels beyond it come from the toolchain epic with a contract-change PR for the shape.

The fixture is one hash-pinned wheel, and `fixture-requirements.txt` carries its hash.
A consumer installs with `--no-index --find-links http://mirror.wdf-infra.svc --trusted-host mirror.wdf-infra.svc --require-hashes`.
The `--trusted-host` flag is what lets pip use a plain HTTP index.
A wheel that does not match its pinned hash is refused.

Only the compile pool installs packages, and it reaches the mirror only.
The aggregate pool gets no package index, mirror or egress configuration from any module.
