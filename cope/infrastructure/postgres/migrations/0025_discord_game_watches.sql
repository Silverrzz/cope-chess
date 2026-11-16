ALTER TABLE cope_notifications.subscribers ADD COLUMN all_tournament_games_since timestamptz;
ALTER TABLE cope_notifications.deliveries ADD COLUMN version_id bigint NOT NULL DEFAULT 0;
ALTER TABLE cope_notifications.discord_settings ADD COLUMN channel_id text CHECK (channel_id ~ '^[0-9]{1,20}$');
UPDATE cope_notifications.discord_settings
SET enabled = false, revision = revision + 1, active_since = clock_timestamp()
WHERE enabled;
ALTER TABLE cope_notifications.discord_settings
ADD CONSTRAINT discord_channel_required CHECK (NOT enabled OR channel_id IS NOT NULL);
UPDATE cope_notifications.deliveries SET outcome = 'suppressed' WHERE outcome IS NULL;

CREATE VIEW cope_notifications.discord_destination AS
SELECT channel_id FROM cope_notifications.discord_settings WHERE singleton AND enabled;
GRANT SELECT ON cope_notifications.discord_destination TO cope_discord_dispatcher;

ALTER TABLE cope_notifications.subscriptions DROP CONSTRAINT subscriptions_kinds_check;
ALTER TABLE cope_notifications.subscriptions ADD CONSTRAINT subscriptions_kinds_check CHECK (
    cardinality(kinds) > 0 AND kinds <@ ARRAY[
        'game_started', 'tournament_started', 'event_game_started', 'event_started',
        'rating_placed', 'rating_updated', 'gauntlet_hero_started'
    ]
);

CREATE INDEX notification_live_games ON public.games (id) WHERE status = 'live';
CREATE INDEX notification_pending_by_fact
ON cope_notifications.deliveries (fact_id, version_id, discord_id) WHERE outcome IS NULL;

CREATE OR REPLACE VIEW cope_notifications.catalog AS
SELECT engine.id AS family_id, 0::bigint AS version_id, engine.name AS label
FROM public.engines engine, cope_notifications.public_platform platform
WHERE platform.enabled AND EXISTS (
    SELECT 1 FROM public.engine_versions version WHERE version.engine_id = engine.id AND NOT version.public_hidden
)
UNION ALL
SELECT engine.id, version.id, engine.name || ' ' || version.version
FROM public.engines engine
JOIN public.engine_versions version ON version.engine_id = engine.id
CROSS JOIN cope_notifications.public_platform platform
WHERE platform.enabled AND NOT version.public_hidden;

CREATE VIEW cope_notifications.game_lineup AS
SELECT game.id AS game_id, seat.side, COALESCE(member.engine_version_id, seat.version_id) AS version_id,
       COALESCE(member.position, 0) AS position
FROM public.games game
CROSS JOIN LATERAL (VALUES (game.white_engine_id, 0), (game.black_engine_id, 1)) seat(version_id, side)
LEFT JOIN public.engine_relay_fixtures fixture ON fixture.tournament_id = game.tournament_id
LEFT JOIN public.engine_relay_fixture_teams team
    ON team.fixture_id = fixture.id AND team.anchor_engine_id = seat.version_id
LEFT JOIN public.event_cast_members member
    ON member.parent_id = team.team_id AND member.kind = 'engine' AND member.engine_version_id IS NOT NULL;

CREATE VIEW cope_notifications.live_games AS
SELECT game.id AS game_id, game.started_at::timestamptz AS started_at,
       game.tournament_id, tournament_slug.slug AS tournament_slug,
       event.slug AS event_slug,
       COALESCE(event.title, tournament.name) AS title, version.engine_id AS family_id,
       version.id AS version_id, engine.name || ' ' || version.version AS engine
FROM public.games game
JOIN public.tournaments tournament ON tournament.id = game.tournament_id
JOIN cope_notifications.game_lineup lineup ON lineup.game_id = game.id
JOIN public.engine_versions version ON version.id = lineup.version_id
JOIN public.engines engine ON engine.id = version.engine_id
CROSS JOIN cope_notifications.public_platform platform
LEFT JOIN cope_notifications.event_tournaments relation ON relation.tournament_id = game.tournament_id
LEFT JOIN public.events event ON event.id = relation.event_id
LEFT JOIN public.public_slugs tournament_slug ON tournament_slug.kind = 'tournament'
    AND tournament_slug.scope_id = 0 AND tournament_slug.resource_id = tournament.id AND tournament_slug.canonical
WHERE platform.enabled AND game.status = 'live' AND game.started_at::timestamptz <= clock_timestamp()
  AND NOT version.public_hidden
  AND tournament.status IN ('running', 'paused')
  AND (relation.event_id IS NULL OR (event.published_at IS NOT NULL AND event.status NOT IN ('draft', 'cancelled')));

GRANT SELECT ON cope_notifications.live_games TO cope_discord_dispatcher;

CREATE VIEW cope_notifications.eligible_mentions AS
WITH matching AS (
SELECT subscription.discord_id, fact.id AS fact_id, version.id AS version_id,
       engine.name || ' ' || version.version AS engine, fact.payload->>'hero_version_id' = version.id::text AS hero
FROM cope_notifications.public_facts fact
JOIN public.engine_versions version ON version.id = ANY(fact.version_ids)
JOIN public.engines engine ON engine.id = version.engine_id
JOIN cope_notifications.subscriptions subscription ON subscription.family_id = version.engine_id
JOIN cope_notifications.subscribers subscriber ON subscriber.discord_id = subscription.discord_id
WHERE NOT subscriber.paused AND NOT version.public_hidden
  AND (subscription.version_id = 0 OR subscription.version_id = version.id)
  AND (fact.rating_list_id IS NULL OR subscription.rating_list_id IS NULL OR subscription.rating_list_id = fact.rating_list_id)
  AND (fact.kind = ANY(subscription.kinds) OR (
      fact.kind = 'tournament_started' AND 'gauntlet_hero_started' = ANY(subscription.kinds)
      AND fact.payload->>'hero_version_id' = version.id::text
  ))
  AND GREATEST(fact.occurred_at, fact.recorded_at) >= GREATEST(subscriber.active_since, subscription.active_since)
UNION ALL
SELECT subscriber.discord_id, fact.id, 0::bigint, NULL::text, false
FROM cope_notifications.public_facts fact
JOIN cope_notifications.subscribers subscriber ON (
    GREATEST(fact.occurred_at, fact.recorded_at) >= GREATEST(subscriber.active_since, subscriber.all_tournament_games_since)
)
WHERE fact.kind = 'game_started' AND subscriber.all_tournament_games_since IS NOT NULL
  AND NOT subscriber.paused
)
SELECT DISTINCT ON (discord_id, fact_id) discord_id, fact_id, version_id, engine
FROM matching ORDER BY discord_id, fact_id, version_id = 0, hero DESC NULLS LAST, version_id;

GRANT SELECT ON cope_notifications.eligible_mentions TO cope_discord_dispatcher;

CREATE OR REPLACE VIEW cope_notifications.eligible_deliveries AS
SELECT discord_id, fact_id FROM cope_notifications.eligible_mentions;

CREATE FUNCTION cope_notifications.record_rating_version_difference() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
    prior_elo integer;
BEGIN
    SELECT floor(rating.elo + 0.5)::integer INTO prior_elo
    FROM public.engine_versions published
    JOIN public.engine_versions previous ON previous.engine_id = published.engine_id
    JOIN public.rating_list_ratings rating ON rating.engine_id = previous.id AND rating.rating_list_id = NEW.rating_list_id
    WHERE published.id = NEW.version_ids[1] AND NOT previous.public_hidden
      AND (COALESCE(previous.source_date, previous.created_at), previous.id)
          < (COALESCE(published.source_date, published.created_at), published.id)
    ORDER BY COALESCE(previous.source_date, previous.created_at) DESC, previous.id DESC LIMIT 1;
    IF prior_elo IS NOT NULL THEN
        NEW.payload := NEW.payload || jsonb_build_object('version_delta', (NEW.payload->>'elo')::integer - prior_elo);
    END IF;
    RETURN NEW;
END;
$$;

REVOKE EXECUTE ON FUNCTION cope_notifications.record_rating_version_difference() FROM PUBLIC;

CREATE TRIGGER notification_rating_version_difference
BEFORE INSERT ON cope_notifications.facts FOR EACH ROW
WHEN (NEW.kind IN ('rating_placed', 'rating_updated'))
EXECUTE FUNCTION cope_notifications.record_rating_version_difference();

CREATE OR REPLACE FUNCTION cope_notifications.capture_start() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
    fact_kind text;
    source_event bigint;
    source_tournament bigint;
    source_game bigint;
    versions bigint[];
    details jsonb;
    hero_version_id bigint;
    hero_label text;
    started timestamptz := NEW.started_at::timestamptz;
    all_tournament_games boolean;
BEGIN
    IF NOT (SELECT enabled FROM cope_notifications.public_platform)
       OR NOT (SELECT enabled FROM cope_notifications.discord_settings WHERE singleton) THEN
        RETURN NULL;
    END IF;

    all_tournament_games := TG_TABLE_NAME = 'games' AND EXISTS (
        SELECT 1 FROM cope_notifications.subscribers
        WHERE all_tournament_games_since IS NOT NULL AND NOT paused
    );
    IF NOT all_tournament_games AND NOT EXISTS (SELECT 1 FROM cope_notifications.subscriptions) THEN
        RETURN NULL;
    END IF;

    IF TG_TABLE_NAME = 'events' THEN
        fact_kind := 'event_started';
        source_event := NEW.id;
        SELECT array_agg(DISTINCT member.engine_version_id)
        INTO versions
        FROM public.event_cast_members member
        LEFT JOIN public.event_cast_members team ON team.id = member.parent_id
        WHERE member.event_id = source_event AND member.kind = 'engine' AND member.status = 'active'
          AND member.engine_version_id IS NOT NULL AND (team.id IS NULL OR team.status = 'active');
        details := jsonb_build_object('title', NEW.title);
    ELSE
        IF TG_TABLE_NAME = 'games' THEN
            source_tournament := NEW.tournament_id;
        ELSE
            source_tournament := NEW.id;
        END IF;
        SELECT event_id INTO source_event FROM cope_notifications.event_tournaments WHERE tournament_id = source_tournament;
        IF TG_TABLE_NAME = 'tournaments' THEN
            IF source_event IS NOT NULL THEN
                RETURN NULL;
            END IF;
            fact_kind := 'tournament_started';
            SELECT array_agg(engine_id) INTO versions FROM public.participants WHERE tournament_id = NEW.id;
            details := jsonb_build_object('title', NEW.name, 'time_control', NEW.config::jsonb->'time_control');
            IF NEW.config::jsonb->>'format' = 'gauntlet' THEN
                hero_version_id := (NEW.config::jsonb #>> '{format_options,hero_engine_id}')::bigint;
                SELECT engine.name || ' ' || version.version INTO hero_label
                FROM public.engine_versions version JOIN public.engines engine ON engine.id = version.engine_id
                WHERE version.id = hero_version_id;
                details := details || jsonb_build_object('hero_version_id', hero_version_id, 'hero', hero_label);
            END IF;
        ELSE
            source_game := NEW.id;
            fact_kind := CASE WHEN source_event IS NULL THEN 'game_started' ELSE 'event_game_started' END;
            SELECT array_agg(DISTINCT lineup.version_id),
                   jsonb_build_object(
                       'white', jsonb_agg(engine.name || ' ' || version.version ORDER BY lineup.position) FILTER (WHERE lineup.side = 0),
                       'black', jsonb_agg(engine.name || ' ' || version.version ORDER BY lineup.position) FILTER (WHERE lineup.side = 1)
                   )
            INTO versions, details
            FROM cope_notifications.game_lineup lineup
            JOIN public.engine_versions version ON version.id = lineup.version_id
            JOIN public.engines engine ON engine.id = version.engine_id
            WHERE lineup.game_id = source_game;

            SELECT details || jsonb_build_object(
                'title', COALESCE(event.title, tournament.name),
                'time_control', tournament.config::jsonb->'time_control',
                'variant', tournament.config::jsonb->>'variant',
                'format', tournament.config::jsonb->>'format'
            )
            INTO details
            FROM public.tournaments tournament
            LEFT JOIN public.events event ON event.id = source_event
            WHERE tournament.id = source_tournament;
        END IF;
    END IF;

    IF versions IS NULL OR details IS NULL
       OR (source_event IS NOT NULL AND NOT EXISTS (
           SELECT 1 FROM public.events WHERE id = source_event AND published_at IS NOT NULL AND status <> 'draft'
       ))
       OR ((fact_kind <> 'game_started' OR NOT all_tournament_games) AND NOT EXISTS (
           SELECT 1 FROM cope_notifications.subscriptions subscription
           JOIN public.engine_versions version ON version.engine_id = subscription.family_id
           WHERE version.id = ANY(versions) AND subscription.version_id IN (0, version.id)
             AND (fact_kind = ANY(subscription.kinds) OR (
                 version.id = hero_version_id AND 'gauntlet_hero_started' = ANY(subscription.kinds)
             ))
       )) THEN
        RETURN NULL;
    END IF;

    INSERT INTO cope_notifications.starts (kind, subject_id) VALUES (fact_kind, NEW.id) ON CONFLICT DO NOTHING;
    IF FOUND THEN
        INSERT INTO cope_notifications.facts
            (kind, tournament_id, event_id, game_id, version_ids, occurred_at, expires_at, payload)
        VALUES (
            fact_kind, source_tournament, source_event, source_game, versions, started,
            started + CASE WHEN source_game IS NULL THEN interval '6 hours' ELSE interval '2 minutes' END,
            details
        );
    END IF;
    RETURN NULL;
END;
$$;
