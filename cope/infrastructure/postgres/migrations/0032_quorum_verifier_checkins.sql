CREATE TABLE cope_quorum.verifier_checkins (
    public_key TEXT PRIMARY KEY,
    sent_at BIGINT NOT NULL,
    seen_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    last_scan BIGINT,
    archived INTEGER NOT NULL,
    scan_errors INTEGER NOT NULL,
    runtime TEXT NOT NULL
);
