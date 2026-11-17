ALTER TABLE cope_notifications.discord_settings
    ADD COLUMN oauth_client_id TEXT CHECK (oauth_client_id ~ '^[1-9][0-9]{0,19}$'),
    ADD COLUMN oauth_encrypted_secret BYTEA,
    ADD COLUMN oauth_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN oauth_generation BIGINT NOT NULL DEFAULT 1,
    ADD CONSTRAINT discord_oauth_credentials_required
        CHECK (NOT oauth_enabled OR (oauth_client_id IS NOT NULL AND oauth_encrypted_secret IS NOT NULL));

CREATE TABLE discord_accounts (
    id TEXT PRIMARY KEY CHECK (id ~ '^[1-9][0-9]{0,19}$'),
    username TEXT NOT NULL CHECK (length(username) BETWEEN 1 AND 64),
    display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 64)
);

CREATE TABLE discord_web_sessions (
    token_hash TEXT PRIMARY KEY CHECK (length(token_hash) = 64),
    account_id TEXT NOT NULL REFERENCES discord_accounts(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX discord_web_sessions_expires ON discord_web_sessions(expires_at);

CREATE TABLE discord_oauth_flows (
    state_hash TEXT PRIMARY KEY CHECK (length(state_hash) = 64),
    browser_hash TEXT NOT NULL CHECK (length(browser_hash) = 64),
    verifier TEXT NOT NULL,
    destination TEXT NOT NULL,
    generation BIGINT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX discord_oauth_flows_expires ON discord_oauth_flows(expires_at);

ALTER TABLE chat_messages ADD COLUMN discord_username TEXT;

CREATE TABLE form_discord_participation (
    form_id UUID NOT NULL REFERENCES form_definitions(id),
    participant_hash TEXT NOT NULL CHECK (length(participant_hash) = 64),
    PRIMARY KEY (form_id, participant_hash)
);

INSERT INTO form_private_keys(purpose, secret)
VALUES ('discord_participation', gen_random_uuid()::text || gen_random_uuid()::text);

CREATE INDEX form_responses_release_public ON form_responses(form_id, release_id, id)
WHERE withdrawn_at IS NULL;

DO $$
DECLARE
    old_ballot RECORD;
    new_form UUID;
    new_copy UUID;
    new_slug TEXT;
    fields JSONB;
BEGIN
    FOR old_ballot IN SELECT slug, settings FROM secret_ballot_forms ORDER BY slug LOOP
        new_form := gen_random_uuid();
        new_copy := gen_random_uuid();
        new_slug := old_ballot.slug;
        IF EXISTS (SELECT 1 FROM form_definitions WHERE slug = new_slug) THEN
            new_slug := 'imported-' || new_form::TEXT;
        END IF;

        SELECT jsonb_agg(jsonb_build_object(
            'id', 'question_' || questions.ordinality,
            'kind', 'choice',
            'label', questions.question->>'title',
            'required', TRUE,
            'choices', (
                SELECT jsonb_agg(jsonb_build_object('id', 'option_' || options.ordinality, 'label', options.label)
                                 ORDER BY options.ordinality)
                FROM jsonb_array_elements_text(questions.question->'options') WITH ORDINALITY AS options(label, ordinality)
            )
        ) ORDER BY questions.ordinality)
        INTO fields
        FROM jsonb_array_elements(old_ballot.settings->'questions') WITH ORDINALITY AS questions(question, ordinality);

        INSERT INTO application_objects(id, type_key) VALUES (new_form, 'form'), (new_copy, 'form_working_copy');
        INSERT INTO form_definitions(id, slug, label, owner_actor)
        VALUES (new_form, new_slug, old_ballot.settings->>'title', 'browser:admin');
        INSERT INTO form_working_copies(id, form_id, owner_actor)
        VALUES (new_copy, new_form, 'browser:admin');
        INSERT INTO form_working_copy_revisions(copy_id, revision, basis_version, document, actor)
        VALUES (new_copy, 1, 1, jsonb_build_object(
            'title', old_ballot.settings->>'title',
            'description', COALESCE(old_ballot.settings->>'description', ''),
            'binding', 'response_collection.v1', 'audience', 'public',
            'anonymous', TRUE, 'listed', FALSE, 'response_visibility', 'private', 'fields', fields
        ), 'browser:admin');
    END LOOP;
END;
$$;
