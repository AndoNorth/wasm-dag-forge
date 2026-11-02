-- A job has one class, so the class an accepted event carries must be the one of its row.
SELECT e.event_id AS key
FROM event e
JOIN accepted_job j ON j.job_id = e.job_id
WHERE e.type = 'dev.wdf.job.accepted.v1'
  AND e.body::jsonb ->> 'jobclass' IS DISTINCT FROM j.job_class
