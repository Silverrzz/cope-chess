CREATE TABLE execution_ownership (
    object_id UUID PRIMARY KEY REFERENCES application_objects(id),
    backend TEXT NOT NULL DEFAULT 'legacy' CHECK (backend IN ('legacy','canonical')),
    held BOOLEAN NOT NULL DEFAULT FALSE,
    generation BIGINT NOT NULL DEFAULT 1 CHECK (generation>0),
    changed_by TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 1000),
    changed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT execution_ownership_legacy_only CHECK (backend='legacy')
);

CREATE TABLE execution_ownership_history (
    object_id UUID NOT NULL REFERENCES execution_ownership(object_id),
    generation BIGINT NOT NULL CHECK (generation>0),
    backend TEXT NOT NULL,
    held BOOLEAN NOT NULL,
    changed_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (object_id,generation)
);

CREATE TABLE execution_legacy_constituents (
    namespace TEXT NOT NULL,
    alias TEXT NOT NULL,
    object_id UUID NOT NULL REFERENCES execution_ownership(object_id),
    PRIMARY KEY (namespace,alias)
);

INSERT INTO execution_ownership(object_id,changed_by,reason)
SELECT object_id,'migration:0007','Observed legacy ownership; no execution transferred'
FROM application_object_aliases WHERE namespace IN ('legacy.control_operation','legacy.tool_job','legacy.benchmark_job');
INSERT INTO execution_ownership_history SELECT object_id,generation,backend,held,changed_by,reason,changed_at FROM execution_ownership;
INSERT INTO execution_legacy_constituents(namespace,alias,object_id)
SELECT 'legacy.puzzle_suite_run',run.id::text,job.object_id FROM puzzle_suite_runs run JOIN tool_jobs job ON job.id=run.job_id;

CREATE FUNCTION execution_check_ownership_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Execution ownership history is retained';
    ELSIF TG_OP='INSERT' THEN
        IF NEW.generation<>1 OR NEW.backend<>'legacy' OR NEW.held THEN
            RAISE EXCEPTION 'Initial legacy ownership must be observed without a hold';
        END IF;
    ELSE
        IF NEW.object_id IS DISTINCT FROM OLD.object_id OR NEW.generation<>OLD.generation+1
           OR (NEW.backend,NEW.held) IS NOT DISTINCT FROM (OLD.backend,OLD.held) THEN
            RAISE EXCEPTION 'Ownership transitions require a new generation and a changed routing decision';
        END IF;
        NEW.changed_at := clock_timestamp();
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION execution_record_ownership() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO execution_ownership_history(object_id,generation,backend,held,changed_by,reason,changed_at)
    VALUES (NEW.object_id,NEW.generation,NEW.backend,NEW.held,NEW.changed_by,NEW.reason,NEW.changed_at);
    INSERT INTO application_outbox(object_id,kind,payload)
    VALUES (NEW.object_id,'execution.ownership_changed',jsonb_build_object('generation',NEW.generation,'held',NEW.held,'backend',NEW.backend));
    RETURN NEW;
END;
$$;

CREATE TRIGGER execution_ownership_guard BEFORE INSERT OR UPDATE OR DELETE ON execution_ownership
FOR EACH ROW EXECUTE FUNCTION execution_check_ownership_change();
CREATE TRIGGER execution_ownership_record AFTER INSERT OR UPDATE ON execution_ownership
FOR EACH ROW EXECUTE FUNCTION execution_record_ownership();
CREATE TRIGGER execution_ownership_history_immutable BEFORE UPDATE OR DELETE ON execution_ownership_history
FOR EACH ROW EXECUTE FUNCTION application_immutable_fact();
CREATE TRIGGER execution_constituent_immutable BEFORE UPDATE OR DELETE ON execution_legacy_constituents
FOR EACH ROW EXECUTE FUNCTION application_immutable_fact();

CREATE FUNCTION execution_observe_legacy_owner() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO execution_ownership(object_id,changed_by,reason)
    VALUES (NEW.object_id,'legacy:source','New work remains owned by its legacy executor');
    RETURN NEW;
END;
$$;

CREATE TRIGGER zy_control_ownership AFTER INSERT ON control_operations
FOR EACH ROW EXECUTE FUNCTION execution_observe_legacy_owner();
CREATE TRIGGER zy_tool_ownership AFTER INSERT ON tool_jobs
FOR EACH ROW EXECUTE FUNCTION execution_observe_legacy_owner();
CREATE TRIGGER zy_benchmark_ownership AFTER INSERT ON benchmark_jobs
FOR EACH ROW EXECUTE FUNCTION execution_observe_legacy_owner();

CREATE FUNCTION execution_require_legacy(namespace_key TEXT,source_alias TEXT) RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
    owner execution_ownership%ROWTYPE;
BEGIN
    IF source_alias IS NULL THEN RETURN; END IF;
    SELECT ownership.* INTO owner FROM execution_ownership ownership
    WHERE ownership.object_id=(SELECT identity.object_id FROM application_object_aliases identity
                              WHERE identity.namespace=namespace_key AND identity.alias=source_alias
                              UNION ALL SELECT member.object_id FROM execution_legacy_constituents member
                              WHERE member.namespace=namespace_key AND member.alias=source_alias)
    FOR SHARE OF ownership NOWAIT;
    IF NOT FOUND OR owner.backend<>'legacy' OR owner.held THEN
        RAISE EXCEPTION USING ERRCODE='P7501',MESSAGE='Legacy execution routing is held or no longer owns this work';
    END IF;
EXCEPTION WHEN lock_not_available THEN
    RAISE EXCEPTION USING ERRCODE='P7501',MESSAGE='Execution ownership is changing; retry after refreshing its routing';
END;
$$;

CREATE FUNCTION execution_guard_legacy_write() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP<>'INSERT' THEN PERFORM execution_require_legacy(TG_ARGV[0],to_jsonb(OLD)->>TG_ARGV[1]); END IF;
    IF TG_OP<>'DELETE' THEN PERFORM execution_require_legacy(TG_ARGV[0],to_jsonb(NEW)->>TG_ARGV[1]); RETURN NEW; END IF;
    RETURN OLD;
END;
$$;

CREATE TRIGGER a_execution_owner BEFORE UPDATE OR DELETE ON control_operations
FOR EACH ROW EXECUTE FUNCTION execution_guard_legacy_write('legacy.control_operation','id');
CREATE TRIGGER a_execution_owner BEFORE UPDATE OR DELETE ON tool_jobs
FOR EACH ROW EXECUTE FUNCTION execution_guard_legacy_write('legacy.tool_job','id');
CREATE TRIGGER a_execution_owner BEFORE UPDATE OR DELETE ON benchmark_jobs
FOR EACH ROW EXECUTE FUNCTION execution_guard_legacy_write('legacy.benchmark_job','id');
CREATE TRIGGER a_execution_owner BEFORE INSERT OR UPDATE OR DELETE ON control_steps
FOR EACH ROW EXECUTE FUNCTION execution_guard_legacy_write('legacy.control_operation','operation_id');
CREATE TRIGGER a_execution_owner BEFORE INSERT OR UPDATE OR DELETE ON control_events
FOR EACH ROW EXECUTE FUNCTION execution_guard_legacy_write('legacy.control_operation','operation_id');
CREATE TRIGGER a_execution_owner BEFORE INSERT OR UPDATE OR DELETE ON tool_job_items
FOR EACH ROW EXECUTE FUNCTION execution_guard_legacy_write('legacy.tool_job','job_id');
CREATE TRIGGER a_execution_owner BEFORE INSERT OR UPDATE OR DELETE ON puzzle_suite_runs
FOR EACH ROW EXECUTE FUNCTION execution_guard_legacy_write('legacy.tool_job','job_id');
CREATE TRIGGER a_execution_owner BEFORE INSERT OR UPDATE OR DELETE ON puzzle_suite_engine_results
FOR EACH ROW EXECUTE FUNCTION execution_guard_legacy_write('legacy.puzzle_suite_run','run_id');
CREATE TRIGGER a_execution_owner BEFORE INSERT OR UPDATE OR DELETE ON engine_benchmarks
FOR EACH ROW EXECUTE FUNCTION execution_guard_legacy_write('legacy.benchmark_job','job_id');
CREATE TRIGGER a_execution_owner BEFORE INSERT OR UPDATE OR DELETE ON engine_acknowledged_queue
FOR EACH ROW EXECUTE FUNCTION execution_guard_legacy_write('legacy.control_operation','intake_operation_id');
CREATE TRIGGER a_execution_owner BEFORE INSERT OR UPDATE OR DELETE ON engine_waiting_test_queue
FOR EACH ROW EXECUTE FUNCTION execution_guard_legacy_write('legacy.control_operation','operation_id');

CREATE FUNCTION execution_observe_puzzle_run() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO execution_legacy_constituents(namespace,alias,object_id)
    SELECT 'legacy.puzzle_suite_run',NEW.id::text,object_id FROM tool_jobs WHERE id=NEW.job_id;
    RETURN NEW;
END;
$$;

CREATE FUNCTION execution_puzzle_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.job_id IS DISTINCT FROM OLD.job_id THEN
        RAISE EXCEPTION USING ERRCODE='P7501',MESSAGE='A puzzle run cannot change its execution ownership identity';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER zy_execution_run AFTER INSERT ON puzzle_suite_runs
FOR EACH ROW EXECUTE FUNCTION execution_observe_puzzle_run();
CREATE TRIGGER execution_run_identity BEFORE UPDATE ON puzzle_suite_runs
FOR EACH ROW EXECUTE FUNCTION execution_puzzle_identity();
