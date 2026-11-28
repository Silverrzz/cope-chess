DROP INDEX IF EXISTS idx_event_fixture_workers_active_worker;

CREATE INDEX IF NOT EXISTS idx_event_fixture_workers_worker
  ON event_fixture_workers(worker_id);
