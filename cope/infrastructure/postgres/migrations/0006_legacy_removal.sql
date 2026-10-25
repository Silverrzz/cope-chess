CREATE TABLE application_object_removals (
    object_id UUID PRIMARY KEY REFERENCES application_objects(id),
    source_namespace TEXT NOT NULL,
    source_alias TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('source_deleted','source_missing')),
    observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (source_namespace,source_alias),
    FOREIGN KEY (source_namespace,source_alias) REFERENCES application_object_aliases(namespace,alias)
);

CREATE FUNCTION application_record_removal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    resource_state BIGINT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM application_object_aliases WHERE namespace=NEW.source_namespace
                   AND alias=NEW.source_alias AND object_id=NEW.object_id) THEN
        RAISE EXCEPTION 'Removal must retain the original resource identity';
    END IF;
    UPDATE application_objects SET state_version=state_version+1
    WHERE id=NEW.object_id RETURNING state_version INTO resource_state;
    INSERT INTO application_outbox(object_id,kind,payload)
    VALUES (NEW.object_id,'object.removed',jsonb_build_object('state_version',resource_state,'observation_only',TRUE));
    RETURN NEW;
END;
$$;

CREATE TRIGGER application_removal_recorded AFTER INSERT ON application_object_removals
FOR EACH ROW EXECUTE FUNCTION application_record_removal();
CREATE TRIGGER application_removal_immutable BEFORE UPDATE OR DELETE ON application_object_removals
FOR EACH ROW EXECUTE FUNCTION application_immutable_fact();
CREATE TRIGGER application_alias_immutable BEFORE UPDATE OR DELETE ON application_object_aliases
FOR EACH ROW EXECUTE FUNCTION application_immutable_fact();

CREATE FUNCTION application_observed_removal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO application_object_removals(object_id,source_namespace,source_alias,reason)
    VALUES (OLD.object_id,TG_ARGV[0],OLD.id::text,'source_deleted');
    RETURN OLD;
END;
$$;

CREATE TRIGGER engines_object_removed AFTER DELETE ON engines
FOR EACH ROW EXECUTE FUNCTION application_observed_removal('legacy.engine');
CREATE TRIGGER tournaments_object_removed AFTER DELETE ON tournaments
FOR EACH ROW EXECUTE FUNCTION application_observed_removal('legacy.tournament');
CREATE TRIGGER workers_object_removed AFTER DELETE ON workers
FOR EACH ROW EXECUTE FUNCTION application_observed_removal('legacy.worker');
CREATE TRIGGER control_operations_object_removed AFTER DELETE ON control_operations
FOR EACH ROW EXECUTE FUNCTION application_observed_removal('legacy.control_operation');
CREATE TRIGGER tool_jobs_object_removed AFTER DELETE ON tool_jobs
FOR EACH ROW EXECUTE FUNCTION application_observed_removal('legacy.tool_job');
CREATE TRIGGER benchmark_jobs_object_removed AFTER DELETE ON benchmark_jobs
FOR EACH ROW EXECUTE FUNCTION application_observed_removal('legacy.benchmark_job');

DO $$
DECLARE
    source TEXT[];
BEGIN
    FOREACH source SLICE 1 IN ARRAY ARRAY[
        ARRAY['engines','legacy.engine'],ARRAY['tournaments','legacy.tournament'],ARRAY['workers','legacy.worker'],
        ARRAY['control_operations','legacy.control_operation'],ARRAY['tool_jobs','legacy.tool_job'],ARRAY['benchmark_jobs','legacy.benchmark_job']
    ] LOOP
        EXECUTE format('INSERT INTO application_object_removals(object_id,source_namespace,source_alias,reason)
                        SELECT object_id,namespace,alias,''source_missing'' FROM application_object_aliases identity
                        WHERE namespace=%L AND NOT EXISTS (SELECT 1 FROM %I source WHERE source.id::text=identity.alias)',source[2],source[1]);
    END LOOP;
END;
$$;
