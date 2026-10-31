# wdf-keys

Identity and cache key derivation for the platform, imported as `wdf_keys`.
The formulas are written in [contracts/events/README.md](../../contracts/events/README.md), and `contracts/events/vectors/keys.json` holds the vectors.

`wdf_keys.compile_cache_key` is the only place the compile cache key is computed.
The workflow and the compile worker both import it and never derive the key themselves.
