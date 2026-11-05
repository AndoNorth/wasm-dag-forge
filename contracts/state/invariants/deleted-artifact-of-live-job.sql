-- A soft deleted row that a job which can still run holds. A use row holds a blob while its job is
-- not terminal, or, with no job row yet, while its last link is younger than the unreferenced grace.
-- An attempt-scoped row of a job that is not terminal holds its job's output, except a checkpoint,
-- which a prune removes from a running job.
SELECT a.uri AS key
FROM artifact a
WHERE a.deleted_at IS NOT NULL
  AND (
    EXISTS (
      SELECT 1
      FROM artifact_use u
      LEFT JOIN accepted_job j ON j.job_id = u.job_id
      LEFT JOIN job_status s ON s.status = j.status
      WHERE u.artifact_row_id = a.row_id
        AND CASE WHEN j.job_id IS NULL THEN u.last_linked_at > now() - %(unreferenced_grace)s
                 ELSE NOT coalesce(s.terminal, false) END
    )
    OR (
      a.producer_job IS NOT NULL AND a.kind <> 'checkpoint'
      AND EXISTS (
        SELECT 1
        FROM accepted_job j JOIN job_status s ON s.status = j.status
        WHERE j.job_id = a.producer_job AND NOT s.terminal
      )
    )
  )
