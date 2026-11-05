-- Expand only: a table and nullable columns, so the previous release keeps working against it.
-- The unique cache key index is untouched. A retired cache entry gets a new key instead.

-- The build-info a cache entry names, so a live entry pins it as it pins its wasm.
ALTER TABLE artifact ADD COLUMN build_info_uri text REFERENCES artifact (uri);
-- Set once the object is removed from the store. A purged row is never revived by a link.
ALTER TABLE artifact ADD COLUMN purged_at timestamptz;

ALTER TABLE artifact ADD CONSTRAINT artifact_build_info_on_cache_entry
    CHECK (build_info_uri IS NULL OR kind = 'compile-cache') NOT VALID;
ALTER TABLE artifact VALIDATE CONSTRAINT artifact_build_info_on_cache_entry;
ALTER TABLE artifact ADD CONSTRAINT artifact_purged_is_deleted
    CHECK (purged_at IS NULL OR (deleted_at IS NOT NULL AND kind <> 'compile-cache')) NOT VALID;
ALTER TABLE artifact VALIDATE CONSTRAINT artifact_purged_is_deleted;

CREATE INDEX artifact_build_info_uri ON artifact (build_info_uri) WHERE build_info_uri IS NOT NULL;

-- A job holds the content-addressed blobs that it was given. The job row need not exist yet.
CREATE TABLE artifact_use (
    job_id uuid NOT NULL,
    artifact_row_id bigint NOT NULL REFERENCES artifact (row_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    last_linked_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, artifact_row_id)
);

CREATE INDEX artifact_use_row ON artifact_use (artifact_row_id);
