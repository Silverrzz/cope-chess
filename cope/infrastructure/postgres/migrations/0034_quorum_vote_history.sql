CREATE TABLE cope_quorum.vote_checkpoints (
    ballot_id UUID NOT NULL REFERENCES cope_quorum.ballots(id),
    phase TEXT NOT NULL CHECK (phase IN ('open', 'closed')),
    size INTEGER NOT NULL CHECK (size BETWEEN 0 AND 1024),
    digest TEXT NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
    public_key TEXT NOT NULL CHECK (public_key ~ '^[a-f0-9]{64}$'),
    signature TEXT NOT NULL CHECK (signature ~ '^[a-f0-9]{128}$'),
    PRIMARY KEY (ballot_id, phase, size, public_key)
);

CREATE TABLE cope_quorum.witness_policy_anchor (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    head TEXT NOT NULL CHECK (head ~ '^[a-f0-9]{64}$')
);
