-- The lookup of one job's events reads an index instead of every chunk.
CREATE INDEX event_job_time ON event (job_id, event_time);
