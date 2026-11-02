-- The artifact index: references to stored objects and compile cache entries share one table.
-- The kinds of the artifact layout and the producer rule of their addressing classes live only in
-- the generated repeatable file. The cache branch below holds the producer columns null.
CREATE TABLE artifact (
    row_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind text NOT NULL,
    uri text UNIQUE,
    bucket text,
    key text,
    sha256 text,
    size bigint,
    name text,
    -- The attempt identity as the reference carries it: no default, never derived by the store.
    producer_job uuid,
    producer_task text,
    producer_attempt text,
    created_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    cache_key text,
    -- A cache entry counts as a reference to its wasm, so a raw delete of that row is refused.
    wasm_uri text REFERENCES artifact (uri),
    componentize_py_version text,
    wit_version text,
    last_used_at timestamptz,
    CONSTRAINT artifact_sha256_shape CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT artifact_size_shape CHECK (size >= 0),
    CONSTRAINT artifact_name_shape CHECK (name ~ '^[^/]{1,255}$'),
    CONSTRAINT artifact_producer_task_shape CHECK (producer_task ~ '^[a-z0-9][a-z0-9._-]{0,62}$'),
    CONSTRAINT artifact_producer_attempt_shape CHECK (producer_attempt ~ '^r[0-9]+a[0-9]+$'),
    CONSTRAINT artifact_cache_key_shape CHECK (cache_key ~ '^[0-9a-f]{64}$'),
    CONSTRAINT artifact_reference_or_cache_entry CHECK (
        (
            kind <> 'compile-cache'
            AND uri IS NOT NULL AND bucket IS NOT NULL AND key IS NOT NULL
            AND sha256 IS NOT NULL AND size IS NOT NULL AND name IS NOT NULL
            AND cache_key IS NULL AND wasm_uri IS NULL AND componentize_py_version IS NULL
            AND wit_version IS NULL AND last_used_at IS NULL
        ) OR (
            kind = 'compile-cache'
            AND cache_key IS NOT NULL AND wasm_uri IS NOT NULL
            AND componentize_py_version IS NOT NULL AND wit_version IS NOT NULL
            AND last_used_at IS NOT NULL
            AND uri IS NULL AND bucket IS NULL AND key IS NULL
            AND sha256 IS NULL AND size IS NULL AND name IS NULL
            AND producer_job IS NULL AND producer_task IS NULL AND producer_attempt IS NULL
        )
    )
);

-- The first writer of a cache key wins.
CREATE UNIQUE INDEX artifact_cache_key ON artifact (cache_key) WHERE kind = 'compile-cache';
-- Finds the cache entries that name a wasm, for the pin check and the reference count.
CREATE INDEX artifact_wasm_uri ON artifact (wasm_uri) WHERE wasm_uri IS NOT NULL;
