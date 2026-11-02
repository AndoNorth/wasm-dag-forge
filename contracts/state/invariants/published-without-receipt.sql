-- The broker receipt is set once the publish is confirmed, so a job that was published or moved on without one is drift.
SELECT j.job_id::text AS key
FROM accepted_job j
WHERE j.published_at IS NULL
  AND (
    j.status <> 'accepted'
    OR EXISTS (SELECT 1 FROM event e WHERE e.job_id = j.job_id AND e.type = 'dev.wdf.job.published.v1')
  )
