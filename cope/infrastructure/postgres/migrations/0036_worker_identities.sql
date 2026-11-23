CREATE TABLE worker_identities (
    id BIGSERIAL PRIMARY KEY,
    label TEXT NOT NULL,
    donor_name TEXT NOT NULL,
    token_hash TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ
);

ALTER TABLE workers ADD COLUMN worker_identity_id BIGINT REFERENCES worker_identities(id);
CREATE INDEX idx_workers_identity ON workers(worker_identity_id) WHERE worker_identity_id IS NOT NULL;
ALTER TABLE game_assignments ADD COLUMN worker_donor_name TEXT;
ALTER TABLE hardware_donor_games ADD COLUMN donor_name TEXT;
