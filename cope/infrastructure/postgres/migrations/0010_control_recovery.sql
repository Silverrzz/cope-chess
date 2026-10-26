INSERT INTO application_object_types(key,version) VALUES ('execution_incarnation',1);

CREATE TABLE execution_incarnations (
    id UUID PRIMARY KEY,
    object_type TEXT NOT NULL DEFAULT 'execution_incarnation' CHECK (object_type='execution_incarnation'),
    installation_id UUID NOT NULL,
    established_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (id,object_type) REFERENCES application_objects(id,type_key)
);

CREATE TABLE execution_bootstrap_records (
    id UUID PRIMARY KEY,
    installation_id UUID NOT NULL,
    sequence BIGINT NOT NULL CHECK (sequence>0),
    digest TEXT NOT NULL UNIQUE CHECK (digest ~ '^[a-f0-9]{64}$'),
    incarnation UUID NOT NULL REFERENCES execution_incarnations(id),
    action TEXT NOT NULL CHECK (action IN ('initialize','restore')),
    body JSONB NOT NULL CHECK (jsonb_typeof(body)='object'),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (installation_id,sequence)
);

ALTER TABLE execution_control_state DROP CONSTRAINT execution_control_state_check;
ALTER TABLE execution_control_state ADD COLUMN installation_id UUID;
ALTER TABLE execution_control_state ADD COLUMN journal_record UUID REFERENCES execution_bootstrap_records(id);
ALTER TABLE execution_control_state ADD COLUMN journal_digest TEXT CHECK (journal_digest ~ '^[a-f0-9]{64}$');
ALTER TABLE execution_control_state ADD COLUMN generation BIGINT NOT NULL DEFAULT 1 CHECK (generation>0);
ALTER TABLE execution_control_state ADD COLUMN credential_reset_pending BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE execution_control_state ADD COLUMN established_at TIMESTAMPTZ;
ALTER TABLE execution_control_state ADD CONSTRAINT execution_control_incarnation_fk FOREIGN KEY (control_incarnation) REFERENCES execution_incarnations(id);
ALTER TABLE execution_control_state ADD CONSTRAINT execution_control_recovery_hold CHECK (recovery_hold);
ALTER TABLE execution_control_state ADD CONSTRAINT execution_control_binding_complete CHECK (
    (control_incarnation IS NULL AND installation_id IS NULL AND journal_record IS NULL AND journal_digest IS NULL AND established_at IS NULL AND NOT credential_reset_pending)
    OR (control_incarnation IS NOT NULL AND installation_id IS NOT NULL AND journal_record IS NOT NULL AND journal_digest IS NOT NULL AND established_at IS NOT NULL)
);

ALTER TABLE execution_jobs ADD COLUMN admitted_incarnation UUID REFERENCES execution_incarnations(id);

CREATE TABLE execution_recovery_subjects (
    incarnation UUID NOT NULL REFERENCES execution_incarnations(id),
    job_id UUID NOT NULL REFERENCES execution_jobs(id),
    classification TEXT NOT NULL DEFAULT 'held' CHECK (classification='held'),
    observed_status TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (incarnation,job_id)
);

ALTER TABLE application_bootstrap_roles ADD COLUMN sessions_valid_after BIGINT NOT NULL DEFAULT 0 CHECK (sessions_valid_after>=0);
ALTER TABLE application_bootstrap_roles ADD COLUMN recovery_locked BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE execution_credential_recoveries (
    incarnation UUID PRIMARY KEY REFERENCES execution_incarnations(id),
    token_digest TEXT NOT NULL CHECK (token_digest ~ '^[a-f0-9]{64}$'),
    recovered_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER execution_credential_recovery_immutable BEFORE UPDATE OR DELETE ON execution_credential_recoveries
FOR EACH ROW EXECUTE FUNCTION form_immutable_record();
CREATE TRIGGER execution_credential_recovery_no_truncate BEFORE TRUNCATE ON execution_credential_recoveries
FOR EACH STATEMENT EXECUTE FUNCTION execution_reject_truncate();

CREATE OR REPLACE FUNCTION application_changed_bootstrap_secret() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='UPDATE' AND NEW.role=OLD.role AND NEW.token IS NOT DISTINCT FROM OLD.token THEN
        RETURN NEW;
    END IF;
    IF TG_OP<>'INSERT' THEN
        UPDATE application_bootstrap_roles SET generation=generation+1,token_digest=NULL,
            recovery_locked=CASE WHEN TG_OP='UPDATE' AND NEW.role=OLD.role AND COALESCE(NEW.token,'')<>'' THEN FALSE ELSE recovery_locked END
        WHERE role=OLD.role;
    END IF;
    IF TG_OP='INSERT' OR TG_OP='UPDATE' AND NEW.role IS DISTINCT FROM OLD.role THEN
        UPDATE application_bootstrap_roles SET generation=generation+1,token_digest=NULL,recovery_locked=COALESCE(NEW.token,'')=''
        WHERE role=NEW.role;
    END IF;
    RETURN NULL;
END;
$$;

CREATE FUNCTION execution_preserve_session_cutoff() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.sessions_valid_after<OLD.sessions_valid_after THEN
        RAISE EXCEPTION 'Browser session recovery cutoff cannot move backwards';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER zz_bootstrap_session_cutoff BEFORE UPDATE ON application_bootstrap_roles
FOR EACH ROW EXECUTE FUNCTION execution_preserve_session_cutoff();

CREATE TRIGGER execution_incarnation_immutable BEFORE UPDATE OR DELETE ON execution_incarnations
FOR EACH ROW EXECUTE FUNCTION form_immutable_record();
CREATE TRIGGER execution_bootstrap_record_immutable BEFORE UPDATE OR DELETE ON execution_bootstrap_records
FOR EACH ROW EXECUTE FUNCTION form_immutable_record();
CREATE TRIGGER execution_recovery_subject_immutable BEFORE UPDATE OR DELETE ON execution_recovery_subjects
FOR EACH ROW EXECUTE FUNCTION form_immutable_record();
CREATE TRIGGER execution_incarnations_no_truncate BEFORE TRUNCATE ON execution_incarnations
FOR EACH STATEMENT EXECUTE FUNCTION execution_reject_truncate();
CREATE TRIGGER execution_bootstrap_records_no_truncate BEFORE TRUNCATE ON execution_bootstrap_records
FOR EACH STATEMENT EXECUTE FUNCTION execution_reject_truncate();
CREATE TRIGGER execution_recovery_subjects_no_truncate BEFORE TRUNCATE ON execution_recovery_subjects
FOR EACH STATEMENT EXECUTE FUNCTION execution_reject_truncate();

CREATE FUNCTION execution_preserve_control_state() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Control authority requires a retained bootstrap transition';
    END IF;
    IF NEW.singleton IS DISTINCT FROM OLD.singleton OR NEW.generation<>OLD.generation+1 THEN
        RAISE EXCEPTION 'Control authority changes require the next local state version';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER execution_control_state_version BEFORE UPDATE OR DELETE ON execution_control_state
FOR EACH ROW EXECUTE FUNCTION execution_preserve_control_state();
CREATE TRIGGER execution_control_state_no_truncate BEFORE TRUNCATE ON execution_control_state
FOR EACH STATEMENT EXECUTE FUNCTION execution_reject_truncate();
