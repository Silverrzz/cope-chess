ALTER TABLE cope_quorum.vote_checkpoints
    DROP CONSTRAINT vote_checkpoints_phase_check,
    ADD CONSTRAINT vote_checkpoints_phase_check CHECK (phase IN ('open', 'frozen', 'closed'));
