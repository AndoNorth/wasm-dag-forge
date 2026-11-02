-- A running job always has a workflow, so a running row that no open workflow belongs to has lost it.
-- A job that never started has no workflow, which is why no other status is compared.
SELECT job_id::text AS key
FROM accepted_job
WHERE status = 'running'
  AND NOT (%(workflow_prefix)s || job_id::text) = ANY(%(open_workflows)s::text[])
