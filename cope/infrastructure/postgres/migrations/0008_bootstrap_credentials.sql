INSERT INTO application_object_types(key,version) VALUES ('credential',1);

CREATE TABLE application_bootstrap_epoch (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    generation BIGINT NOT NULL DEFAULT 1 CHECK (generation>0)
);
INSERT INTO application_bootstrap_epoch(singleton) VALUES (TRUE);

CREATE TABLE application_bootstrap_roles (
    role TEXT PRIMARY KEY CHECK (role IN ('admin','manager')),
    generation BIGINT NOT NULL DEFAULT 1 CHECK (generation>0),
    token_digest TEXT CHECK (token_digest ~ '^[a-f0-9]{64}$')
);
INSERT INTO application_bootstrap_roles(role) VALUES ('admin'),('manager');

CREATE TABLE application_browser_sessions (
    id UUID PRIMARY KEY,
    object_type TEXT NOT NULL DEFAULT 'credential' CHECK (object_type='credential'),
    role TEXT NOT NULL REFERENCES application_bootstrap_roles(role),
    role_generation BIGINT NOT NULL CHECK (role_generation>0),
    session_hash TEXT NOT NULL UNIQUE CHECK (session_hash ~ '^[a-f0-9]{64}$'),
    token_digest TEXT NOT NULL CHECK (token_digest ~ '^[a-f0-9]{64}$'),
    issued_at BIGINT NOT NULL CHECK (issued_at>0),
    expires_at BIGINT NOT NULL CHECK (expires_at=issued_at+43200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    revoked_at TIMESTAMPTZ,
    FOREIGN KEY (id,object_type) REFERENCES application_objects(id,type_key)
);
CREATE INDEX application_browser_sessions_active_role ON application_browser_sessions(role) WHERE revoked_at IS NULL;

CREATE FUNCTION application_lock_bootstrap_credentials() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('application-bootstrap-credentials.v1',0));
    UPDATE application_bootstrap_epoch SET generation=generation+1 WHERE singleton;
    RETURN NULL;
END;
$$;

CREATE TRIGGER application_bootstrap_credential_writes BEFORE INSERT OR UPDATE OR DELETE ON admin_access_tokens
FOR EACH STATEMENT EXECUTE FUNCTION application_lock_bootstrap_credentials();

CREATE FUNCTION application_reject_bootstrap_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Bootstrap credentials require scoped rotation or revocation';
END;
$$;

CREATE TRIGGER application_bootstrap_no_truncate BEFORE TRUNCATE ON admin_access_tokens
FOR EACH STATEMENT EXECUTE FUNCTION application_reject_bootstrap_truncate();

CREATE TRIGGER application_bootstrap_role_writes BEFORE INSERT OR UPDATE OR DELETE ON application_bootstrap_roles
FOR EACH STATEMENT EXECUTE FUNCTION application_lock_bootstrap_credentials();
CREATE TRIGGER application_bootstrap_roles_no_truncate BEFORE TRUNCATE ON application_bootstrap_roles
FOR EACH STATEMENT EXECUTE FUNCTION application_reject_bootstrap_truncate();

CREATE FUNCTION application_preserve_bootstrap_role() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP<>'UPDATE' THEN
        RAISE EXCEPTION 'Bootstrap role identities are retained';
    END IF;
    IF NEW.role IS DISTINCT FROM OLD.role OR NEW.generation<>OLD.generation+1 THEN
        RAISE EXCEPTION 'Bootstrap role changes require the next generation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER application_bootstrap_role_identity BEFORE INSERT OR UPDATE OR DELETE ON application_bootstrap_roles
FOR EACH ROW EXECUTE FUNCTION application_preserve_bootstrap_role();

CREATE FUNCTION application_preserve_browser_session() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Browser credential identities are retained';
    END IF;
    IF (to_jsonb(NEW)-'revoked_at') IS DISTINCT FROM (to_jsonb(OLD)-'revoked_at')
       OR OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
        RAISE EXCEPTION 'Only monotonic browser credential revocation is permitted';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER application_browser_session_identity BEFORE UPDATE OR DELETE ON application_browser_sessions
FOR EACH ROW EXECUTE FUNCTION application_preserve_browser_session();
CREATE TRIGGER application_browser_sessions_no_truncate BEFORE TRUNCATE ON application_browser_sessions
FOR EACH STATEMENT EXECUTE FUNCTION application_reject_bootstrap_truncate();

CREATE FUNCTION application_observe_browser_revocation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    version BIGINT;
BEGIN
    IF OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL THEN
        UPDATE application_objects SET state_version=state_version+1 WHERE id=NEW.id RETURNING state_version INTO version;
        INSERT INTO application_outbox(object_id,kind,payload)
        VALUES (NEW.id,'credential.revoked',jsonb_build_object('state_version',version));
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER application_browser_session_revoked AFTER UPDATE ON application_browser_sessions
FOR EACH ROW EXECUTE FUNCTION application_observe_browser_revocation();

CREATE FUNCTION application_revoke_bootstrap_role_sessions() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE application_browser_sessions SET revoked_at=clock_timestamp() WHERE role=NEW.role AND revoked_at IS NULL;
    RETURN NEW;
END;
$$;
CREATE TRIGGER application_bootstrap_role_changed AFTER UPDATE ON application_bootstrap_roles
FOR EACH ROW EXECUTE FUNCTION application_revoke_bootstrap_role_sessions();

CREATE FUNCTION application_changed_bootstrap_secret() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='UPDATE' AND NEW.role=OLD.role AND NEW.token IS NOT DISTINCT FROM OLD.token THEN
        RETURN NEW;
    END IF;
    IF TG_OP<>'INSERT' THEN
        UPDATE application_bootstrap_roles SET generation=generation+1,token_digest=NULL WHERE role=OLD.role;
    END IF;
    IF TG_OP='INSERT' OR TG_OP='UPDATE' AND NEW.role IS DISTINCT FROM OLD.role THEN
        UPDATE application_bootstrap_roles SET generation=generation+1,token_digest=NULL WHERE role=NEW.role;
    END IF;
    RETURN NULL;
END;
$$;
CREATE TRIGGER application_bootstrap_secret_changed AFTER INSERT OR UPDATE OR DELETE ON admin_access_tokens
FOR EACH ROW EXECUTE FUNCTION application_changed_bootstrap_secret();
