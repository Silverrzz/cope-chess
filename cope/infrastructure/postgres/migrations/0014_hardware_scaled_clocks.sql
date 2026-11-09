ALTER TABLE rating_lists ADD COLUMN hardware_scaled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE rating_lists ADD COLUMN monthly_refresh BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE rating_lists ADD CONSTRAINT rating_lists_scaled_standard CHECK (NOT hardware_scaled OR variant = 'standard');

UPDATE rating_lists SET hardware_scaled = TRUE, monthly_refresh = TRUE WHERE name = 'Monthly Cope Top 15';

CREATE TABLE tournament_clock_profiles (
    tournament_id BIGINT NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    hardware_key TEXT NOT NULL REFERENCES benchmark_hardware(hardware_key),
    version_id BIGINT NOT NULL REFERENCES engine_versions(id),
    build_hash TEXT NOT NULL CHECK (build_hash ~ '^[0-9a-f]{64}$'),
    artifact_sha256 TEXT NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    nps BIGINT NOT NULL CHECK (nps > 0),
    PRIMARY KEY (tournament_id, hardware_key, version_id)
);
CREATE TABLE game_clock_calibrations (
    game_id BIGINT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    assignment_key TEXT NOT NULL,
    reference_hardware_key TEXT NOT NULL REFERENCES benchmark_hardware(hardware_key),
    worker_hardware_key TEXT NOT NULL REFERENCES benchmark_hardware(hardware_key),
    white_reference_nps BIGINT NOT NULL CHECK (white_reference_nps > 0),
    black_reference_nps BIGINT NOT NULL CHECK (black_reference_nps > 0),
    white_worker_nps BIGINT NOT NULL CHECK (white_worker_nps > 0),
    black_worker_nps BIGINT NOT NULL CHECK (black_worker_nps > 0),
    white_initial_ms BIGINT NOT NULL CHECK (white_initial_ms > 0),
    black_initial_ms BIGINT NOT NULL CHECK (black_initial_ms > 0),
    white_increment_ms BIGINT NOT NULL CHECK (white_increment_ms >= 0),
    black_increment_ms BIGINT NOT NULL CHECK (black_increment_ms >= 0),
    PRIMARY KEY (game_id, assignment_key)
);
