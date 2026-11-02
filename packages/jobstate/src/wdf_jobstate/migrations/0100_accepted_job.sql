-- The accepted-jobs table. The status and class checks and the transition guard are in R__job_vocab.sql,
-- which is generated from the contracts and applied after every numbered file.
CREATE TABLE accepted_job (
    job_id uuid PRIMARY KEY,
    tenant text NOT NULL,
    submission_key text NOT NULL CHECK (char_length(submission_key) BETWEEN 1 AND 256),
    batch_id uuid NOT NULL,
    priority_class text NOT NULL,
    job_class text NOT NULL DEFAULT 'short',
    status text NOT NULL DEFAULT 'accepted',
    manifest_ref jsonb NOT NULL CHECK (jsonb_typeof(manifest_ref) = 'object' AND manifest_ref ->> 'kind' = 'manifest'),
    manifest_sha256 text GENERATED ALWAYS AS (manifest_ref ->> 'sha256') STORED,
    schema_version text NOT NULL,
    traceparent text NOT NULL,
    tracestate text,
    published_at timestamptz,
    seed bigint NOT NULL CHECK (seed >= 0 AND seed < 9007199254740992),
    parameters jsonb NOT NULL CHECK (jsonb_typeof(parameters) = 'object'),
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    requested_action text CHECK (requested_action IN ('pause', 'resume', 'cancel', 'terminate')),
    control_seq integer NOT NULL DEFAULT 0 CHECK (control_seq >= 0),
    control_delivered_at timestamptz,
    pending_patch_ref jsonb CHECK (jsonb_typeof(pending_patch_ref) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    terminal_at timestamptz,
    UNIQUE (tenant, submission_key)
);

CREATE INDEX accepted_job_batch_status ON accepted_job (tenant, batch_id, status);
-- The reconciler selects the jobs whose publish was never confirmed.
CREATE INDEX accepted_job_unpublished ON accepted_job (created_at) WHERE published_at IS NULL;
CREATE INDEX accepted_job_manifest_sha256 ON accepted_job (manifest_sha256);

CREATE FUNCTION accepted_job_touch() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END
$$;

CREATE TRIGGER accepted_job_touch BEFORE UPDATE ON accepted_job
    FOR EACH ROW EXECUTE FUNCTION accepted_job_touch();
