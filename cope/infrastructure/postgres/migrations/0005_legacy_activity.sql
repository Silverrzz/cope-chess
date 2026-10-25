INSERT INTO application_object_types(key,version) VALUES ('operation',1),('job',1);

ALTER TABLE control_operations ADD COLUMN object_id UUID UNIQUE;
ALTER TABLE control_operations ADD COLUMN object_type TEXT NOT NULL DEFAULT 'operation' CHECK (object_type='operation');
ALTER TABLE control_operations ADD CONSTRAINT control_operations_application_object_fk
FOREIGN KEY (object_id,object_type) REFERENCES application_objects(id,type_key) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE tool_jobs ADD COLUMN object_id UUID UNIQUE;
ALTER TABLE tool_jobs ADD COLUMN object_type TEXT NOT NULL DEFAULT 'job' CHECK (object_type='job');
ALTER TABLE tool_jobs ADD CONSTRAINT tool_jobs_application_object_fk
FOREIGN KEY (object_id,object_type) REFERENCES application_objects(id,type_key) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE benchmark_jobs ADD COLUMN object_id UUID UNIQUE;
ALTER TABLE benchmark_jobs ADD COLUMN object_type TEXT NOT NULL DEFAULT 'job' CHECK (object_type='job');
ALTER TABLE benchmark_jobs ADD CONSTRAINT benchmark_jobs_application_object_fk
FOREIGN KEY (object_id,object_type) REFERENCES application_objects(id,type_key) DEFERRABLE INITIALLY DEFERRED;

CREATE TRIGGER control_operations_object_identity BEFORE INSERT OR UPDATE ON control_operations
FOR EACH ROW EXECUTE FUNCTION application_bind_object('operation','legacy.control_operation');
CREATE TRIGGER control_operations_register_object AFTER INSERT OR UPDATE ON control_operations
FOR EACH ROW EXECUTE FUNCTION application_register_object('operation','legacy.control_operation');

CREATE TRIGGER tool_jobs_object_identity BEFORE INSERT OR UPDATE ON tool_jobs
FOR EACH ROW EXECUTE FUNCTION application_bind_object('job','legacy.tool_job');
CREATE TRIGGER tool_jobs_register_object AFTER INSERT OR UPDATE ON tool_jobs
FOR EACH ROW EXECUTE FUNCTION application_register_object('job','legacy.tool_job');

CREATE TRIGGER benchmark_jobs_object_identity BEFORE INSERT OR UPDATE ON benchmark_jobs
FOR EACH ROW EXECUTE FUNCTION application_bind_object('job','legacy.benchmark_job');
CREATE TRIGGER benchmark_jobs_register_object AFTER INSERT OR UPDATE ON benchmark_jobs
FOR EACH ROW EXECUTE FUNCTION application_register_object('job','legacy.benchmark_job');

CREATE FUNCTION application_observed_activity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    previous JSONB;
    current JSONB;
    field_name TEXT;
    content_changed BOOLEAN := FALSE;
    state_changed BOOLEAN := FALSE;
    content_version BIGINT;
    state_version BIGINT;
BEGIN
    IF TG_OP='INSERT' OR OLD.object_id IS NULL THEN
        INSERT INTO application_outbox(object_id,kind,payload)
        VALUES (NEW.object_id,TG_ARGV[0] || '.created',jsonb_build_object('observation_only',TRUE));
        RETURN NEW;
    END IF;
    previous := to_jsonb(OLD);
    current := to_jsonb(NEW);
    FOREACH field_name IN ARRAY string_to_array(TG_ARGV[1],',') LOOP
        content_changed := content_changed OR (previous->field_name IS DISTINCT FROM current->field_name);
    END LOOP;
    FOREACH field_name IN ARRAY string_to_array(TG_ARGV[2],',') LOOP
        state_changed := state_changed OR (previous->field_name IS DISTINCT FROM current->field_name);
    END LOOP;
    IF content_changed OR state_changed THEN
        UPDATE application_objects SET content_revision=content_revision+content_changed::int,
               state_version=application_objects.state_version+state_changed::int
        WHERE id=NEW.object_id RETURNING content_revision,application_objects.state_version INTO content_version,state_version;
        INSERT INTO application_outbox(object_id,kind,payload)
        VALUES (NEW.object_id,TG_ARGV[0] || '.changed',jsonb_build_object('observation_only',TRUE,
                'content_revision',content_version,'state_version',state_version));
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER zz_control_activity AFTER INSERT OR UPDATE ON control_operations
FOR EACH ROW EXECUTE FUNCTION application_observed_activity('legacy.control_operation','title','status,cancel_requested');
CREATE TRIGGER zz_tool_activity AFTER INSERT OR UPDATE ON tool_jobs
FOR EACH ROW EXECUTE FUNCTION application_observed_activity('legacy.tool_job','tool_name','status');
CREATE TRIGGER zz_benchmark_activity AFTER INSERT OR UPDATE ON benchmark_jobs
FOR EACH ROW EXECUTE FUNCTION application_observed_activity('legacy.benchmark_job','engine_name,engine_version','status');

CREATE INDEX application_objects_activity_order ON application_objects(type_key,created_at DESC,id DESC);
CREATE INDEX application_outbox_object_delivery ON application_outbox(object_id,delivered_position);

UPDATE control_operations SET object_id=NULL;
UPDATE tool_jobs SET object_id=NULL;
UPDATE benchmark_jobs SET object_id=NULL;

SET CONSTRAINTS control_operations_application_object_fk,tool_jobs_application_object_fk,benchmark_jobs_application_object_fk IMMEDIATE;
ALTER TABLE control_operations ALTER COLUMN object_id SET NOT NULL;
ALTER TABLE tool_jobs ALTER COLUMN object_id SET NOT NULL;
ALTER TABLE benchmark_jobs ALTER COLUMN object_id SET NOT NULL;
SET CONSTRAINTS control_operations_application_object_fk,tool_jobs_application_object_fk,benchmark_jobs_application_object_fk DEFERRED;

