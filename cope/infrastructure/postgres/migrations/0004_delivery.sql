CREATE TABLE application_delivery_incarnations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    retired_at TIMESTAMPTZ
);

CREATE TABLE application_delivery_state (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    incarnation UUID NOT NULL REFERENCES application_delivery_incarnations(id),
    retained_after BIGINT NOT NULL DEFAULT 0 CHECK (retained_after >= 0),
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1)
);

WITH incarnation AS (INSERT INTO application_delivery_incarnations DEFAULT VALUES RETURNING id)
INSERT INTO application_delivery_state(singleton,incarnation) SELECT TRUE,id FROM incarnation;

CREATE SEQUENCE application_delivery_position AS BIGINT;

CREATE TABLE application_event_log (
    position BIGINT PRIMARY KEY CHECK (position > 0),
    event_id BIGINT NOT NULL UNIQUE REFERENCES application_outbox(id),
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
    delivered_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(event_id,position)
);

ALTER TABLE application_outbox ADD COLUMN delivered_position BIGINT UNIQUE;
ALTER TABLE application_outbox ADD FOREIGN KEY (id,delivered_position)
REFERENCES application_event_log(event_id,position) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX application_outbox_pending ON application_outbox(id) WHERE delivered_position IS NULL;

CREATE TABLE application_consumer_checkpoints (
    consumer TEXT PRIMARY KEY,
    incarnation UUID NOT NULL REFERENCES application_delivery_incarnations(id),
    applied_position BIGINT NOT NULL DEFAULT 0 CHECK (applied_position >= 0),
    failure_count INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
    last_error TEXT NOT NULL DEFAULT '',
    retry_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE application_consumer_receipts (
    consumer TEXT NOT NULL REFERENCES application_consumer_checkpoints(consumer),
    event_id BIGINT NOT NULL REFERENCES application_outbox(id),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(consumer,event_id)
);

CREATE TABLE application_object_changes (
    object_id UUID PRIMARY KEY REFERENCES application_objects(id),
    delivered_position BIGINT NOT NULL REFERENCES application_event_log(position),
    kind TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE application_private_keys (
    purpose TEXT PRIMARY KEY,
    secret TEXT NOT NULL
);

INSERT INTO application_private_keys(purpose,secret)
VALUES ('delivery_cursor.v1',gen_random_uuid()::text || gen_random_uuid()::text);

CREATE FUNCTION application_preserve_outbox() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Outbox facts require an explicit retention policy';
    END IF;
    IF (to_jsonb(NEW) - 'delivered_position') IS DISTINCT FROM (to_jsonb(OLD) - 'delivered_position')
       OR (OLD.delivered_position IS NOT NULL AND NEW.delivered_position IS DISTINCT FROM OLD.delivered_position) THEN
        RAISE EXCEPTION 'Committed outbox facts and delivery assignments are immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER application_outbox_immutable BEFORE UPDATE OR DELETE ON application_outbox
FOR EACH ROW EXECUTE FUNCTION application_preserve_outbox();

CREATE FUNCTION application_immutable_fact() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Committed facts are immutable';
END;
$$;

CREATE TRIGGER application_event_log_immutable BEFORE UPDATE OR DELETE ON application_event_log
FOR EACH ROW EXECUTE FUNCTION application_immutable_fact();

CREATE TRIGGER application_consumer_receipt_immutable BEFORE UPDATE OR DELETE ON application_consumer_receipts
FOR EACH ROW EXECUTE FUNCTION application_immutable_fact();

DROP TRIGGER artifact_descriptor_immutable ON artifact_descriptors;
CREATE TRIGGER artifact_descriptor_immutable BEFORE UPDATE OR DELETE ON artifact_descriptors
FOR EACH ROW EXECUTE FUNCTION application_immutable_fact();
DROP TRIGGER artifact_blob_immutable ON artifact_blobs;
CREATE TRIGGER artifact_blob_immutable BEFORE UPDATE OR DELETE ON artifact_blobs
FOR EACH ROW EXECUTE FUNCTION application_immutable_fact();
