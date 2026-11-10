CREATE TABLE public_slugs (
    kind text NOT NULL,
    scope_id bigint NOT NULL,
    resource_id bigint NOT NULL,
    slug text NOT NULL CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND slug !~ '^[0-9]+$'),
    canonical boolean NOT NULL DEFAULT true,
    PRIMARY KEY (kind, scope_id, slug)
);

CREATE UNIQUE INDEX public_slugs_canonical ON public_slugs (kind, scope_id, resource_id) WHERE canonical;

CREATE FUNCTION reserve_public_slug(kind text, scope_id bigint, resource_id bigint, title text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    base text;
    candidate text;
    suffix integer := 0;
    edition text[];
    months text[] := ARRAY['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'];
BEGIN
    base := trim(both '-' from regexp_replace(regexp_replace(lower(normalize(title, NFKD)), '[̀-ͯ]', '', 'g'), '[^a-z0-9]+', '-', 'g'));
    IF kind = 'tournament' THEN
        edition := regexp_match(base, '^(.*)-(' || array_to_string(months, '|') || ')-([0-9]{4})$');
        IF edition IS NOT NULL THEN
            base := edition[1] || '-' || edition[3] || '-' || lpad(array_position(months, edition[2])::text, 2, '0');
        END IF;
    END IF;
    IF base = '' OR base ~ '^[0-9]+$' THEN
        base := concat_ws('-', kind, nullif(base, ''));
    END IF;
    candidate := base;
    LOOP
        INSERT INTO public_slugs VALUES (kind, scope_id, resource_id, candidate, true)
        ON CONFLICT DO NOTHING;
        IF FOUND OR EXISTS (
            SELECT 1 FROM public_slugs p
            WHERE p.kind = $1 AND p.scope_id = $2 AND p.resource_id = $3 AND p.canonical
        ) THEN
            RETURN;
        END IF;
        suffix := suffix + 1;
        candidate := base || '-' || (suffix + 1);
    END LOOP;
END;
$$;

CREATE FUNCTION reserve_new_public_slug() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'engine_versions' THEN
        PERFORM reserve_public_slug('version', NEW.engine_id, NEW.id, NEW.version);
    ELSE
        PERFORM reserve_public_slug(TG_ARGV[0], 0, NEW.id, NEW.name);
    END IF;
    RETURN NEW;
END;
$$;

SELECT reserve_public_slug('engine', 0, id, name) FROM (SELECT id, name FROM engines ORDER BY id) e;
SELECT reserve_public_slug('version', engine_id, id, version) FROM (SELECT id, engine_id, version FROM engine_versions ORDER BY id) v;
SELECT reserve_public_slug('rating', 0, id, name) FROM (SELECT id, name FROM rating_lists ORDER BY id) r;
SELECT reserve_public_slug('tournament', 0, id, name) FROM (SELECT id, name FROM tournaments ORDER BY id) t;

CREATE TRIGGER engine_public_slug AFTER INSERT ON engines FOR EACH ROW EXECUTE FUNCTION reserve_new_public_slug('engine');
CREATE TRIGGER version_public_slug AFTER INSERT ON engine_versions FOR EACH ROW EXECUTE FUNCTION reserve_new_public_slug('version');
CREATE TRIGGER rating_public_slug AFTER INSERT ON rating_lists FOR EACH ROW EXECUTE FUNCTION reserve_new_public_slug('rating');
CREATE TRIGGER tournament_public_slug AFTER INSERT ON tournaments FOR EACH ROW EXECUTE FUNCTION reserve_new_public_slug('tournament');
