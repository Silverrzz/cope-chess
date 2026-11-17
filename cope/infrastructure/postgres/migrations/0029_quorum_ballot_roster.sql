ALTER TABLE cope_quorum.ballots ADD COLUMN roster JSONB;

UPDATE cope_quorum.ballots
SET roster = (SELECT value::jsonb FROM cope_quorum.settings WHERE key = 'quorum.genesis.v1');

ALTER TABLE cope_quorum.ballots ALTER COLUMN roster SET NOT NULL;
