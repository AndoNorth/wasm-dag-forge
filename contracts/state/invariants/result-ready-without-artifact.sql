-- A result that was announced as ready must have been indexed.
SELECT e.event_id AS key
FROM event e
WHERE e.type = 'dev.wdf.task.result.ready.v1'
  AND NOT EXISTS (SELECT 1 FROM artifact a WHERE a.uri = e.body::jsonb #>> '{data,result,uri}')
