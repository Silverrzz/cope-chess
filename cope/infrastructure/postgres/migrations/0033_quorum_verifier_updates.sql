ALTER TABLE cope_quorum.verifier_checkins
    ADD COLUMN id BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    ADD COLUMN app_commit TEXT CHECK (app_commit IS NULL OR app_commit ~ '^[0-9a-f]{40}$');

ALTER TABLE deployment_targets DROP CONSTRAINT deployment_targets_target_kind_check;
ALTER TABLE deployment_targets ADD CONSTRAINT deployment_targets_target_kind_check
    CHECK (target_kind IN ('server', 'worker', 'benchmarker', 'verifier'));

CREATE UNIQUE INDEX deployment_targets_verifier_job
    ON deployment_targets (job_id, target_id) WHERE target_kind = 'verifier';
