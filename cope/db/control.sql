CREATE TABLE IF NOT EXISTS control_tokens (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  scopes TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS control_operations (
  id TEXT PRIMARY KEY,
  actor TEXT NOT NULL,
  token_id TEXT REFERENCES control_tokens(id),
  scopes TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  source_request_hash TEXT,
  title TEXT NOT NULL,
  plan TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('planned', 'pending', 'running', 'waiting', 'failed', 'succeeded', 'cancelled')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  lease_owner TEXT,
  lease_until TIMESTAMPTZ,
  cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
  error TEXT NOT NULL DEFAULT '',
  UNIQUE(actor, idempotency_key)
);

CREATE TABLE IF NOT EXISTS control_steps (
  operation_id TEXT NOT NULL REFERENCES control_operations(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  position INTEGER NOT NULL,
  action TEXT NOT NULL,
  input TEXT NOT NULL,
  dependencies TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'waiting', 'failed', 'succeeded', 'cancelled')),
  attempts INTEGER NOT NULL DEFAULT 0,
  result TEXT,
  error TEXT NOT NULL DEFAULT '',
  available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  PRIMARY KEY(operation_id, key)
);

CREATE TABLE IF NOT EXISTS control_events (
  id BIGSERIAL PRIMARY KEY,
  operation_id TEXT REFERENCES control_operations(id) ON DELETE CASCADE,
  actor TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS control_presets (
  key TEXT NOT NULL,
  revision INTEGER NOT NULL,
  name TEXT NOT NULL,
  rating_list_id BIGINT NOT NULL REFERENCES rating_lists(id),
  category TEXT NOT NULL CHECK (category IN ('bullet', 'rapid', 'classical')),
  variant TEXT NOT NULL CHECK (variant IN ('standard', 'dfrc')),
  config TEXT NOT NULL,
  opponent_count INTEGER NOT NULL CHECK (opponent_count BETWEEN 1 AND 499),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(key, revision)
);

CREATE TABLE IF NOT EXISTS control_engine_capabilities (
  build_hash TEXT PRIMARY KEY,
  dfrc TEXT NOT NULL CHECK (dfrc IN ('supported', 'unsupported', 'unknown')),
  evidence TEXT NOT NULL,
  actor TEXT NOT NULL,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

ALTER TABLE engine_acknowledged_queue ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ;
ALTER TABLE engine_acknowledged_queue ADD COLUMN IF NOT EXISTS intake_operation_id TEXT REFERENCES control_operations(id);
ALTER TABLE engine_acknowledged_queue ADD COLUMN IF NOT EXISTS engine_version_id BIGINT REFERENCES engine_versions(id) ON DELETE SET NULL;
ALTER TABLE engine_waiting_test_queue ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ;
ALTER TABLE engine_waiting_test_queue ADD COLUMN IF NOT EXISTS preset_key TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE engine_waiting_test_queue ADD COLUMN IF NOT EXISTS preset_revision INTEGER;
ALTER TABLE engine_waiting_test_queue ADD COLUMN IF NOT EXISTS operation_id TEXT REFERENCES control_operations(id);
ALTER TABLE engine_waiting_test_queue ADD COLUMN IF NOT EXISTS tournament_id BIGINT REFERENCES tournaments(id) ON DELETE SET NULL;
ALTER TABLE engine_waiting_test_queue ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'queued';
ALTER TABLE engine_waiting_test_queue ADD COLUMN IF NOT EXISTS reason TEXT NOT NULL DEFAULT '';
ALTER TABLE engine_waiting_test_queue ADD COLUMN IF NOT EXISTS priority_group INTEGER NOT NULL DEFAULT 0;
ALTER TABLE engine_waiting_test_queue ADD COLUMN IF NOT EXISTS duration_ms BIGINT NOT NULL DEFAULT 0;
ALTER TABLE engine_waiting_test_queue ADD COLUMN IF NOT EXISTS build_hash TEXT;
ALTER TABLE engine_waiting_test_queue ADD COLUMN IF NOT EXISTS config TEXT;
ALTER TABLE engine_waiting_test_queue DROP CONSTRAINT IF EXISTS engine_waiting_test_queue_engine_version_id_rating_list_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_engine_queue_plan ON engine_waiting_test_queue(engine_version_id, rating_list_id, preset_key);

CREATE TABLE IF NOT EXISTS control_queue_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  revision BIGINT NOT NULL DEFAULT 0
);
INSERT INTO control_queue_state(id) VALUES (1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS control_tournament_builds (
  tournament_id BIGINT NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
  engine_version_id BIGINT NOT NULL REFERENCES engine_versions(id),
  build_hash TEXT NOT NULL,
  engine_spec TEXT NOT NULL,
  PRIMARY KEY(tournament_id, engine_version_id)
);

CREATE INDEX IF NOT EXISTS idx_control_operations_claim ON control_operations(status, lease_until, created_at);
CREATE TABLE IF NOT EXISTS control_worker_settings (
  worker_id BIGINT PRIMARY KEY REFERENCES workers(id) ON DELETE CASCADE,
  draining BOOLEAN NOT NULL DEFAULT FALSE,
  changed_by TEXT NOT NULL,
  changed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS idx_control_events_operation ON control_events(operation_id, id);
CREATE INDEX IF NOT EXISTS idx_control_queue_dispatch ON engine_waiting_test_queue(tournament_id, priority_group, duration_ms, submitted_at, id);
