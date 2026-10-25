INSERT INTO application_object_types(key, version) VALUES ('artifact', 1), ('artifact_upload', 1);

ALTER TABLE form_responses ADD COLUMN attachment_subject UUID NOT NULL DEFAULT gen_random_uuid();

CREATE TABLE artifact_upload_sessions (
    id UUID PRIMARY KEY REFERENCES application_objects(id),
    owner_actor TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose = 'form_attachment'),
    form_id UUID NOT NULL REFERENCES form_definitions(id),
    release_id UUID NOT NULL REFERENCES form_releases(id),
    subject UUID NOT NULL,
    field_path JSONB NOT NULL,
    filename TEXT NOT NULL CHECK (length(filename) BETWEEN 1 AND 200),
    expected_size BIGINT NOT NULL CHECK (expected_size > 0),
    expected_digest TEXT NOT NULL CHECK (expected_digest ~ '^[0-9a-f]{64}$'),
    media_type TEXT NOT NULL CHECK (media_type IN ('text/plain', 'application/octet-stream')),
    state TEXT NOT NULL DEFAULT 'receiving' CHECK (state IN ('receiving', 'verifying', 'stored', 'rejected', 'expired', 'abandoned')),
    lease UUID,
    lease_until TIMESTAMPTZ,
    installed_intent UUID,
    artifact_id UUID REFERENCES application_objects(id),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp() + interval '24 hours',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((state = 'stored') = (artifact_id IS NOT NULL))
);

CREATE INDEX artifact_upload_owner ON artifact_upload_sessions(owner_actor, state);

CREATE TABLE artifact_storage_intents (
    id UUID PRIMARY KEY,
    upload_id UUID NOT NULL REFERENCES artifact_upload_sessions(id),
    physical_key UUID NOT NULL UNIQUE,
    lease UUID NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'installed', 'abandoned')),
    validation_policy TEXT,
    error_code TEXT,
    physical_reserved BOOLEAN NOT NULL DEFAULT TRUE,
    validated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((state = 'installed') = (validated_at IS NOT NULL))
);

ALTER TABLE artifact_upload_sessions ADD FOREIGN KEY (installed_intent) REFERENCES artifact_storage_intents(id);

CREATE TABLE artifact_blobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    digest_algorithm TEXT NOT NULL DEFAULT 'sha256' CHECK (digest_algorithm = 'sha256'),
    digest TEXT NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
    size BIGINT NOT NULL CHECK (size > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE artifact_replicas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    blob_id UUID NOT NULL REFERENCES artifact_blobs(id),
    physical_key UUID NOT NULL UNIQUE,
    generation UUID NOT NULL DEFAULT gen_random_uuid(),
    state TEXT NOT NULL DEFAULT 'ready' CHECK (state IN ('ready', 'missing', 'corrupt', 'retiring', 'deleting', 'deleted')),
    installed_intent UUID NOT NULL UNIQUE REFERENCES artifact_storage_intents(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE artifact_descriptors (
    artifact_id UUID NOT NULL REFERENCES application_objects(id),
    revision BIGINT NOT NULL CHECK (revision > 0),
    upload_id UUID NOT NULL UNIQUE REFERENCES artifact_upload_sessions(id),
    blob_id UUID NOT NULL REFERENCES artifact_blobs(id),
    owner_actor TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose = 'form_attachment'),
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL,
    validation_policy TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (artifact_id, revision)
);

CREATE TABLE artifact_retention_roots (
    artifact_id UUID NOT NULL,
    descriptor_revision BIGINT NOT NULL,
    consumer_response UUID NOT NULL,
    consumer_revision BIGINT NOT NULL,
    field_path JSONB NOT NULL,
    reason TEXT NOT NULL CHECK (reason = 'form_response_evidence'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (artifact_id, descriptor_revision) REFERENCES artifact_descriptors(artifact_id, revision),
    FOREIGN KEY (consumer_response, consumer_revision) REFERENCES form_response_revisions(response_id, revision),
    PRIMARY KEY (artifact_id, descriptor_revision, consumer_response, consumer_revision, field_path)
);

CREATE TRIGGER artifact_descriptor_immutable BEFORE UPDATE OR DELETE ON artifact_descriptors
FOR EACH ROW EXECUTE FUNCTION form_immutable_record();
CREATE TRIGGER artifact_blob_immutable BEFORE UPDATE OR DELETE ON artifact_blobs
FOR EACH ROW EXECUTE FUNCTION form_immutable_record();
