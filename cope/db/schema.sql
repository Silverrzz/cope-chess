CREATE TABLE IF NOT EXISTS schema_metadata (
  key TEXT PRIMARY KEY,
  value INTEGER NOT NULL
);

DO $$
BEGIN
  IF to_regclass('schema_migrations') IS NOT NULL THEN
    RAISE EXCEPTION 'incompatible database from an obsolete Cope architecture detected'
      USING HINT = 'Back up the existing database, then choose a new Docker Compose project name or reset its postgres-data volume.';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'engines' AND column_name = 'git_url') THEN
    RAISE EXCEPTION 'legacy source-built engine schema is unsupported'
      USING HINT = 'Back up the existing database, then choose a new Docker Compose project name or reset its postgres-data volume.';
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS engines (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  author TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

DROP TABLE IF EXISTS app_settings;

CREATE TABLE IF NOT EXISTS git_hosts (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  provider TEXT NOT NULL CHECK (provider IN ('github', 'gitlab')),
  base_url TEXT NOT NULL,
  api_url TEXT NOT NULL,
  access_token TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  created_at TEXT NOT NULL
);

INSERT INTO git_hosts (name, provider, base_url, api_url, access_token, enabled, created_at)
VALUES ('GitHub', 'github', 'https://github.com', 'https://api.github.com', '', 1, '1970-01-01T00:00:00+00:00')
ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS engine_versions (
  id BIGSERIAL PRIMARY KEY,
  engine_id BIGINT NOT NULL REFERENCES engines(id) ON DELETE CASCADE,
  version TEXT NOT NULL,
  git_host_id BIGINT REFERENCES git_hosts(id) ON DELETE SET NULL,
  repository_url TEXT NOT NULL,
  repository_full_name TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  source_kind TEXT NOT NULL CHECK (source_kind IN ('release', 'commit')),
  dockerfile_path TEXT NOT NULL DEFAULT '',
  dockerfile TEXT NOT NULL,
  build_hash TEXT NOT NULL CHECK (build_hash ~ '^[0-9a-f]{64}$'),
  uci_options TEXT NOT NULL DEFAULT '{}',
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  created_at TEXT NOT NULL,
  UNIQUE (engine_id, version)
);

ALTER TABLE engine_versions ADD COLUMN IF NOT EXISTS git_host_id BIGINT REFERENCES git_hosts(id) ON DELETE SET NULL;
ALTER TABLE engine_versions ADD COLUMN IF NOT EXISTS repository_url TEXT;
ALTER TABLE engine_versions ADD COLUMN IF NOT EXISTS repository_full_name TEXT;
ALTER TABLE engine_versions ADD COLUMN IF NOT EXISTS source_ref TEXT;
ALTER TABLE engine_versions ADD COLUMN IF NOT EXISTS source_kind TEXT;
ALTER TABLE engine_versions ADD COLUMN IF NOT EXISTS dockerfile_path TEXT NOT NULL DEFAULT '';
ALTER TABLE engine_versions ADD COLUMN IF NOT EXISTS dockerfile TEXT;
ALTER TABLE engine_versions ADD COLUMN IF NOT EXISTS build_hash TEXT;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'engine_versions' AND column_name = 'binary_filename') THEN
    ALTER TABLE engine_versions ALTER COLUMN binary_filename DROP NOT NULL;
    ALTER TABLE engine_versions ALTER COLUMN binary_sha256 DROP NOT NULL;
    ALTER TABLE engine_versions ALTER COLUMN binary_size DROP NOT NULL;
    ALTER TABLE engine_versions ALTER COLUMN storage_key DROP NOT NULL;
  END IF;
END $$;
UPDATE engine_versions
SET active = 0
WHERE repository_url IS NULL OR source_ref IS NULL OR dockerfile IS NULL OR build_hash IS NULL;

CREATE TABLE IF NOT EXISTS engine_artifacts (
  build_hash TEXT PRIMARY KEY CHECK (build_hash ~ '^[0-9a-f]{64}$'),
  artifact_sha256 TEXT NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
  artifact_size BIGINT NOT NULL CHECK (artifact_size > 0),
  artifact_format TEXT NOT NULL CHECK (artifact_format = 'cope-tar-gzip-v1'),
  entrypoint TEXT NOT NULL DEFAULT 'engine',
  platform TEXT NOT NULL DEFAULT 'linux-x86_64'
    CHECK (platform = 'linux-x86_64'),
  storage_key TEXT NOT NULL CHECK (storage_key ~ '^[0-9a-f]{64}$'),
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_engine_artifacts_sha256
  ON engine_artifacts(artifact_sha256);

CREATE TABLE IF NOT EXISTS tournaments (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  config TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'scheduled', 'running', 'paused', 'finished', 'aborted')),
  current_round INTEGER NOT NULL DEFAULT 0,
  worker_profile TEXT,
  created_at TEXT NOT NULL,
  scheduled_start_at TEXT,
  started_at TEXT,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id BIGSERIAL PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'),
  handler_key TEXT NOT NULL,
  handler_version INTEGER NOT NULL DEFAULT 1 CHECK (handler_version > 0),
  title TEXT NOT NULL,
  subtitle TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  rules TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN (
      'draft', 'announced', 'scheduled', 'live', 'intermission',
      'postponed', 'completed', 'cancelled'
    )),
  featured INTEGER NOT NULL DEFAULT 0 CHECK (featured IN (0, 1)),
  published_at TEXT,
  scheduled_start_at TEXT,
  scheduled_end_at TEXT,
  started_at TEXT,
  finished_at TEXT,
  theme TEXT NOT NULL DEFAULT '{}',
  config TEXT NOT NULL DEFAULT '{}',
  state TEXT NOT NULL DEFAULT '{}',
  revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_stages (
  id BIGSERIAL PRIMARY KEY,
  event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  stage_key TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'active', 'completed', 'cancelled')),
  position INTEGER NOT NULL CHECK (position >= 0),
  scheduled_start_at TEXT,
  scheduled_end_at TEXT,
  started_at TEXT,
  finished_at TEXT,
  metadata TEXT NOT NULL DEFAULT '{}',
  UNIQUE (event_id, stage_key),
  UNIQUE (event_id, position)
);

CREATE TABLE IF NOT EXISTS event_sessions (
  id BIGSERIAL PRIMARY KEY,
  event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  stage_id BIGINT REFERENCES event_stages(id) ON DELETE SET NULL,
  session_key TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN (
      'pending', 'scheduled', 'live', 'intermission',
      'completed', 'postponed', 'cancelled'
    )),
  position INTEGER NOT NULL CHECK (position >= 0),
  scheduled_start_at TEXT,
  scheduled_end_at TEXT,
  started_at TEXT,
  finished_at TEXT,
  metadata TEXT NOT NULL DEFAULT '{}',
  UNIQUE (event_id, session_key),
  UNIQUE (event_id, position)
);

CREATE TABLE IF NOT EXISTS event_cast_members (
  id BIGSERIAL PRIMARY KEY,
  event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  parent_id BIGINT REFERENCES event_cast_members(id) ON DELETE SET NULL,
  member_key TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('engine', 'team', 'person', 'other')),
  display_name TEXT NOT NULL,
  short_name TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'reserve', 'withdrawn', 'eliminated')),
  engine_version_id BIGINT REFERENCES engine_versions(id) ON DELETE SET NULL,
  profile TEXT NOT NULL DEFAULT '',
  avatar_url TEXT NOT NULL DEFAULT '',
  accent_color TEXT NOT NULL DEFAULT '',
  position INTEGER NOT NULL CHECK (position >= 0),
  metadata TEXT NOT NULL DEFAULT '{}',
  UNIQUE (event_id, member_key),
  UNIQUE (event_id, position)
);

CREATE TABLE IF NOT EXISTS event_contests (
  id BIGSERIAL PRIMARY KEY,
  event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  stage_id BIGINT REFERENCES event_stages(id) ON DELETE SET NULL,
  session_id BIGINT REFERENCES event_sessions(id) ON DELETE SET NULL,
  contest_key TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN (
      'pending', 'scheduled', 'live', 'intermission',
      'completed', 'postponed', 'cancelled'
    )),
  position INTEGER NOT NULL CHECK (position >= 0),
  scheduled_start_at TEXT,
  scheduled_end_at TEXT,
  started_at TEXT,
  finished_at TEXT,
  result TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT '{}',
  metadata TEXT NOT NULL DEFAULT '{}',
  UNIQUE (event_id, contest_key),
  UNIQUE (event_id, position)
);

CREATE TABLE IF NOT EXISTS event_contest_cast (
  contest_id BIGINT NOT NULL REFERENCES event_contests(id) ON DELETE CASCADE,
  cast_member_id BIGINT NOT NULL REFERENCES event_cast_members(id) ON DELETE CASCADE,
  side TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT '',
  position INTEGER NOT NULL CHECK (position >= 0),
  metadata TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (contest_id, cast_member_id),
  UNIQUE (contest_id, position)
);

CREATE TABLE IF NOT EXISTS event_updates (
  id BIGSERIAL PRIMARY KEY,
  event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  kind TEXT NOT NULL
    CHECK (kind IN ('announcement', 'schedule', 'incident', 'result', 'milestone')),
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
  occurred_at TEXT NOT NULL,
  published_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_awards (
  id BIGSERIAL PRIMARY KEY,
  event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  award_key TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  recipient_cast_id BIGINT REFERENCES event_cast_members(id) ON DELETE SET NULL,
  recipient_label TEXT NOT NULL DEFAULT '',
  position INTEGER NOT NULL CHECK (position >= 0),
  awarded_at TEXT,
  metadata TEXT NOT NULL DEFAULT '{}',
  UNIQUE (event_id, award_key),
  UNIQUE (event_id, position)
);

CREATE TABLE IF NOT EXISTS event_chat_settings (
  event_id BIGINT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
  enabled INTEGER CHECK (enabled IS NULL OR enabled IN (0, 1)),
  slowmode_seconds INTEGER CHECK (slowmode_seconds IS NULL OR slowmode_seconds >= 0),
  max_message_length INTEGER CHECK (max_message_length IS NULL OR max_message_length > 0),
  allow_anonymous_names INTEGER CHECK (
    allow_anonymous_names IS NULL OR allow_anonymous_names IN (0, 1)
  ),
  retention_days INTEGER CHECK (retention_days IS NULL OR retention_days > 0)
);

CREATE TABLE IF NOT EXISTS engine_relay_fixtures (
  id BIGSERIAL PRIMARY KEY,
  event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  tournament_id BIGINT NOT NULL UNIQUE REFERENCES tournaments(id) ON DELETE CASCADE,
  team_a_id BIGINT NOT NULL REFERENCES event_cast_members(id),
  team_b_id BIGINT NOT NULL REFERENCES event_cast_members(id),
  anchor_a_engine_id BIGINT NOT NULL REFERENCES engine_versions(id),
  anchor_b_engine_id BIGINT NOT NULL REFERENCES engine_versions(id),
  kibitzer_engine_id BIGINT REFERENCES engine_versions(id),
  kibitzer_threads INTEGER CHECK (kibitzer_threads IS NULL OR kibitzer_threads > 0),
  kibitzer_hash_mb INTEGER CHECK (kibitzer_hash_mb IS NULL OR kibitzer_hash_mb > 0),
  title TEXT NOT NULL,
  position INTEGER NOT NULL CHECK (position >= 0),
  created_at TEXT NOT NULL,
  CHECK (team_a_id <> team_b_id),
  CHECK (anchor_a_engine_id <> anchor_b_engine_id),
  UNIQUE (event_id, position)
);

ALTER TABLE engine_relay_fixtures ADD COLUMN IF NOT EXISTS kibitzer_engine_id BIGINT REFERENCES engine_versions(id);
ALTER TABLE engine_relay_fixtures ADD COLUMN IF NOT EXISTS kibitzer_threads INTEGER CHECK (kibitzer_threads IS NULL OR kibitzer_threads > 0);
ALTER TABLE engine_relay_fixtures ADD COLUMN IF NOT EXISTS kibitzer_hash_mb INTEGER CHECK (kibitzer_hash_mb IS NULL OR kibitzer_hash_mb > 0);

CREATE TABLE IF NOT EXISTS engine_relay_fixture_teams (
  fixture_id BIGINT NOT NULL REFERENCES engine_relay_fixtures(id) ON DELETE CASCADE,
  team_id BIGINT NOT NULL REFERENCES event_cast_members(id),
  anchor_engine_id BIGINT NOT NULL REFERENCES engine_versions(id),
  position INTEGER NOT NULL CHECK (position >= 0),
  PRIMARY KEY (fixture_id, team_id),
  UNIQUE (fixture_id, anchor_engine_id),
  UNIQUE (fixture_id, position)
);

INSERT INTO engine_relay_fixture_teams (fixture_id, team_id, anchor_engine_id, position)
SELECT id, team_a_id, anchor_a_engine_id, 0 FROM engine_relay_fixtures
UNION ALL
SELECT id, team_b_id, anchor_b_engine_id, 1 FROM engine_relay_fixtures
ON CONFLICT (fixture_id, team_id) DO NOTHING;

ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS scheduled_start_at TEXT;
UPDATE tournaments
SET scheduled_start_at = to_char(
  CURRENT_TIMESTAMP AT TIME ZONE 'UTC',
  'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'
)
WHERE status = 'scheduled' AND scheduled_start_at IS NULL;
ALTER TABLE tournaments DROP CONSTRAINT IF EXISTS tournaments_scheduled_start_check;
ALTER TABLE tournaments ADD CONSTRAINT tournaments_scheduled_start_check
  CHECK (status != 'scheduled' OR scheduled_start_at IS NOT NULL);

CREATE TABLE IF NOT EXISTS participants (
  tournament_id BIGINT NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
  engine_id BIGINT NOT NULL REFERENCES engine_versions(id),
  seed INTEGER NOT NULL,
  PRIMARY KEY (tournament_id, engine_id),
  UNIQUE (tournament_id, seed)
);

CREATE TABLE IF NOT EXISTS tournament_matches (
  id BIGSERIAL PRIMARY KEY,
  tournament_id BIGINT NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
  round INTEGER NOT NULL,
  match_index INTEGER NOT NULL,
  engine1_id BIGINT NOT NULL REFERENCES engine_versions(id),
  engine2_id BIGINT REFERENCES engine_versions(id),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'finished', 'bye')),
  winner_engine_id BIGINT REFERENCES engine_versions(id),
  UNIQUE (tournament_id, round, match_index)
);

CREATE TABLE IF NOT EXISTS games (
  id BIGSERIAL PRIMARY KEY,
  tournament_id BIGINT NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
  round INTEGER NOT NULL,
  pair_index INTEGER NOT NULL,
  white_engine_id BIGINT NOT NULL REFERENCES engine_versions(id),
  black_engine_id BIGINT NOT NULL REFERENCES engine_versions(id),
  match_id BIGINT REFERENCES tournament_matches(id) ON DELETE SET NULL,
  game_number INTEGER NOT NULL DEFAULT 1,
  tiebreak_kind TEXT CHECK (tiebreak_kind IS NULL OR tiebreak_kind IN ('extra_pair', 'armageddon')),
  opening_id BIGINT,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'assigned', 'live', 'finished', 'abandoned')),
  result TEXT CHECK (result IS NULL OR result IN ('1-0', '0-1', '1/2-1/2')),
  termination TEXT,
  pgn TEXT,
  white_hw TEXT,
  black_hw TEXT,
  started_at TEXT,
  finished_at TEXT,
  UNIQUE (tournament_id, round, pair_index, white_engine_id, black_engine_id)
);

CREATE TABLE IF NOT EXISTS workers (
  id BIGSERIAL PRIMARY KEY,
  label TEXT NOT NULL,
  token_hash TEXT,
  token_expires_at TEXT,
  status TEXT NOT NULL DEFAULT 'minted'
    CHECK (status IN ('minted', 'connected', 'downloading', 'ready', 'busy', 'offline', 'revoked')),
  session_id TEXT,
  app_commit TEXT,
  protocol_version INTEGER,
  machine_id TEXT,
  hw TEXT,
  core_limit INTEGER CHECK (core_limit IS NULL OR core_limit > 0),
  tournament_scope TEXT NOT NULL DEFAULT 'all'
    CHECK (tournament_scope IN ('all', 'selected')),
  last_seen TEXT
);

ALTER TABLE workers ADD COLUMN IF NOT EXISTS core_limit INTEGER
  CHECK (core_limit IS NULL OR core_limit > 0);
ALTER TABLE workers ADD COLUMN IF NOT EXISTS tournament_scope TEXT NOT NULL DEFAULT 'all'
  CHECK (tournament_scope IN ('all', 'selected'));

CREATE TABLE IF NOT EXISTS worker_tournament_permissions (
  worker_id BIGINT NOT NULL REFERENCES workers(id) ON DELETE CASCADE,
  tournament_id BIGINT NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
  PRIMARY KEY (worker_id, tournament_id)
);

CREATE TABLE IF NOT EXISTS worker_event_permissions (
  worker_id BIGINT NOT NULL REFERENCES workers(id) ON DELETE CASCADE,
  event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  PRIMARY KEY (worker_id, event_id)
);

CREATE TABLE IF NOT EXISTS event_fixture_workers (
  tournament_id BIGINT PRIMARY KEY REFERENCES tournaments(id) ON DELETE CASCADE,
  event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  worker_id BIGINT NOT NULL REFERENCES workers(id) ON DELETE CASCADE,
  claimed_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_event_fixture_workers_active_worker
  ON event_fixture_workers(worker_id);

CREATE TABLE IF NOT EXISTS game_assignments (
  id BIGSERIAL PRIMARY KEY,
  game_id BIGINT NOT NULL UNIQUE REFERENCES games(id) ON DELETE CASCADE,
  assignment_key TEXT NOT NULL UNIQUE,
  worker_id BIGINT REFERENCES workers(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'assigned'
    CHECK (status IN ('assigned', 'acked', 'live', 'finished', 'abandoned', 'expired')),
  sent_at TEXT,
  acked_at TEXT,
  finished_at TEXT,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS game_assignment_progress (
  id BIGSERIAL PRIMARY KEY,
  assignment_id BIGINT NOT NULL REFERENCES game_assignments(id) ON DELETE CASCADE,
  assignment_key TEXT NOT NULL,
  game_id BIGINT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  source TEXT NOT NULL CHECK (source IN ('server', 'worker')),
  stage TEXT NOT NULL,
  stage_label TEXT NOT NULL,
  stage_order INTEGER NOT NULL CHECK (stage_order >= 0),
  substage TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed')),
  detail TEXT NOT NULL,
  engine_id BIGINT REFERENCES engine_versions(id) ON DELETE SET NULL,
  engine_name TEXT,
  current_value BIGINT CHECK (current_value IS NULL OR current_value >= 0),
  total_value BIGINT CHECK (total_value IS NULL OR total_value > 0),
  metadata TEXT NOT NULL DEFAULT '{}',
  occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_failures (
  id BIGSERIAL PRIMARY KEY,
  worker_id BIGINT REFERENCES workers(id) ON DELETE SET NULL,
  worker_label TEXT NOT NULL,
  machine_id TEXT,
  assignment_id BIGINT REFERENCES game_assignments(id) ON DELETE SET NULL,
  game_id BIGINT REFERENCES games(id) ON DELETE SET NULL,
  engine_id BIGINT REFERENCES engine_versions(id) ON DELETE SET NULL,
  engine_name TEXT NOT NULL,
  stage TEXT NOT NULL,
  error TEXT NOT NULL,
  occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS game_pause_checkpoints (
  game_id BIGINT PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
  state TEXT NOT NULL,
  paused_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_resource_samples (
  id BIGSERIAL PRIMARY KEY,
  worker_id BIGINT NOT NULL REFERENCES workers(id) ON DELETE CASCADE,
  sampled_at TEXT NOT NULL,
  cpu_percent REAL NOT NULL CHECK (cpu_percent >= 0 AND cpu_percent <= 100),
  memory_used_mb REAL NOT NULL CHECK (memory_used_mb >= 0),
  memory_total_mb REAL NOT NULL CHECK (memory_total_mb > 0),
  memory_available_mb REAL NOT NULL CHECK (memory_available_mb >= 0),
  coordinator_cpu_cores REAL NOT NULL CHECK (coordinator_cpu_cores >= 0),
  coordinator_memory_mb REAL NOT NULL CHECK (coordinator_memory_mb >= 0),
  engine_cpu_cores REAL NOT NULL CHECK (engine_cpu_cores >= 0),
  engine_memory_mb REAL NOT NULL CHECK (engine_memory_mb >= 0),
  disk_used_mb REAL NOT NULL CHECK (disk_used_mb >= 0),
  disk_free_mb REAL NOT NULL CHECK (disk_free_mb >= 0),
  disk_total_mb REAL NOT NULL CHECK (disk_total_mb > 0)
);

CREATE TABLE IF NOT EXISTS tool_jobs (
  id BIGSERIAL PRIMARY KEY,
  job_key TEXT NOT NULL UNIQUE,
  tool_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
  input TEXT NOT NULL DEFAULT '{}',
  worker_id BIGINT REFERENCES workers(id) ON DELETE SET NULL,
  total_items INTEGER NOT NULL CHECK (total_items > 0),
  completed_items INTEGER NOT NULL DEFAULT 0 CHECK (completed_items >= 0),
  attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS tool_job_items (
  id BIGSERIAL PRIMARY KEY,
  job_id BIGINT NOT NULL REFERENCES tool_jobs(id) ON DELETE CASCADE,
  engine_version_id BIGINT NOT NULL REFERENCES engine_versions(id),
  position INTEGER NOT NULL CHECK (position >= 0),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'running', 'supported', 'unsupported', 'failed')),
  result TEXT NOT NULL DEFAULT '{}',
  error TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  finished_at TEXT,
  UNIQUE (job_id, engine_version_id),
  UNIQUE (job_id, position)
);

ALTER TABLE worker_failures DROP CONSTRAINT IF EXISTS worker_failures_stage_check;

CREATE INDEX IF NOT EXISTS idx_worker_failures_worker_time
  ON worker_failures(worker_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_worker_failures_machine_time
  ON worker_failures(machine_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_worker_resource_samples_worker_time
  ON worker_resource_samples(worker_id, sampled_at DESC, id DESC);

DO $$
BEGIN
  UPDATE games
  SET status = 'pending'
  WHERE status IN ('assigned', 'live')
    AND id IN (
      SELECT assignment.game_id
      FROM game_assignments assignment
      JOIN (
        SELECT id
        FROM (
          SELECT id,
                 ROW_NUMBER() OVER (
                   PARTITION BY machine_id
                   ORDER BY CASE WHEN status = 'revoked' THEN 1 ELSE 0 END,
                            last_seen DESC NULLS LAST,
                            id
                 ) AS position
          FROM workers
          WHERE machine_id IS NOT NULL
        ) ranked
        WHERE ranked.position > 1
      ) duplicate_worker ON duplicate_worker.id = assignment.worker_id
      WHERE assignment.status IN ('assigned', 'acked', 'live')
    );
  UPDATE game_assignments assignment
  SET status = 'abandoned',
      finished_at = CURRENT_TIMESTAMP::text,
      last_error = 'duplicate machine worker retired',
      worker_id = NULL
  FROM (
    SELECT id
    FROM (
      SELECT id,
             ROW_NUMBER() OVER (
               PARTITION BY machine_id
               ORDER BY CASE WHEN status = 'revoked' THEN 1 ELSE 0 END,
                        last_seen DESC NULLS LAST,
                        id
             ) AS position
      FROM workers
      WHERE machine_id IS NOT NULL
    ) ranked
    WHERE ranked.position > 1
  ) duplicate_worker
  WHERE duplicate_worker.id = assignment.worker_id
    AND assignment.status IN ('assigned', 'acked', 'live');
  DELETE FROM workers worker
  USING (
    SELECT id
    FROM (
      SELECT id,
             ROW_NUMBER() OVER (
               PARTITION BY machine_id
               ORDER BY CASE WHEN status = 'revoked' THEN 1 ELSE 0 END,
                        last_seen DESC NULLS LAST,
                        id
             ) AS position
      FROM workers
      WHERE machine_id IS NOT NULL
    ) ranked
    WHERE ranked.position > 1
  ) duplicate_worker
  WHERE duplicate_worker.id = worker.id;
END
$$;

CREATE TABLE IF NOT EXISTS benchmark_hardware (
  hardware_key TEXT PRIMARY KEY CHECK (hardware_key ~ '^[0-9a-f]{64}$'),
  machine_id TEXT NOT NULL,
  hw TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE OR REPLACE FUNCTION reject_benchmark_hardware_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'benchmark hardware records are immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS benchmark_hardware_immutable ON benchmark_hardware;
CREATE TRIGGER benchmark_hardware_immutable
BEFORE UPDATE OR DELETE ON benchmark_hardware
FOR EACH ROW EXECUTE FUNCTION reject_benchmark_hardware_mutation();

CREATE TABLE IF NOT EXISTS benchmarkers (
  id BIGSERIAL PRIMARY KEY,
  label TEXT NOT NULL,
  token_hash TEXT,
  token_expires_at TEXT,
  status TEXT NOT NULL DEFAULT 'minted'
    CHECK (status IN ('minted', 'connected', 'busy', 'offline', 'revoked')),
  session_id TEXT,
  app_commit TEXT,
  protocol_version INTEGER,
  machine_id TEXT,
  hardware_key TEXT REFERENCES benchmark_hardware(hardware_key),
  hw TEXT,
  created_at TEXT NOT NULL,
  last_seen TEXT
);

CREATE TABLE IF NOT EXISTS benchmark_jobs (
  id BIGSERIAL PRIMARY KEY,
  job_key TEXT NOT NULL UNIQUE,
  benchmarker_id BIGINT REFERENCES benchmarkers(id) ON DELETE SET NULL,
  engine_version_id BIGINT REFERENCES engine_versions(id) ON DELETE SET NULL,
  engine_spec TEXT NOT NULL,
  engine_name TEXT NOT NULL,
  engine_version TEXT NOT NULL,
  build_hash TEXT NOT NULL CHECK (build_hash ~ '^[0-9a-f]{64}$'),
  hardware_key TEXT NOT NULL REFERENCES benchmark_hardware(hardware_key),
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
  attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
  scheduled_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  error TEXT NOT NULL DEFAULT '',
  UNIQUE (build_hash, hardware_key)
);

DROP INDEX IF EXISTS idx_benchmark_jobs_claim;
ALTER TABLE benchmark_jobs DROP COLUMN IF EXISTS next_retry_at;
ALTER TABLE benchmark_jobs ADD COLUMN IF NOT EXISTS output TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS engine_benchmarks (
  id BIGSERIAL PRIMARY KEY,
  job_id BIGINT NOT NULL UNIQUE REFERENCES benchmark_jobs(id),
  engine_version_id BIGINT REFERENCES engine_versions(id) ON DELETE SET NULL,
  engine_name TEXT NOT NULL,
  engine_version TEXT NOT NULL,
  build_hash TEXT NOT NULL CHECK (build_hash ~ '^[0-9a-f]{64}$'),
  artifact_sha256 TEXT CHECK (artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$'),
  hardware_key TEXT NOT NULL REFERENCES benchmark_hardware(hardware_key),
  nps BIGINT NOT NULL CHECK (nps > 0),
  elapsed_ms BIGINT NOT NULL CHECK (elapsed_ms >= 0),
  output TEXT NOT NULL DEFAULT '',
  recorded_at TEXT NOT NULL,
  UNIQUE (build_hash, hardware_key)
);

CREATE TABLE IF NOT EXISTS game_hardware_scores (
  game_id BIGINT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  assignment_id BIGINT NOT NULL REFERENCES game_assignments(id) ON DELETE CASCADE,
  worker_id BIGINT REFERENCES workers(id) ON DELETE SET NULL,
  engine_version_id BIGINT NOT NULL REFERENCES engine_versions(id),
  color TEXT NOT NULL CHECK (color IN ('white', 'black')),
  benchmark_hardware_key TEXT NOT NULL REFERENCES benchmark_hardware(hardware_key),
  benchmark_nps BIGINT NOT NULL CHECK (benchmark_nps > 0),
  worker_nps BIGINT NOT NULL CHECK (worker_nps > 0),
  hardware_score DOUBLE PRECISION NOT NULL CHECK (hardware_score > 0),
  elapsed_ms BIGINT NOT NULL CHECK (elapsed_ms >= 0),
  recorded_at TEXT NOT NULL,
  PRIMARY KEY (game_id, engine_version_id)
);

ALTER TABLE game_hardware_scores
  DROP CONSTRAINT IF EXISTS game_hardware_scores_game_id_color_key;

CREATE TABLE IF NOT EXISTS moves (
  game_id BIGINT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  ply INTEGER NOT NULL,
  uci TEXT NOT NULL,
  san TEXT NOT NULL,
  is_book INTEGER NOT NULL DEFAULT 0 CHECK (is_book IN (0, 1)),
  eval_cp INTEGER,
  eval_mate INTEGER,
  score_bound TEXT CHECK (score_bound IN ('lowerbound', 'upperbound')),
  depth INTEGER,
  seldepth INTEGER,
  nodes BIGINT,
  nps BIGINT,
  hashfull INTEGER,
  pv TEXT,
  info_line TEXT,
  time_ms INTEGER NOT NULL DEFAULT 0,
  clock_after_ms INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (game_id, ply)
);

ALTER TABLE engine_benchmarks ADD COLUMN IF NOT EXISTS artifact_sha256 TEXT;
ALTER TABLE engine_benchmarks DROP CONSTRAINT IF EXISTS engine_benchmarks_artifact_sha256_check;
ALTER TABLE engine_benchmarks ADD CONSTRAINT engine_benchmarks_artifact_sha256_check
  CHECK (artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$');

ALTER TABLE moves ADD COLUMN IF NOT EXISTS score_bound TEXT CHECK (score_bound IN ('lowerbound', 'upperbound'));
ALTER TABLE moves ADD COLUMN IF NOT EXISTS seldepth INTEGER;
ALTER TABLE moves ADD COLUMN IF NOT EXISTS hashfull INTEGER;
ALTER TABLE moves ADD COLUMN IF NOT EXISTS engine_version_id BIGINT REFERENCES engine_versions(id) ON DELETE SET NULL;
ALTER TABLE moves
  ALTER COLUMN nodes TYPE BIGINT,
  ALTER COLUMN nps TYPE BIGINT;

CREATE TABLE IF NOT EXISTS rating_lists (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  anchor_engine_id BIGINT REFERENCES engine_versions(id) ON DELETE SET NULL,
  anchor_elo REAL NOT NULL DEFAULT 1500,
  created_at TEXT NOT NULL
);

ALTER TABLE rating_lists ADD COLUMN IF NOT EXISTS anchor_engine_id BIGINT REFERENCES engine_versions(id) ON DELETE SET NULL;
ALTER TABLE rating_lists ADD COLUMN IF NOT EXISTS anchor_elo REAL NOT NULL DEFAULT 1500;

INSERT INTO rating_lists (name, created_at)
VALUES ('Default', '1970-01-01T00:00:00+00:00')
ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS rating_list_ratings (
  engine_id BIGINT NOT NULL REFERENCES engine_versions(id),
  rating_list_id BIGINT NOT NULL REFERENCES rating_lists(id) ON DELETE CASCADE,
  elo REAL NOT NULL DEFAULT 1500,
  games_played INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (engine_id, rating_list_id)
);

CREATE TABLE IF NOT EXISTS rating_list_history (
  id BIGSERIAL PRIMARY KEY,
  engine_id BIGINT NOT NULL REFERENCES engine_versions(id),
  rating_list_id BIGINT NOT NULL REFERENCES rating_lists(id) ON DELETE CASCADE,
  tournament_id BIGINT NOT NULL REFERENCES tournaments(id),
  opponent_engine_id BIGINT NOT NULL REFERENCES engine_versions(id),
  elo_before REAL NOT NULL,
  elo REAL NOT NULL,
  elo_change REAL NOT NULL,
  score REAL NOT NULL CHECK (score IN (0, 0.5, 1)),
  expected_score REAL NOT NULL CHECK (expected_score >= 0 AND expected_score <= 1),
  hardware_score REAL NOT NULL CHECK (hardware_score > 0),
  opponent_hardware_score REAL NOT NULL CHECK (opponent_hardware_score > 0),
  game_id BIGINT NOT NULL REFERENCES games(id),
  at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tournament_rating_list_commits (
  tournament_id BIGINT NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
  rating_list_id BIGINT NOT NULL REFERENCES rating_lists(id) ON DELETE CASCADE,
  command_id BIGINT,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'claimed', 'applied', 'failed')),
  requested_at TEXT NOT NULL,
  applied_at TEXT,
  error TEXT,
  PRIMARY KEY (tournament_id, rating_list_id)
);

CREATE TABLE IF NOT EXISTS service_endpoints (
  service TEXT PRIMARY KEY,
  host TEXT NOT NULL,
  port INTEGER NOT NULL CHECK (port > 0 AND port <= 65535),
  path TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS service_heartbeats (
  service TEXT PRIMARY KEY,
  app_commit TEXT NOT NULL,
  last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opening_suites (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS openings (
  id BIGSERIAL PRIMARY KEY,
  suite_id BIGINT NOT NULL REFERENCES opening_suites(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  start_fen TEXT NOT NULL,
  moves TEXT NOT NULL DEFAULT '[]',
  fen TEXT NOT NULL,
  UNIQUE (suite_id, position)
);

ALTER TABLE openings ADD COLUMN IF NOT EXISTS start_fen TEXT;
ALTER TABLE openings ADD COLUMN IF NOT EXISTS moves TEXT NOT NULL DEFAULT '[]';
UPDATE openings SET start_fen = fen WHERE start_fen IS NULL OR start_fen = '';
ALTER TABLE openings ALTER COLUMN start_fen SET NOT NULL;

CREATE TABLE IF NOT EXISTS chat_messages (
  id BIGSERIAL PRIMARY KEY,
  tournament_id BIGINT REFERENCES tournaments(id) ON DELETE CASCADE,
  event_id BIGINT REFERENCES events(id) ON DELETE CASCADE,
  display_name TEXT NOT NULL,
  text TEXT NOT NULL,
  at TEXT NOT NULL,
  CONSTRAINT chat_messages_subject_check
    CHECK ((tournament_id IS NOT NULL)::integer + (event_id IS NOT NULL)::integer = 1)
);

ALTER TABLE chat_messages ALTER COLUMN tournament_id DROP NOT NULL;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS event_id BIGINT REFERENCES events(id) ON DELETE CASCADE;
ALTER TABLE chat_messages DROP CONSTRAINT IF EXISTS chat_messages_subject_check;
ALTER TABLE chat_messages ADD CONSTRAINT chat_messages_subject_check
  CHECK ((tournament_id IS NOT NULL)::integer + (event_id IS NOT NULL)::integer = 1);

CREATE TABLE IF NOT EXISTS system_chat_events (
  tournament_id BIGINT NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
  event_key TEXT NOT NULL,
  event_type TEXT NOT NULL,
  message_id BIGINT NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
  metadata TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (tournament_id, event_key)
);

CREATE TABLE IF NOT EXISTS chat_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

INSERT INTO chat_settings (key, value) VALUES
  ('enabled', 'true'),
  ('slowmode_seconds', '0'),
  ('max_message_length', '300'),
  ('allow_anonymous_names', 'true'),
  ('retention_days', '30')
ON CONFLICT (key) DO NOTHING;

CREATE TABLE IF NOT EXISTS runner_commands (
  id BIGSERIAL PRIMARY KEY,
  command TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'claimed', 'applied', 'failed')),
  created_at TEXT NOT NULL,
  claimed_at TEXT,
  finished_at TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS deployment_jobs (
  id BIGSERIAL PRIMARY KEY,
  requested_ref TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'platform' CHECK (scope IN ('platform', 'web')),
  target_commit TEXT CHECK (target_commit IS NULL OR target_commit ~ '^[0-9a-f]{40}$'),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN (
      'pending', 'resolving', 'updating_workers', 'building', 'migrating',
      'restarting', 'verifying', 'succeeded', 'failed'
    )),
  requested_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS deployment_targets (
  id BIGSERIAL PRIMARY KEY,
  job_id BIGINT NOT NULL REFERENCES deployment_jobs(id) ON DELETE CASCADE,
  target_kind TEXT NOT NULL CHECK (target_kind IN ('server', 'worker', 'benchmarker')),
  target_id BIGINT,
  label TEXT NOT NULL,
  repository_url TEXT,
  target_commit TEXT CHECK (target_commit IS NULL OR target_commit ~ '^[0-9a-f]{40}$'),
  current_commit TEXT,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN (
      'pending', 'waiting', 'updating', 'restarting', 'succeeded',
      'deferred', 'failed'
    )),
  detail TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);

ALTER TABLE deployment_jobs ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'platform';
ALTER TABLE deployment_jobs DROP CONSTRAINT IF EXISTS deployment_jobs_scope_check;
ALTER TABLE deployment_jobs ADD CONSTRAINT deployment_jobs_scope_check
  CHECK (scope IN ('platform', 'web'));

CREATE TABLE IF NOT EXISTS dockerfile_pull_jobs (
  id BIGSERIAL PRIMARY KEY,
  requested_ref TEXT NOT NULL,
  target_commit TEXT CHECK (target_commit IS NULL OR target_commit ~ '^[0-9a-f]{40}$'),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'resolving', 'syncing', 'succeeded', 'failed')),
  files_updated INTEGER NOT NULL DEFAULT 0,
  requested_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  error TEXT
);

ALTER TABLE deployment_targets DROP CONSTRAINT IF EXISTS deployment_targets_target_kind_check;
ALTER TABLE deployment_targets DROP CONSTRAINT IF EXISTS deployment_targets_target_id_fkey;
ALTER TABLE deployment_targets ADD CONSTRAINT deployment_targets_target_kind_check
  CHECK (target_kind IN ('server', 'worker', 'benchmarker'));

CREATE INDEX IF NOT EXISTS idx_games_tournament_status ON games(tournament_id, status);
CREATE INDEX IF NOT EXISTS idx_games_round_pair ON games(tournament_id, round, pair_index);
CREATE INDEX IF NOT EXISTS idx_games_tournament_id_desc ON games(tournament_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_games_finished_white_export
  ON games(white_engine_id, tournament_id, id)
  WHERE status = 'finished' AND result IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_games_finished_black_export
  ON games(black_engine_id, tournament_id, id)
  WHERE status = 'finished' AND result IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tournament_matches_round ON tournament_matches(tournament_id, round, match_index);
CREATE INDEX IF NOT EXISTS idx_rating_list_history_engine_list_at
  ON rating_list_history(engine_id, rating_list_id, at);

CREATE INDEX IF NOT EXISTS idx_tournaments_scheduled_start
  ON tournaments(status, scheduled_start_at);

CREATE INDEX IF NOT EXISTS idx_events_public_status
  ON events(published_at, status, scheduled_start_at, id DESC);
CREATE INDEX IF NOT EXISTS idx_events_handler ON events(handler_key, id DESC);
CREATE INDEX IF NOT EXISTS idx_event_stages_event_position ON event_stages(event_id, position);
CREATE INDEX IF NOT EXISTS idx_event_sessions_event_position ON event_sessions(event_id, position);
CREATE INDEX IF NOT EXISTS idx_event_cast_event_position ON event_cast_members(event_id, position);
CREATE INDEX IF NOT EXISTS idx_event_contests_event_position ON event_contests(event_id, position);
CREATE INDEX IF NOT EXISTS idx_event_updates_public
  ON event_updates(event_id, published_at, occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_event_awards_event_position ON event_awards(event_id, position);
CREATE INDEX IF NOT EXISTS idx_engine_relay_fixtures_event_position ON engine_relay_fixtures(event_id, position);
CREATE INDEX IF NOT EXISTS idx_engine_relay_fixtures_team_a ON engine_relay_fixtures(team_a_id);
CREATE INDEX IF NOT EXISTS idx_engine_relay_fixtures_team_b ON engine_relay_fixtures(team_b_id);
CREATE INDEX IF NOT EXISTS idx_engine_relay_fixture_teams_team ON engine_relay_fixture_teams(team_id);
CREATE INDEX IF NOT EXISTS idx_engine_relay_fixture_teams_anchor ON engine_relay_fixture_teams(anchor_engine_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_event_id ON chat_messages(event_id, id DESC)
  WHERE event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_chat_messages_tournament_id ON chat_messages(tournament_id, id DESC)
  WHERE tournament_id IS NOT NULL;

INSERT INTO schema_metadata (key, value) VALUES ('schema_version', 41)
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
CREATE INDEX IF NOT EXISTS idx_runner_commands_status_created ON runner_commands(status, created_at);
CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(status);
CREATE INDEX IF NOT EXISTS idx_workers_machine_active ON workers(machine_id, status);
CREATE INDEX IF NOT EXISTS idx_worker_tournament_permissions_tournament
  ON worker_tournament_permissions(tournament_id, worker_id);
CREATE INDEX IF NOT EXISTS idx_worker_event_permissions_event
  ON worker_event_permissions(event_id, worker_id);
CREATE INDEX IF NOT EXISTS idx_event_fixture_workers_event
  ON event_fixture_workers(event_id, worker_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_workers_machine_id ON workers(machine_id)
  WHERE machine_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_game_assignments_worker_active ON game_assignments(worker_id, status);
CREATE INDEX IF NOT EXISTS idx_game_assignments_game_status ON game_assignments(game_id, status);
CREATE INDEX IF NOT EXISTS idx_game_assignment_progress_current
  ON game_assignment_progress(game_id, assignment_key, id DESC);
CREATE INDEX IF NOT EXISTS idx_game_assignment_progress_assignment_current
  ON game_assignment_progress(assignment_id, assignment_key, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_workers_token_hash ON workers(token_hash) WHERE token_hash IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_workers_session_id ON workers(session_id) WHERE session_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_benchmarkers_token_hash ON benchmarkers(token_hash) WHERE token_hash IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_benchmarkers_session_id ON benchmarkers(session_id) WHERE session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_benchmarkers_status ON benchmarkers(status);
CREATE INDEX IF NOT EXISTS idx_benchmark_jobs_claim ON benchmark_jobs(hardware_key, status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_engine_benchmarks_engine ON engine_benchmarks(engine_version_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_game_hardware_scores_engine ON game_hardware_scores(engine_version_id, game_id);
CREATE INDEX IF NOT EXISTS idx_deployment_jobs_status_id ON deployment_jobs(status, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_deployment_jobs_one_active
  ON deployment_jobs ((TRUE))
  WHERE status NOT IN ('succeeded', 'failed');
CREATE INDEX IF NOT EXISTS idx_dockerfile_pull_jobs_status_id ON dockerfile_pull_jobs(status, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dockerfile_pull_jobs_one_active
  ON dockerfile_pull_jobs ((TRUE))
  WHERE status NOT IN ('succeeded', 'failed');
CREATE INDEX IF NOT EXISTS idx_deployment_targets_job ON deployment_targets(job_id, target_kind, target_id);
CREATE INDEX IF NOT EXISTS idx_deployment_targets_worker_pending ON deployment_targets(target_id, status)
  WHERE target_kind = 'worker' AND target_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_deployment_targets_benchmarker_pending ON deployment_targets(target_id, status)
  WHERE target_kind = 'benchmarker' AND target_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tool_jobs_claim ON tool_jobs(status, created_at, id);
CREATE INDEX IF NOT EXISTS idx_tool_jobs_worker ON tool_jobs(worker_id, status);
CREATE INDEX IF NOT EXISTS idx_tool_job_items_job ON tool_job_items(job_id, position);
