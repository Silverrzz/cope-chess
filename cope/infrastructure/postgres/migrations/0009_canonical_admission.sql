INSERT INTO application_object_types(key,version) VALUES ('execution_runtime',1);

CREATE TABLE execution_runtimes (
    id UUID PRIMARY KEY,
    object_type TEXT NOT NULL DEFAULT 'execution_runtime' CHECK (object_type='execution_runtime'),
    workload TEXT NOT NULL CHECK (workload='artifacts.verify_legacy_engine.v1'),
    profile TEXT NOT NULL CHECK (profile='guarded-artifact-validation.v1'),
    image_digest TEXT NOT NULL UNIQUE CHECK (image_digest ~ '^sha256:[a-f0-9]{64}$'),
    source_revision TEXT NOT NULL CHECK (source_revision ~ '^[a-f0-9]{40}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (id,object_type) REFERENCES application_objects(id,type_key)
);
CREATE TRIGGER execution_runtime_immutable BEFORE UPDATE OR DELETE ON execution_runtimes
FOR EACH ROW EXECUTE FUNCTION form_immutable_record();

CREATE TABLE execution_admission_roots (
    key TEXT PRIMARY KEY,
    max_outstanding_jobs INTEGER NOT NULL CHECK (max_outstanding_jobs BETWEEN 1 AND 10000),
    max_actor_jobs INTEGER NOT NULL CHECK (max_actor_jobs BETWEEN 1 AND max_outstanding_jobs)
);
INSERT INTO execution_admission_roots(key,max_outstanding_jobs,max_actor_jobs) VALUES ('engine_preparation.v1',128,16);

CREATE TABLE execution_control_state (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    control_incarnation UUID,
    recovery_hold BOOLEAN NOT NULL DEFAULT TRUE,
    CHECK (recovery_hold AND control_incarnation IS NULL)
);
INSERT INTO execution_control_state(singleton) VALUES (TRUE);

CREATE TABLE execution_operations (
    id UUID PRIMARY KEY,
    object_type TEXT NOT NULL DEFAULT 'operation' CHECK (object_type='operation'),
    actor TEXT NOT NULL,
    command TEXT NOT NULL,
    admission_root TEXT NOT NULL REFERENCES execution_admission_roots(key),
    status TEXT NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting','cancelled')),
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    cancel_reason TEXT CHECK (length(cancel_reason) BETWEEN 1 AND 500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (id,object_type) REFERENCES application_objects(id,type_key),
    CHECK ((status='cancelled')=cancel_requested),
    CHECK (cancel_requested=(cancel_reason IS NOT NULL))
);
CREATE INDEX execution_operations_actor_order ON execution_operations(actor,created_at DESC,id DESC);

CREATE TABLE execution_jobs (
    id UUID PRIMARY KEY,
    object_type TEXT NOT NULL DEFAULT 'job' CHECK (object_type='job'),
    owner_operation UUID NOT NULL REFERENCES execution_operations(id),
    runtime_id UUID NOT NULL REFERENCES execution_runtimes(id),
    admission_root TEXT NOT NULL REFERENCES execution_admission_roots(key),
    workload TEXT NOT NULL CHECK (workload='artifacts.verify_legacy_engine.v1'),
    specification JSONB NOT NULL CHECK (jsonb_typeof(specification)='object'),
    specification_digest TEXT NOT NULL CHECK (specification_digest ~ '^[a-f0-9]{64}$'),
    mandate JSONB NOT NULL CHECK (jsonb_typeof(mandate)='object'),
    not_after TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'blocked',
    block_reason JSONB NOT NULL CHECK (jsonb_typeof(block_reason)='object'),
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    cancel_reason TEXT CHECK (length(cancel_reason) BETWEEN 1 AND 500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (id,owner_operation),
    FOREIGN KEY (id,object_type) REFERENCES application_objects(id,type_key),
    CONSTRAINT execution_jobs_admission_only CHECK (status IN ('blocked','cancelled')),
    CHECK ((status='cancelled')=cancel_requested),
    CHECK (cancel_requested=(cancel_reason IS NOT NULL))
);
CREATE INDEX execution_jobs_root_state ON execution_jobs(admission_root,status,created_at,id);

CREATE TABLE execution_operation_jobs (
    operation_id UUID NOT NULL REFERENCES execution_operations(id),
    job_id UUID NOT NULL REFERENCES execution_jobs(id),
    interest TEXT NOT NULL CHECK (interest IN ('owning','observing')),
    owner_operation UUID,
    PRIMARY KEY (operation_id,job_id),
    FOREIGN KEY (job_id,owner_operation) REFERENCES execution_jobs(id,owner_operation),
    CHECK ((interest='owning')=(owner_operation IS NOT NULL)),
    CHECK (owner_operation IS NULL OR owner_operation=operation_id)
);

CREATE FUNCTION execution_reject_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Canonical execution identities and admitted history are retained';
END;
$$;

CREATE FUNCTION execution_preserve_admission() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    mutable TEXT[] := ARRAY['status','cancel_requested','cancel_reason','updated_at'];
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Canonical execution identities and admitted specifications are retained';
    END IF;
    IF (to_jsonb(NEW)-mutable) IS DISTINCT FROM (to_jsonb(OLD)-mutable)
       OR NEW.updated_at IS DISTINCT FROM OLD.updated_at
       OR OLD.cancel_requested AND NOT NEW.cancel_requested
       OR OLD.status='cancelled' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'Canonical admission is immutable; only supported state transitions are permitted';
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;
CREATE TRIGGER execution_operation_admission_immutable BEFORE UPDATE OR DELETE ON execution_operations
FOR EACH ROW EXECUTE FUNCTION execution_preserve_admission();
CREATE TRIGGER execution_job_admission_immutable BEFORE UPDATE OR DELETE ON execution_jobs
FOR EACH ROW EXECUTE FUNCTION execution_preserve_admission();
CREATE TRIGGER execution_operations_no_truncate BEFORE TRUNCATE ON execution_operations
FOR EACH STATEMENT EXECUTE FUNCTION execution_reject_truncate();
CREATE TRIGGER execution_jobs_no_truncate BEFORE TRUNCATE ON execution_jobs
FOR EACH STATEMENT EXECUTE FUNCTION execution_reject_truncate();
CREATE TRIGGER execution_operation_jobs_immutable BEFORE UPDATE OR DELETE ON execution_operation_jobs
FOR EACH ROW EXECUTE FUNCTION form_immutable_record();
CREATE TRIGGER execution_operation_jobs_no_truncate BEFORE TRUNCATE ON execution_operation_jobs
FOR EACH STATEMENT EXECUTE FUNCTION execution_reject_truncate();
CREATE TRIGGER execution_runtimes_no_truncate BEFORE TRUNCATE ON execution_runtimes
FOR EACH STATEMENT EXECUTE FUNCTION execution_reject_truncate();

CREATE FUNCTION execution_observe_canonical_state() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    version BIGINT;
BEGIN
    IF TG_OP='INSERT' THEN
        INSERT INTO application_object_aliases(namespace,alias,object_id) VALUES ('canonical.' || NEW.object_type,NEW.id::text,NEW.id);
        INSERT INTO application_outbox(object_id,kind,payload) VALUES (NEW.id,'execution.' || NEW.object_type || '.admitted',jsonb_build_object('state_version',1));
    ELSIF NEW.status IS DISTINCT FROM OLD.status OR NEW.cancel_requested IS DISTINCT FROM OLD.cancel_requested THEN
        UPDATE application_objects SET state_version=state_version+1 WHERE id=NEW.id RETURNING state_version INTO version;
        INSERT INTO application_outbox(object_id,kind,payload) VALUES (NEW.id,'execution.' || NEW.object_type || '.changed',jsonb_build_object('state_version',version));
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER execution_operation_observed AFTER INSERT OR UPDATE ON execution_operations
FOR EACH ROW EXECUTE FUNCTION execution_observe_canonical_state();
CREATE TRIGGER execution_job_observed AFTER INSERT OR UPDATE ON execution_jobs
FOR EACH ROW EXECUTE FUNCTION execution_observe_canonical_state();
