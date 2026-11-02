-- Group roles and column grants. The writers table of contracts/state/README.md is the statement of
-- who writes what, and the grants below are that statement made enforceable at column level.
-- Repeatable: roles are cluster wide and one server holds many databases, so each role is created
-- only when absent, and every grant is revoked and given again so this file stays the whole truth.
-- The roles cannot log in. A service login joins the group of its writer.
-- A status value is outside what a grant can say: the transition trigger and the library keep it.

DO $$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'wdf_dispatch', 'wdf_control', 'wdf_admission', 'wdf_workflow',
        'wdf_artifacts', 'wdf_reaper', 'wdf_reader'
    ] LOOP
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = role_name) THEN
            BEGIN
                EXECUTE format('CREATE ROLE %I NOLOGIN', role_name);
            EXCEPTION WHEN duplicate_object THEN
                -- Another database of the same server created it between the check and the statement.
                NULL;
            END;
        END IF;
    END LOOP;
END
$$;

REVOKE ALL ON accepted_job, job_status, job_transition, event, artifact FROM
    wdf_dispatch, wdf_control, wdf_admission, wdf_workflow, wdf_artifacts, wdf_reaper, wdf_reader;
GRANT USAGE ON SCHEMA public TO
    wdf_dispatch, wdf_control, wdf_admission, wdf_workflow, wdf_artifacts, wdf_reaper, wdf_reader;

-- Every row writer reads the row it moves, and the guard trigger reads the transition table.
GRANT SELECT ON accepted_job, job_transition TO wdf_dispatch, wdf_control, wdf_admission, wdf_workflow;
GRANT SELECT ON job_status TO wdf_control, wdf_admission, wdf_workflow;

-- The submit sequence inserts the row at its defaults and sets the broker receipt.
GRANT INSERT (
    job_id, tenant, submission_key, batch_id, priority_class, job_class, manifest_ref,
    schema_version, traceparent, tracestate, seed, parameters, fingerprint
) ON accepted_job TO wdf_dispatch;
GRANT UPDATE (published_at) ON accepted_job TO wdf_dispatch;

-- A guarded move names the status, the terminal time and the requested action it may clear.
GRANT UPDATE (status, terminal_at, requested_action) ON accepted_job TO
    wdf_control, wdf_admission, wdf_workflow;
GRANT UPDATE (control_seq, control_delivered_at, pending_patch_ref) ON accepted_job TO wdf_control;

-- The event history is append only, so no role can update or delete an event.
-- The conflict check of the idempotent insert reads the key columns.
GRANT INSERT ON event TO wdf_dispatch, wdf_control, wdf_admission, wdf_workflow;
GRANT SELECT (event_id, event_time) ON event TO wdf_dispatch, wdf_control, wdf_admission, wdf_workflow;

GRANT SELECT ON artifact TO wdf_artifacts, wdf_reaper;
GRANT INSERT (
    kind, uri, bucket, key, sha256, size, name, producer_job, producer_task, producer_attempt,
    cache_key, wasm_uri, componentize_py_version, wit_version, last_used_at
) ON artifact TO wdf_artifacts;
GRANT UPDATE (last_used_at) ON artifact TO wdf_artifacts;
GRANT UPDATE (deleted_at) ON artifact TO wdf_reaper;

GRANT SELECT ON accepted_job, job_status, job_transition, event, artifact TO wdf_reader;
