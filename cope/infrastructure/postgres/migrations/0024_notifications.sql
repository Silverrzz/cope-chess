CREATE SCHEMA cope_notifications;

CREATE TABLE cope_notifications.discord_settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    guild_id text CHECK (guild_id ~ '^[0-9]{1,20}$'),
    encrypted_token bytea,
    enabled boolean NOT NULL DEFAULT false,
    revision bigint NOT NULL DEFAULT 0,
    active_since timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (NOT enabled OR (guild_id IS NOT NULL AND encrypted_token IS NOT NULL))
);

INSERT INTO cope_notifications.discord_settings DEFAULT VALUES;

CREATE TABLE cope_notifications.subscribers (
    discord_id text PRIMARY KEY,
    paused boolean NOT NULL DEFAULT false,
    dm_blocked boolean NOT NULL DEFAULT false,
    active_since timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE cope_notifications.subscriptions (
    discord_id text NOT NULL REFERENCES cope_notifications.subscribers ON DELETE CASCADE,
    family_id bigint NOT NULL,
    version_id bigint NOT NULL DEFAULT 0,
    rating_list_id bigint,
    kinds text[] NOT NULL CHECK (
        cardinality(kinds) > 0 AND kinds <@ ARRAY[
            'game_started', 'tournament_started', 'event_game_started', 'event_started',
            'rating_placed', 'rating_updated'
        ]
    ),
    active_since timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (discord_id, family_id, version_id)
);
CREATE INDEX notification_subscription_family ON cope_notifications.subscriptions (family_id);

CREATE TABLE cope_notifications.starts (
    kind text NOT NULL,
    subject_id bigint NOT NULL,
    PRIMARY KEY (kind, subject_id)
);

CREATE TABLE cope_notifications.facts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind text NOT NULL,
    tournament_id bigint,
    event_id bigint,
    game_id bigint,
    rating_list_id bigint,
    version_ids bigint[] NOT NULL,
    occurred_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    payload jsonb NOT NULL,
    projected boolean NOT NULL DEFAULT false
);
CREATE INDEX notification_facts_pending ON cope_notifications.facts (occurred_at, id) WHERE NOT projected;
CREATE INDEX notification_facts_expiry ON cope_notifications.facts (expires_at);

CREATE TABLE cope_notifications.deliveries (
    discord_id text NOT NULL REFERENCES cope_notifications.subscribers ON DELETE CASCADE,
    fact_id bigint NOT NULL REFERENCES cope_notifications.facts ON DELETE CASCADE,
    ready_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    nonce text,
    claim_token uuid,
    first_attempt_at timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    outcome text CHECK (outcome IN ('sent', 'suppressed', 'blocked', 'failed', 'uncertain')),
    message_id text,
    PRIMARY KEY (discord_id, fact_id)
);
CREATE INDEX notification_deliveries_ready ON cope_notifications.deliveries (ready_at, fact_id) WHERE outcome IS NULL;

CREATE VIEW cope_notifications.public_platform AS
SELECT NOT EXISTS (
    SELECT 1 FROM public.platform_settings
    WHERE key = 'privatise_platform' AND lower(btrim(value)) IN ('true', '1', 'yes', 'on')
) AS enabled;

CREATE VIEW cope_notifications.catalog AS
SELECT engine.id AS family_id, 0::bigint AS version_id, engine.name AS label
FROM public.engines engine, cope_notifications.public_platform platform
WHERE platform.enabled
UNION ALL
SELECT engine.id, version.id, engine.name || ' ' || version.version
FROM public.engines engine
JOIN public.engine_versions version ON version.engine_id = engine.id
CROSS JOIN cope_notifications.public_platform platform
WHERE platform.enabled;

CREATE VIEW cope_notifications.rating_catalog AS
SELECT list.id, list.name
FROM public.rating_lists list, cope_notifications.public_platform platform
WHERE platform.enabled;

CREATE VIEW cope_notifications.event_tournaments AS
SELECT tournament_id, event_id FROM public.engine_relay_fixtures
UNION
SELECT tournament_id, event_id FROM public.puzzle_gauntlet_events WHERE tournament_id IS NOT NULL
UNION
SELECT (config::jsonb->>'tournament_id')::bigint, id FROM public.events
WHERE handler_key = 'duck-vs-fish' AND config::jsonb->>'tournament_id' IS NOT NULL;

CREATE VIEW cope_notifications.public_facts AS
SELECT fact.*, event.slug AS event_slug, tournament_slug.slug AS tournament_slug,
       rating_slug.slug AS rating_slug
FROM cope_notifications.facts fact
CROSS JOIN cope_notifications.public_platform platform
CROSS JOIN cope_notifications.discord_settings settings
LEFT JOIN public.events event ON event.id = fact.event_id
LEFT JOIN public.tournaments tournament ON tournament.id = fact.tournament_id
LEFT JOIN public.games game ON game.id = fact.game_id
LEFT JOIN public.public_slugs tournament_slug ON tournament_slug.kind = 'tournament'
    AND tournament_slug.scope_id = 0 AND tournament_slug.resource_id = fact.tournament_id AND tournament_slug.canonical
LEFT JOIN public.public_slugs rating_slug ON rating_slug.kind = 'rating'
    AND rating_slug.scope_id = 0 AND rating_slug.resource_id = fact.rating_list_id AND rating_slug.canonical
WHERE platform.enabled AND settings.enabled AND settings.singleton
  AND GREATEST(fact.occurred_at, fact.recorded_at) >= settings.active_since
  AND fact.occurred_at <= clock_timestamp()
  AND fact.expires_at > clock_timestamp()
  AND (fact.event_id IS NULL OR (event.published_at IS NOT NULL AND event.status NOT IN ('draft', 'cancelled')))
  AND (fact.tournament_id IS NULL OR tournament.status NOT IN ('draft', 'aborted'))
  AND (fact.game_id IS NULL OR (
      game.status IN ('live', 'finished') AND game.started_at::timestamptz = fact.occurred_at
  ))
  AND (fact.kind <> 'tournament_started' OR tournament.started_at::timestamptz = fact.occurred_at)
  AND (fact.kind <> 'event_started' OR event.started_at::timestamptz = fact.occurred_at)
  AND (fact.event_id IS NOT NULL OR fact.tournament_id IS NULL OR NOT EXISTS (
      SELECT 1 FROM cope_notifications.event_tournaments relation WHERE relation.tournament_id = fact.tournament_id
  ))
  AND (fact.event_id IS NULL OR fact.tournament_id IS NULL OR EXISTS (
      SELECT 1 FROM cope_notifications.event_tournaments relation
      WHERE relation.tournament_id = fact.tournament_id AND relation.event_id = fact.event_id
  ))
  AND (fact.rating_list_id IS NULL OR EXISTS (
      SELECT 1 FROM public.rating_list_ratings rating
      WHERE rating.rating_list_id = fact.rating_list_id AND rating.engine_id = ANY(fact.version_ids)
  ));

CREATE VIEW cope_notifications.eligible_deliveries AS
SELECT DISTINCT subscription.discord_id, fact.id AS fact_id
FROM cope_notifications.public_facts fact
JOIN public.engine_versions version ON version.id = ANY(fact.version_ids)
JOIN cope_notifications.subscriptions subscription ON subscription.family_id = version.engine_id
JOIN cope_notifications.subscribers subscriber ON subscriber.discord_id = subscription.discord_id
WHERE NOT subscriber.paused AND NOT subscriber.dm_blocked
  AND (subscription.version_id = 0 OR subscription.version_id = version.id)
  AND (fact.rating_list_id IS NULL OR subscription.rating_list_id IS NULL OR subscription.rating_list_id = fact.rating_list_id)
  AND fact.kind = ANY(subscription.kinds)
  AND GREATEST(fact.occurred_at, fact.recorded_at) >= GREATEST(subscriber.active_since, subscription.active_since);

CREATE FUNCTION cope_notifications.capture_start() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
    fact_kind text;
    source_event bigint;
    source_tournament bigint;
    source_game bigint;
    versions bigint[];
    details jsonb;
    started timestamptz := NEW.started_at::timestamptz;
    subject bigint := NEW.id;
BEGIN
    IF NOT (SELECT enabled FROM cope_notifications.public_platform)
       OR NOT (SELECT enabled FROM cope_notifications.discord_settings WHERE singleton)
       OR NOT EXISTS (SELECT 1 FROM cope_notifications.subscriptions) THEN
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
        ELSE
            source_game := NEW.id;
            fact_kind := CASE WHEN source_event IS NULL THEN 'game_started' ELSE 'event_game_started' END;
            WITH teams AS (
                SELECT team.team_id, team.anchor_engine_id
                FROM public.engine_relay_fixtures fixture
                JOIN public.engine_relay_fixture_teams team ON team.fixture_id = fixture.id
                WHERE fixture.tournament_id = source_tournament
                  AND team.anchor_engine_id IN (NEW.white_engine_id, NEW.black_engine_id)
            ), lineup AS (
                SELECT member.engine_version_id AS version_id,
                       CASE team.anchor_engine_id WHEN NEW.white_engine_id THEN 0 ELSE 1 END AS side,
                       member.position
                FROM teams team
                JOIN public.event_cast_members member ON member.parent_id = team.team_id
                WHERE member.kind = 'engine' AND member.engine_version_id IS NOT NULL
                UNION ALL
                SELECT seat.version_id, seat.side, 0
                FROM (VALUES (NEW.white_engine_id, 0), (NEW.black_engine_id, 1)) seat(version_id, side)
                WHERE NOT EXISTS (SELECT 1 FROM teams WHERE anchor_engine_id = seat.version_id)
            )
            SELECT array_agg(DISTINCT lineup.version_id),
                   jsonb_build_object(
                       'white', jsonb_agg(engine.name || ' ' || version.version ORDER BY lineup.position) FILTER (WHERE lineup.side = 0),
                       'black', jsonb_agg(engine.name || ' ' || version.version ORDER BY lineup.position) FILTER (WHERE lineup.side = 1)
                   )
            INTO versions, details
            FROM lineup
            JOIN public.engine_versions version ON version.id = lineup.version_id
            JOIN public.engines engine ON engine.id = version.engine_id;

            SELECT details || jsonb_build_object(
                'title', COALESCE(event.title, tournament.name),
                'time_control', tournament.config::jsonb->'time_control',
                'event_type', event.handler_key
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
       OR NOT EXISTS (
           SELECT 1 FROM cope_notifications.subscriptions subscription
           JOIN public.engine_versions version ON version.engine_id = subscription.family_id
           WHERE fact_kind = ANY(subscription.kinds) AND version.id = ANY(versions)
             AND subscription.version_id IN (0, version.id)
       ) THEN
        RETURN NULL;
    END IF;

    INSERT INTO cope_notifications.starts (kind, subject_id) VALUES (fact_kind, subject) ON CONFLICT DO NOTHING;
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

CREATE CONSTRAINT TRIGGER notification_game_start
AFTER UPDATE ON public.games DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
WHEN (OLD.started_at IS NULL AND NEW.started_at IS NOT NULL AND NEW.status = 'live')
EXECUTE FUNCTION cope_notifications.capture_start();

CREATE CONSTRAINT TRIGGER notification_tournament_start
AFTER UPDATE ON public.tournaments DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
WHEN (OLD.started_at IS NULL AND NEW.started_at IS NOT NULL AND NEW.status = 'running')
EXECUTE FUNCTION cope_notifications.capture_start();

CREATE CONSTRAINT TRIGGER notification_event_start
AFTER UPDATE ON public.events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
WHEN (OLD.started_at IS NULL AND NEW.started_at IS NOT NULL AND NEW.status = 'live')
EXECUTE FUNCTION cope_notifications.capture_start();

CREATE TABLE cope_notifications.rating_baseline (
    rating_list_id bigint NOT NULL REFERENCES public.rating_lists ON DELETE CASCADE,
    version_id bigint NOT NULL REFERENCES public.engine_versions ON DELETE CASCADE,
    elo integer NOT NULL,
    uncertainty integer,
    games integer NOT NULL,
    listed boolean NOT NULL,
    PRIMARY KEY (rating_list_id, version_id)
);

INSERT INTO cope_notifications.rating_baseline (rating_list_id, version_id, elo, uncertainty, games, listed)
SELECT rating_list_id, engine_id, floor(elo + 0.5)::int, floor(error_margin + 0.5)::int, games_played, true
FROM public.rating_list_ratings
UNION ALL
SELECT prior.rating_list_id, prior.engine_id, prior.elo, NULL, prior.games_played, false
FROM (
    SELECT DISTINCT ON (rating_list_id, engine_id)
           rating_list_id, engine_id, floor(elo + 0.5)::int AS elo, games_played
    FROM public.engine_elo_history history
    WHERE NOT EXISTS (
        SELECT 1 FROM public.rating_list_ratings rating
        WHERE rating.rating_list_id = history.rating_list_id AND rating.engine_id = history.engine_id
    )
    ORDER BY rating_list_id, engine_id, id DESC
) prior;

CREATE FUNCTION cope_notifications.capture_ratings(list_ids bigint[]) RETURNS void
LANGUAGE sql SET search_path = pg_catalog AS $$
    WITH current_ratings AS MATERIALIZED (
        SELECT rating.rating_list_id, rating.engine_id AS version_id, engine.id AS family_id,
               engine.name || ' ' || version.version AS engine_name, list.name AS list_name,
               floor(rating.elo + 0.5)::int AS elo, floor(rating.error_margin + 0.5)::int AS uncertainty,
               rating.games_played AS games,
               row_number() OVER (
                   PARTITION BY rating.rating_list_id ORDER BY rating.elo DESC, engine.name, version.version
               ) AS rank
        FROM public.rating_list_ratings rating
        JOIN public.engine_versions version ON version.id = rating.engine_id
        JOIN public.engines engine ON engine.id = version.engine_id
        JOIN public.rating_lists list ON list.id = rating.rating_list_id
        WHERE rating.rating_list_id = ANY(list_ids)
    ), changes AS (
        SELECT published.*, baseline.elo AS previous_elo, COALESCE(NOT baseline.listed, false) AS returned,
               CASE WHEN baseline.version_id IS NULL THEN 'rating_placed' ELSE 'rating_updated' END AS kind
        FROM current_ratings published
        LEFT JOIN cope_notifications.rating_baseline baseline USING (rating_list_id, version_id)
        WHERE baseline.version_id IS NULL OR NOT baseline.listed OR
              (published.elo, published.uncertainty, published.games)
              IS DISTINCT FROM (baseline.elo, baseline.uncertainty, baseline.games)
    ), publish AS (
        INSERT INTO cope_notifications.facts (kind, rating_list_id, version_ids, occurred_at, expires_at, payload)
        SELECT change.kind, change.rating_list_id, ARRAY[change.version_id], statement_timestamp(),
               statement_timestamp() + interval '6 hours',
               jsonb_build_object(
                   'engine', change.engine_name, 'title', change.list_name, 'elo', change.elo,
                   'uncertainty', change.uncertainty, 'games', change.games, 'rank', change.rank,
                   'previous_elo', change.previous_elo, 'returned', change.returned
               )
        FROM changes change
        CROSS JOIN cope_notifications.public_platform platform
        CROSS JOIN cope_notifications.discord_settings settings
        WHERE platform.enabled AND settings.enabled AND settings.singleton AND EXISTS (
            SELECT 1 FROM cope_notifications.subscriptions subscription
            WHERE subscription.family_id = change.family_id
              AND subscription.version_id IN (0, change.version_id)
              AND (subscription.rating_list_id IS NULL OR subscription.rating_list_id = change.rating_list_id)
              AND change.kind = ANY(subscription.kinds)
        )
    ), withdrawn AS (
        UPDATE cope_notifications.rating_baseline baseline SET listed = false
        WHERE baseline.rating_list_id = ANY(list_ids) AND baseline.listed AND NOT EXISTS (
            SELECT 1 FROM current_ratings published
            WHERE published.rating_list_id = baseline.rating_list_id AND published.version_id = baseline.version_id
        )
    )
    INSERT INTO cope_notifications.rating_baseline (rating_list_id, version_id, elo, uncertainty, games, listed)
    SELECT rating_list_id, version_id, elo, uncertainty, games, true FROM changes
    ON CONFLICT (rating_list_id, version_id) DO UPDATE
    SET elo = EXCLUDED.elo, uncertainty = EXCLUDED.uncertainty, games = EXCLUDED.games, listed = true;
$$;

REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA cope_notifications FROM PUBLIC;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'cope_discord_dispatcher') THEN
        CREATE ROLE cope_discord_dispatcher NOLOGIN;
    END IF;
    IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = current_user) THEN
        EXECUTE format('GRANT cope_discord_dispatcher TO %I', current_user);
    END IF;
END;
$$;

GRANT USAGE ON SCHEMA cope_notifications TO cope_discord_dispatcher;
GRANT SELECT ON cope_notifications.public_platform, cope_notifications.catalog, cope_notifications.rating_catalog,
    cope_notifications.public_facts, cope_notifications.eligible_deliveries TO cope_discord_dispatcher;
GRANT SELECT (id, kind, occurred_at, expires_at, projected), UPDATE (projected), DELETE
    ON cope_notifications.facts TO cope_discord_dispatcher;
GRANT SELECT, INSERT, UPDATE, DELETE ON cope_notifications.subscribers, cope_notifications.subscriptions
    TO cope_discord_dispatcher;
GRANT SELECT, INSERT, UPDATE ON cope_notifications.deliveries TO cope_discord_dispatcher;
