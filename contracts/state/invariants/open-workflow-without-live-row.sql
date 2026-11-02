-- An open workflow must belong to a job that is still going, so a terminal row or no row at all is drift.
SELECT DISTINCT w.id AS key
FROM unnest(%(open_workflows)s::text[]) AS w (id)
LEFT JOIN accepted_job j ON %(workflow_prefix)s || j.job_id::text = w.id
WHERE j.job_id IS NULL OR j.status = ANY(%(terminal)s::text[])
