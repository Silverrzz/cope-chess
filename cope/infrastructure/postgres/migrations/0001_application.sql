CREATE TABLE application_object_types (
    key TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version > 0)
);

INSERT INTO application_object_types (key, version)
VALUES ('engine', 1), ('competition', 1), ('agent', 1);

CREATE TABLE application_objects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type_key TEXT NOT NULL REFERENCES application_object_types(key),
    content_revision BIGINT NOT NULL DEFAULT 1 CHECK (content_revision > 0),
    state_version BIGINT NOT NULL DEFAULT 1 CHECK (state_version > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (id, type_key)
);

CREATE TABLE application_object_aliases (
    namespace TEXT NOT NULL,
    alias TEXT NOT NULL,
    object_id UUID NOT NULL REFERENCES application_objects(id),
    PRIMARY KEY (namespace, alias)
);

CREATE INDEX application_object_aliases_object ON application_object_aliases(object_id);

CREATE TABLE application_object_relations (
    source_id UUID NOT NULL REFERENCES application_objects(id),
    relation TEXT NOT NULL,
    target_id UUID NOT NULL REFERENCES application_objects(id),
    PRIMARY KEY (source_id, relation, target_id)
);

CREATE INDEX application_object_relations_target ON application_object_relations(target_id, relation);

CREATE TABLE application_command_receipts (
    id UUID PRIMARY KEY,
    actor TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    command TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    object_id UUID NOT NULL REFERENCES application_objects(id),
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (actor, idempotency_key)
);

CREATE TABLE application_outbox (
    id BIGSERIAL PRIMARY KEY,
    object_id UUID NOT NULL REFERENCES application_objects(id),
    receipt_id UUID REFERENCES application_command_receipts(id),
    kind TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

ALTER TABLE engines ADD COLUMN object_id UUID UNIQUE;
ALTER TABLE engines ADD COLUMN object_type TEXT NOT NULL DEFAULT 'engine' CHECK (object_type = 'engine');
ALTER TABLE engines ADD CONSTRAINT engines_application_object_fk FOREIGN KEY (object_id, object_type) REFERENCES application_objects(id, type_key) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE tournaments ADD COLUMN object_id UUID UNIQUE;
ALTER TABLE tournaments ADD COLUMN object_type TEXT NOT NULL DEFAULT 'competition' CHECK (object_type = 'competition');
ALTER TABLE tournaments ADD CONSTRAINT tournaments_application_object_fk FOREIGN KEY (object_id, object_type) REFERENCES application_objects(id, type_key) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE workers ADD COLUMN object_id UUID UNIQUE;
ALTER TABLE workers ADD COLUMN object_type TEXT NOT NULL DEFAULT 'agent' CHECK (object_type = 'agent');
ALTER TABLE workers ADD CONSTRAINT workers_application_object_fk FOREIGN KEY (object_id, object_type) REFERENCES application_objects(id, type_key) DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION application_bind_object() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.object_id IS NOT NULL THEN
        IF NEW.object_id IS DISTINCT FROM OLD.object_id THEN
            RAISE EXCEPTION 'Object identity is immutable';
        END IF;
    ELSE
        IF NEW.object_id IS NOT NULL THEN
            RAISE EXCEPTION 'Object identity is assigned by the owning resource';
        END IF;
        NEW.object_id := gen_random_uuid();
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION application_register_object() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.object_id IS NOT NULL THEN
        RETURN NEW;
    END IF;
    INSERT INTO application_objects(id, type_key) VALUES (NEW.object_id, TG_ARGV[0]);
    INSERT INTO application_object_aliases(namespace, alias, object_id)
    VALUES (TG_ARGV[1], NEW.id::text, NEW.object_id);
    RETURN NEW;
END;
$$;

CREATE TRIGGER engines_object_identity BEFORE INSERT OR UPDATE ON engines
FOR EACH ROW EXECUTE FUNCTION application_bind_object('engine', 'legacy.engine');
CREATE TRIGGER tournaments_object_identity BEFORE INSERT OR UPDATE ON tournaments
FOR EACH ROW EXECUTE FUNCTION application_bind_object('competition', 'legacy.tournament');
CREATE TRIGGER workers_object_identity BEFORE INSERT OR UPDATE ON workers
FOR EACH ROW EXECUTE FUNCTION application_bind_object('agent', 'legacy.worker');

CREATE TRIGGER engines_register_object AFTER INSERT OR UPDATE ON engines
FOR EACH ROW EXECUTE FUNCTION application_register_object('engine', 'legacy.engine');
CREATE TRIGGER tournaments_register_object AFTER INSERT OR UPDATE ON tournaments
FOR EACH ROW EXECUTE FUNCTION application_register_object('competition', 'legacy.tournament');
CREATE TRIGGER workers_register_object AFTER INSERT OR UPDATE ON workers
FOR EACH ROW EXECUTE FUNCTION application_register_object('agent', 'legacy.worker');

UPDATE engines SET object_id = NULL;
UPDATE tournaments SET object_id = NULL;
UPDATE workers SET object_id = NULL;
SET CONSTRAINTS engines_application_object_fk, tournaments_application_object_fk, workers_application_object_fk IMMEDIATE;
ALTER TABLE engines ALTER COLUMN object_id SET NOT NULL;
ALTER TABLE tournaments ALTER COLUMN object_id SET NOT NULL;
ALTER TABLE workers ALTER COLUMN object_id SET NOT NULL;
SET CONSTRAINTS engines_application_object_fk, tournaments_application_object_fk, workers_application_object_fk DEFERRED;

CREATE FUNCTION application_resource_revision() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    content_changed BOOLEAN;
    state_changed BOOLEAN;
BEGIN
    IF TG_TABLE_NAME = 'engines' THEN
        content_changed := NEW.name IS DISTINCT FROM OLD.name OR NEW.author IS DISTINCT FROM OLD.author;
        state_changed := NEW.active IS DISTINCT FROM OLD.active;
    ELSIF TG_TABLE_NAME = 'tournaments' THEN
        content_changed := NEW.name IS DISTINCT FROM OLD.name;
        state_changed := NEW.status IS DISTINCT FROM OLD.status;
    ELSE
        content_changed := NEW.label IS DISTINCT FROM OLD.label;
        state_changed := NEW.status IS DISTINCT FROM OLD.status;
    END IF;
    IF content_changed OR state_changed THEN
        UPDATE application_objects SET
            content_revision = content_revision + content_changed::int,
            state_version = state_version + state_changed::int
        WHERE id = NEW.object_id;
        INSERT INTO application_outbox(object_id, kind, payload)
        VALUES (NEW.object_id, TG_ARGV[0], jsonb_build_object('content_changed', content_changed, 'state_changed', state_changed));
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER engines_object_revision AFTER UPDATE ON engines
FOR EACH ROW EXECUTE FUNCTION application_resource_revision('engine.changed');
CREATE TRIGGER tournaments_object_revision AFTER UPDATE ON tournaments
FOR EACH ROW EXECUTE FUNCTION application_resource_revision('competition.changed');
CREATE TRIGGER workers_object_revision AFTER UPDATE ON workers
FOR EACH ROW EXECUTE FUNCTION application_resource_revision('agent.changed');

CREATE FUNCTION application_drain_revision() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    target UUID;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.draining IS NOT DISTINCT FROM OLD.draining THEN
            RETURN NEW;
        END IF;
    END IF;
    SELECT object_id INTO STRICT target FROM workers WHERE id = NEW.worker_id;
    UPDATE application_objects SET state_version = state_version + 1 WHERE id = target;
    INSERT INTO application_outbox(object_id, kind, payload)
    VALUES (target, 'agent.drain_changed', jsonb_build_object('draining', NEW.draining));
    RETURN NEW;
END;
$$;

CREATE TRIGGER worker_settings_object_revision AFTER INSERT OR UPDATE ON control_worker_settings
FOR EACH ROW EXECUTE FUNCTION application_drain_revision();
