CREATE SCHEMA IF NOT EXISTS cope_quorum;
CREATE TABLE cope_quorum.settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE cope_quorum.credentials (
    account_id TEXT PRIMARY KEY,
    commitment TEXT NOT NULL UNIQUE,
    enrolled_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE cope_quorum.ballots (
    id UUID PRIMARY KEY,
    manifest JSONB NOT NULL,
    opens_at TIMESTAMPTZ NOT NULL,
    closes_at TIMESTAMPTZ NOT NULL,
    CHECK (closes_at > opens_at)
);
CREATE TABLE cope_quorum.votes (
    ballot_id UUID NOT NULL REFERENCES cope_quorum.ballots(id),
    position INTEGER NOT NULL CHECK (position > 0),
    nullifier TEXT NOT NULL,
    proof JSONB NOT NULL,
    PRIMARY KEY (ballot_id, position),
    UNIQUE (ballot_id, nullifier)
);
CREATE TABLE cope_quorum.endorsements (
    ballot_id UUID NOT NULL REFERENCES cope_quorum.ballots(id),
    size INTEGER NOT NULL CHECK (size >= 0),
    final BOOLEAN NOT NULL,
    witness TEXT NOT NULL,
    digest TEXT NOT NULL,
    signature TEXT NOT NULL,
    PRIMARY KEY (ballot_id, size, final, witness)
);
