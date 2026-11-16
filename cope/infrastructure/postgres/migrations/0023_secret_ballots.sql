CREATE TABLE secret_ballot_forms (
    slug TEXT PRIMARY KEY CHECK (length(slug) <= 80 AND slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    settings JSONB NOT NULL,
    listed BOOLEAN NOT NULL DEFAULT FALSE,
    revision BIGINT NOT NULL DEFAULT 1,
    receipt_private BYTEA NOT NULL CHECK (octet_length(receipt_private) = 32),
    manifest JSONB,
    manifest_digest TEXT CHECK (length(manifest_digest) = 64),
    opened_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    board_digest TEXT CHECK (length(board_digest) = 64),
    results JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((manifest IS NULL) = (opened_at IS NULL)),
    CHECK ((manifest_digest IS NULL) = (manifest IS NULL)),
    CHECK ((board_digest IS NULL) = (closed_at IS NULL)),
    CHECK (closed_at IS NULL OR opened_at IS NOT NULL),
    CHECK (results IS NULL OR closed_at IS NOT NULL)
);

CREATE INDEX secret_ballot_forms_directory ON secret_ballot_forms (opened_at DESC, slug) WHERE listed;

CREATE TABLE secret_ballots (
    form_slug TEXT NOT NULL REFERENCES secret_ballot_forms(slug),
    credential TEXT NOT NULL CHECK (length(credential) = 64),
    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
    envelope JSONB NOT NULL,
    receipt_signature TEXT NOT NULL CHECK (length(receipt_signature) = 128),
    PRIMARY KEY (form_slug, credential),
    UNIQUE (form_slug, fingerprint)
);

CREATE TABLE secret_ballot_shares (
    form_slug TEXT NOT NULL REFERENCES secret_ballot_forms(slug),
    trustee TEXT NOT NULL,
    share JSONB NOT NULL,
    PRIMARY KEY (form_slug, trustee)
);

CREATE FUNCTION keep_secret_ballot_publication() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.manifest IS NOT NULL AND (
        NEW.manifest IS DISTINCT FROM OLD.manifest OR NEW.manifest_digest IS DISTINCT FROM OLD.manifest_digest OR
        NEW.receipt_private IS DISTINCT FROM OLD.receipt_private OR NEW.settings IS DISTINCT FROM OLD.settings OR
        NEW.opened_at IS DISTINCT FROM OLD.opened_at
    ) THEN
        RAISE EXCEPTION 'The published ballot manifest is immutable';
    END IF;
    IF OLD.closed_at IS NOT NULL AND (
        NEW.closed_at IS DISTINCT FROM OLD.closed_at OR NEW.board_digest IS DISTINCT FROM OLD.board_digest
    ) THEN
        RAISE EXCEPTION 'The closed ballot board is immutable';
    END IF;
    IF OLD.results IS NOT NULL AND NEW.results IS DISTINCT FROM OLD.results THEN
        RAISE EXCEPTION 'Published ballot results are immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER secret_ballot_publication_immutable BEFORE UPDATE ON secret_ballot_forms
FOR EACH ROW EXECUTE FUNCTION keep_secret_ballot_publication();

CREATE TRIGGER secret_ballot_envelope_immutable BEFORE UPDATE OR DELETE ON secret_ballots
FOR EACH ROW EXECUTE FUNCTION form_immutable_record();

CREATE TRIGGER secret_ballot_share_immutable BEFORE UPDATE OR DELETE ON secret_ballot_shares
FOR EACH ROW EXECUTE FUNCTION form_immutable_record();
