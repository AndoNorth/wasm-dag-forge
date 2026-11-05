-- Progress of the artifact reaper, written only by its role. Expand only: two new tables.
-- A done marker is set when a pass finds the listing of a job's prefix empty after deleting, so a
-- pass over a job that is done never lists it again.
CREATE TABLE reaper_job (
    job_id uuid PRIMARY KEY REFERENCES accepted_job (job_id),
    results_done_at timestamptz,
    checkpoints_done_at timestamptz
);

-- The continuation of a sweep that is bounded to a page of the listing per run.
CREATE TABLE reaper_cursor (
    name text PRIMARY KEY,
    position text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
