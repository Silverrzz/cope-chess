ALTER TABLE cope_quorum.votes
    ADD COLUMN revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0 AND revision <= 9007199254740991);
