CREATE TABLE cope_quorum.witness_signatures (
    ballot_id UUID NOT NULL REFERENCES cope_quorum.ballots(id),
    public_key TEXT NOT NULL,
    digest TEXT NOT NULL,
    signature TEXT NOT NULL,
    signed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (ballot_id, public_key)
);

CREATE TABLE cope_quorum.vote_disclosures (
    ballot_id UUID NOT NULL,
    nullifier TEXT NOT NULL,
    account_id TEXT NOT NULL,
    username TEXT NOT NULL,
    published_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (ballot_id, nullifier),
    UNIQUE (ballot_id, account_id),
    FOREIGN KEY (ballot_id, nullifier) REFERENCES cope_quorum.votes(ballot_id, nullifier)
);
