INSERT INTO application_object_types(key, version)
VALUES ('form', 1), ('form_working_copy', 1), ('form_response', 1);

CREATE TABLE form_definitions (
    id UUID PRIMARY KEY,
    type_key TEXT NOT NULL DEFAULT 'form' CHECK (type_key = 'form'),
    slug TEXT NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$' AND length(slug) <= 100),
    label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 300),
    owner_actor TEXT NOT NULL,
    published_release UUID,
    closed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (id, type_key) REFERENCES application_objects(id, type_key)
);

CREATE TABLE form_permissions (
    form_id UUID NOT NULL REFERENCES form_definitions(id),
    actor TEXT NOT NULL,
    permission TEXT NOT NULL CHECK (permission IN ('read', 'edit', 'review', 'responses')),
    granted_by TEXT NOT NULL,
    PRIMARY KEY (form_id, actor, permission)
);

CREATE TABLE form_working_copies (
    id UUID PRIMARY KEY,
    type_key TEXT NOT NULL DEFAULT 'form_working_copy' CHECK (type_key = 'form_working_copy'),
    form_id UUID NOT NULL REFERENCES form_definitions(id),
    owner_actor TEXT NOT NULL,
    current_revision BIGINT NOT NULL DEFAULT 1 CHECK (current_revision > 0),
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (id, type_key) REFERENCES application_objects(id, type_key)
);

CREATE INDEX form_working_copies_owner ON form_working_copies(owner_actor, form_id, updated_at);

CREATE TABLE form_working_copy_revisions (
    copy_id UUID NOT NULL REFERENCES form_working_copies(id),
    revision BIGINT NOT NULL CHECK (revision > 0),
    basis_version BIGINT NOT NULL CHECK (basis_version > 0),
    document JSONB NOT NULL CHECK (octet_length(document::text) <= 1048576),
    actor TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (copy_id, revision)
);

ALTER TABLE form_working_copies ADD FOREIGN KEY (id, current_revision)
REFERENCES form_working_copy_revisions(copy_id, revision) DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE form_releases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    form_id UUID NOT NULL REFERENCES form_definitions(id),
    number BIGINT NOT NULL CHECK (number > 0),
    copy_id UUID NOT NULL,
    copy_revision BIGINT NOT NULL,
    definition JSONB NOT NULL,
    definition_digest TEXT NOT NULL CHECK (length(definition_digest) = 64),
    binding_key TEXT NOT NULL,
    policy_revision TEXT NOT NULL,
    publisher_actor TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (form_id, number),
    UNIQUE (form_id, id),
    FOREIGN KEY (copy_id, copy_revision) REFERENCES form_working_copy_revisions(copy_id, revision)
);

ALTER TABLE form_definitions ADD FOREIGN KEY (id, published_release)
REFERENCES form_releases(form_id, id) DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE form_release_acceptance (
    release_id UUID PRIMARY KEY REFERENCES form_releases(id),
    accepting BOOLEAN NOT NULL DEFAULT TRUE,
    valid_until TIMESTAMPTZ,
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE form_claims (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    form_id UUID NOT NULL REFERENCES form_definitions(id),
    token_hash TEXT NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    CHECK (expires_at > created_at)
);

CREATE TABLE form_responses (
    id UUID PRIMARY KEY,
    type_key TEXT NOT NULL DEFAULT 'form_response' CHECK (type_key = 'form_response'),
    form_id UUID NOT NULL REFERENCES form_definitions(id),
    release_id UUID NOT NULL,
    owner_actor TEXT NOT NULL,
    current_revision BIGINT NOT NULL DEFAULT 1 CHECK (current_revision > 0),
    withdrawn_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (id, type_key) REFERENCES application_objects(id, type_key),
    FOREIGN KEY (form_id, release_id) REFERENCES form_releases(form_id, id)
);

CREATE INDEX form_responses_form ON form_responses(form_id, created_at, id);
CREATE INDEX form_responses_owner ON form_responses(owner_actor, created_at, id);

CREATE TABLE form_response_revisions (
    response_id UUID NOT NULL REFERENCES form_responses(id),
    revision BIGINT NOT NULL CHECK (revision > 0),
    answers JSONB NOT NULL,
    proposal JSONB NOT NULL,
    subject JSONB NOT NULL,
    subject_digest TEXT NOT NULL CHECK (length(subject_digest) = 64),
    actor TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (response_id, revision)
);

ALTER TABLE form_responses ADD FOREIGN KEY (id, current_revision)
REFERENCES form_response_revisions(response_id, revision) DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE form_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    response_id UUID NOT NULL,
    response_revision BIGINT NOT NULL,
    subject_digest TEXT NOT NULL CHECK (length(subject_digest) = 64),
    policy_revision TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('accepted', 'declined', 'changes_requested')),
    reason TEXT NOT NULL CHECK (length(reason) <= 8000),
    actor TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (response_id, response_revision) REFERENCES form_response_revisions(response_id, revision)
);

CREATE INDEX form_reviews_response ON form_reviews(response_id, response_revision, created_at DESC, id);

CREATE TABLE form_rate_limits (
    scope TEXT NOT NULL,
    subject TEXT NOT NULL,
    hour TIMESTAMPTZ NOT NULL,
    count INTEGER NOT NULL CHECK (count > 0),
    PRIMARY KEY (scope, subject, hour)
);

CREATE TABLE form_private_keys (
    purpose TEXT PRIMARY KEY,
    secret TEXT NOT NULL
);

INSERT INTO form_private_keys(purpose, secret)
VALUES ('anonymous_rate_key', gen_random_uuid()::text || gen_random_uuid()::text);

CREATE FUNCTION form_immutable_record() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Published form definitions, saved revisions, answers and review decisions are immutable';
END;
$$;

CREATE TRIGGER form_release_immutable BEFORE UPDATE OR DELETE ON form_releases
FOR EACH ROW EXECUTE FUNCTION form_immutable_record();
CREATE TRIGGER form_copy_revision_immutable BEFORE UPDATE OR DELETE ON form_working_copy_revisions
FOR EACH ROW EXECUTE FUNCTION form_immutable_record();
CREATE TRIGGER form_response_revision_immutable BEFORE UPDATE OR DELETE ON form_response_revisions
FOR EACH ROW EXECUTE FUNCTION form_immutable_record();
CREATE TRIGGER form_review_immutable BEFORE UPDATE OR DELETE ON form_reviews
FOR EACH ROW EXECUTE FUNCTION form_immutable_record();
