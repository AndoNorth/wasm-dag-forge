-- The append-only event history. The body is text because jsonb reorders keys and the canonical bytes must survive.
-- The attempt and the task have no default: a row holds what the event carries and nothing is inferred.
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE event (
    event_id text NOT NULL,
    event_time timestamptz NOT NULL,
    type text NOT NULL,
    source text NOT NULL,
    tenant text NOT NULL,
    job_id uuid NOT NULL,
    task_id text,
    attempt integer,
    body text NOT NULL,
    CONSTRAINT event_id_shape CHECK (event_id ~ '^[^#]+#[^#]+$'),
    CONSTRAINT event_attempt_not_negative CHECK (attempt >= 0),
    CONSTRAINT event_text_not_empty CHECK (type <> '' AND source <> '' AND tenant <> '' AND body <> ''),
    CONSTRAINT event_key UNIQUE (event_id, event_time)
);

SELECT create_hypertable('event', 'event_time', chunk_time_interval => INTERVAL '1 day');

ALTER TABLE event SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'job_id',
    timescaledb.compress_orderby = 'event_time DESC'
);
