from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from cope.db import connect_database


CATALOG_KEY = "CASE WHEN version_id = 0 THEN 'f:' || family_id ELSE 'v:' || version_id END"


@dataclass(frozen=True)
class Target:
    family_id: int
    version_id: int
    label: str


@dataclass(frozen=True)
class Batch:
    fact_id: int
    nonce: str
    token: str
    attempts: int
    first_attempt_at: datetime


class Store:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @contextmanager
    def cursor(self):
        with closing(connect_database(self.database_url)) as connection:
            raw = connection.native_connection()
            with raw.transaction(), raw.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE cope_discord_dispatcher")
                cursor.execute("SET LOCAL search_path = pg_catalog, cope_notifications")
                cursor.execute("SET LOCAL statement_timeout = '5s'")
                yield cursor

    def check_schema(self) -> None:
        with self.cursor() as cursor:
            cursor.execute("SELECT 1 FROM eligible_mentions LIMIT 0")

    def catalog_is_public(self) -> bool:
        with self.cursor() as cursor:
            cursor.execute("SELECT enabled FROM public_platform")
            return bool(cursor.fetchone()["enabled"])

    def choices(self, search: str, user_id: int | None = None) -> list[dict]:
        term = search.strip()
        with self.cursor() as cursor:
            if user_id is None:
                cursor.execute(f"""
                    SELECT {CATALOG_KEY} AS key, label
                    FROM catalog
                    WHERE strpos(lower(label), lower(%s)) > 0 OR %s = ''
                    ORDER BY lower(label) <> lower(%s), version_id <> 0, lower(label)
                    LIMIT 25
                """, (term, term, term))
            else:
                cursor.execute(f"""
                    SELECT {CATALOG_KEY} AS key, label
                    FROM (
                        SELECT subscription.family_id, subscription.version_id,
                               COALESCE(catalog.label, 'Engine #' || subscription.family_id ||
                                   CASE WHEN subscription.version_id = 0 THEN '' ELSE ' / version #' ||
                                        subscription.version_id END) AS label
                        FROM subscriptions subscription
                        LEFT JOIN catalog USING (family_id, version_id)
                        WHERE discord_id = %s
                    ) followed
                    WHERE strpos(lower(label), lower(%s)) > 0 OR %s = ''
                    ORDER BY lower(label) <> lower(%s), lower(label)
                    LIMIT 25
                """, (str(user_id), term, term, term))
            return cursor.fetchall()

    def resolve(self, choice: str) -> Target | None:
        value = choice.strip()
        with self.cursor() as cursor:
            cursor.execute(f"""
                SELECT family_id, version_id, label FROM catalog
                WHERE {CATALOG_KEY} = %s OR lower(label) = lower(%s)
                LIMIT 2
            """, (value, value))
            rows = cursor.fetchall()
            return Target(**rows[0]) if len(rows) == 1 else None

    def live_game(self, target: Target) -> dict | None:
        with self.cursor() as cursor:
            cursor.execute("""
                SELECT game_id, tournament_id, tournament_slug, event_slug, title, engine
                FROM live_games WHERE family_id = %s AND (%s = 0 OR version_id = %s)
                ORDER BY started_at DESC, game_id DESC, version_id DESC LIMIT 1
            """, (target.family_id, target.version_id, target.version_id))
            return cursor.fetchone()

    def family_choices(self, search: str) -> list[dict]:
        with self.cursor() as cursor:
            cursor.execute("""
                SELECT 'f:' || family_id AS key, label FROM catalog
                WHERE version_id = 0 AND strpos(lower(label), lower(%s)) > 0
                ORDER BY lower(label) <> lower(%s), lower(label)
                LIMIT 25
            """, (search.strip(), search.strip()))
            return cursor.fetchall()

    def version_choices(self, engine: str, search: str) -> list[dict]:
        family = self.resolve(engine)
        if family is None or family.version_id:
            return []
        with self.cursor() as cursor:
            cursor.execute("""
                SELECT 'v:' || version_id AS key,
                       substr(label, length(%s) + 2) AS label
                FROM catalog
                WHERE family_id = %s AND version_id <> 0
                  AND strpos(lower(substr(label, length(%s) + 2)), lower(%s)) > 0
                ORDER BY version_id DESC
                LIMIT 25
            """, (family.label, family.family_id, family.label, search.strip()))
            return cursor.fetchall()

    def resolve_version(self, engine: str, version: str) -> Target | None:
        family = self.resolve(engine)
        if family is None or family.version_id:
            return None
        with self.cursor() as cursor:
            cursor.execute("""
                SELECT family_id, version_id, label FROM catalog
                WHERE family_id = %s AND version_id <> 0
                  AND ('v:' || version_id = %s OR lower(label) = lower(%s))
                LIMIT 2
            """, (family.family_id, version.strip(), f"{family.label} {version.strip()}"))
            rows = cursor.fetchall()
            return Target(**rows[0]) if len(rows) == 1 else None

    def rating_choices(self, search: str) -> list[dict]:
        with self.cursor() as cursor:
            cursor.execute("""
                SELECT id::text AS key, name AS label FROM rating_catalog
                WHERE strpos(lower(name), lower(%s)) > 0 OR %s = ''
                ORDER BY lower(name) <> lower(%s), lower(name)
                LIMIT 25
            """, (search, search, search))
            return cursor.fetchall()

    def rating_list(self, choice: str) -> dict | None:
        with self.cursor() as cursor:
            cursor.execute("""
                SELECT id, name FROM rating_catalog
                WHERE id::text = %s OR lower(name) = lower(%s)
                LIMIT 2
            """, (choice.strip(), choice.strip()))
            rows = cursor.fetchall()
            return rows[0] if len(rows) == 1 else None

    def subscriber(self, user_id: int) -> dict | None:
        with self.cursor() as cursor:
            cursor.execute(
                """
                SELECT paused, all_tournament_games_since IS NOT NULL AS all_tournament_games
                FROM subscribers WHERE discord_id = %s
                """,
                (str(user_id),),
            )
            return cursor.fetchone()

    def follows(self, user_id: int) -> list[dict]:
        with self.cursor() as cursor:
            cursor.execute("""
                SELECT s.family_id, s.version_id, catalog.label, s.kinds, s.rating_list_id,
                       rating_catalog.name AS rating_list
                FROM subscriptions s
                LEFT JOIN catalog USING (family_id, version_id)
                LEFT JOIN rating_catalog ON rating_catalog.id = s.rating_list_id
                WHERE s.discord_id = %s
                ORDER BY lower(catalog.label), s.family_id, s.version_id
            """, (str(user_id),))
            return cursor.fetchall()

    def save_follow(
        self, user_id: int, target: Target, kinds: list[str],
        rating_list_id: int | None,
    ) -> bool:
        with self.cursor() as cursor:
            cursor.execute("""
                INSERT INTO subscribers (discord_id) VALUES (%s)
                ON CONFLICT DO NOTHING
            """, (str(user_id),))
            cursor.execute("SELECT 1 FROM subscribers WHERE discord_id = %s FOR UPDATE", (str(user_id),))
            cursor.execute("""
                INSERT INTO subscriptions (discord_id, family_id, version_id, kinds, rating_list_id)
                SELECT %s, %s, %s, %s, %s
                WHERE EXISTS (
                    SELECT 1 FROM catalog WHERE family_id = %s AND version_id = %s
                ) AND (
                    %s::bigint IS NULL OR EXISTS (SELECT 1 FROM rating_catalog WHERE id = %s)
                ) AND (
                    EXISTS (
                        SELECT 1 FROM subscriptions WHERE discord_id = %s AND family_id = %s AND version_id = %s
                    ) OR (SELECT count(*) FROM subscriptions WHERE discord_id = %s) < 50
                )
                ON CONFLICT (discord_id, family_id, version_id) DO UPDATE
                SET kinds = EXCLUDED.kinds, rating_list_id = EXCLUDED.rating_list_id,
                    active_since = CASE WHEN (subscriptions.kinds, subscriptions.rating_list_id)
                        IS DISTINCT FROM (EXCLUDED.kinds, EXCLUDED.rating_list_id)
                        THEN clock_timestamp() ELSE subscriptions.active_since END
            """, (
                str(user_id), target.family_id, target.version_id, kinds, rating_list_id,
                target.family_id, target.version_id, rating_list_id, rating_list_id,
                str(user_id), target.family_id, target.version_id, str(user_id),
            ))
            saved = cursor.rowcount == 1
            if not saved:
                self.remove_empty_subscriber(cursor, user_id)
            return saved

    def unfollow(self, user_id: int, engine: str) -> bool:
        prefix, sep, number = engine.strip().partition(":")
        with self.cursor() as cursor:
            cursor.execute("SELECT 1 FROM subscribers WHERE discord_id = %s FOR UPDATE", (str(user_id),))
            if sep and prefix in {"f", "v"} and number.isascii() and number.isdecimal() and len(number) <= 19:
                key = "family_id" if prefix == "f" else "version_id"
                cursor.execute(f"""
                    DELETE FROM subscriptions WHERE discord_id = %s AND {key} = %s
                      AND (%s <> 'f' OR version_id = 0)
                """, (str(user_id), int(number), prefix))
            else:
                cursor.execute("""
                    WITH matched AS (
                        SELECT family_id, version_id
                        FROM subscriptions JOIN catalog USING (family_id, version_id)
                        WHERE discord_id = %s AND lower(catalog.label) = lower(%s)
                    )
                    DELETE FROM subscriptions subscription USING matched
                    WHERE subscription.discord_id = %s AND (SELECT count(*) FROM matched) = 1
                      AND subscription.family_id = matched.family_id AND subscription.version_id = matched.version_id
                """, (str(user_id), engine.strip(), str(user_id)))
            removed = cursor.rowcount > 0
            if removed:
                self.remove_empty_subscriber(cursor, user_id)
            return removed

    def remove_empty_subscriber(self, cursor, user_id: int) -> None:
        cursor.execute("""
            DELETE FROM subscribers subscriber
            WHERE discord_id = %s AND all_tournament_games_since IS NULL AND NOT EXISTS (
                SELECT 1 FROM subscriptions WHERE discord_id = subscriber.discord_id
            )
        """, (str(user_id),))

    def set_all_tournament_games(self, user_id: int, enabled: bool) -> dict | None:
        with self.cursor() as cursor:
            if enabled:
                cursor.execute("""
                    INSERT INTO subscribers (discord_id, all_tournament_games_since)
                    VALUES (%s, clock_timestamp())
                    ON CONFLICT (discord_id) DO UPDATE
                    SET all_tournament_games_since = COALESCE(subscribers.all_tournament_games_since, EXCLUDED.all_tournament_games_since)
                    RETURNING paused
                """, (str(user_id),))
                return cursor.fetchone()
            cursor.execute(
                "UPDATE subscribers SET all_tournament_games_since = NULL WHERE discord_id = %s",
                (str(user_id),),
            )
            self.remove_empty_subscriber(cursor, user_id)
            return None

    def pause(self, user_id: int) -> bool:
        with self.cursor() as cursor:
            cursor.execute("UPDATE subscribers SET paused = true WHERE discord_id = %s", (str(user_id),))
            return cursor.rowcount > 0

    def resume(self, user_id: int) -> None:
        with self.cursor() as cursor:
            cursor.execute("""
                UPDATE subscribers SET paused = false, active_since = clock_timestamp()
                WHERE discord_id = %s AND paused
            """, (str(user_id),))

    def project(self) -> None:
        with self.cursor() as cursor:
            cursor.execute("""
                WITH pending AS MATERIALIZED (
                    SELECT id FROM facts
                    WHERE NOT projected AND occurred_at <= clock_timestamp() AND expires_at > clock_timestamp()
                    ORDER BY occurred_at, id
                    LIMIT 250
                    FOR UPDATE SKIP LOCKED
                ), queued AS (
                    INSERT INTO deliveries (discord_id, fact_id, version_id)
                    SELECT delivery.discord_id, pending.id, delivery.version_id
                    FROM pending JOIN eligible_mentions delivery ON delivery.fact_id = pending.id
                    ON CONFLICT DO NOTHING
                )
                UPDATE facts SET projected = true FROM pending WHERE facts.id = pending.id
            """)

    def prune(self) -> None:
        with self.cursor() as cursor:
            cursor.execute("""
                DELETE FROM facts WHERE id IN (
                    SELECT id FROM facts WHERE expires_at < clock_timestamp() - interval '1 day'
                    ORDER BY expires_at LIMIT 1000
                )
            """)

    def claim(self, channel_id: int) -> Batch | None:
        with self.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(684392719406)")
            cursor.execute("""
                SELECT fact_id, version_id, nonce FROM deliveries
                WHERE outcome IS NULL AND ready_at <= clock_timestamp()
                  AND EXISTS (SELECT 1 FROM discord_destination WHERE channel_id = %s)
                ORDER BY ready_at, fact_id, version_id
                LIMIT 1
            """, (str(channel_id),))
            row = cursor.fetchone()
            if row is None:
                return None

            nonce = row["nonce"] or uuid4().hex[:24]
            token = str(uuid4())
            cursor.execute("""
                WITH candidates AS (
                    SELECT discord_id FROM deliveries
                    WHERE fact_id = %s AND version_id = %s AND outcome IS NULL
                      AND ready_at <= clock_timestamp() AND nonce IS NOT DISTINCT FROM %s::text
                    ORDER BY discord_id LIMIT 35
                )
                UPDATE deliveries delivery
                SET nonce = %s, claim_token = %s, attempts = attempts + 1,
                    first_attempt_at = COALESCE(first_attempt_at, clock_timestamp()),
                    ready_at = clock_timestamp() + interval '60 seconds'
                FROM candidates WHERE delivery.discord_id = candidates.discord_id AND delivery.fact_id = %s
                RETURNING delivery.attempts, delivery.first_attempt_at
            """, (
                row["fact_id"], row["version_id"], row["nonce"], nonce, token, row["fact_id"],
            ))
            claimed = cursor.fetchall()
            if not claimed:
                return None
            return Batch(
                row["fact_id"], nonce, token,
                max(item["attempts"] for item in claimed),
                min(item["first_attempt_at"] for item in claimed),
            )

    def eligible(self, batch: Batch, channel_id: int) -> dict | None:
        with self.cursor() as cursor:
            cursor.execute("""
                SELECT fact.*, matching.discord_ids, matching.engine FROM public_facts fact
                JOIN LATERAL (
                    SELECT array_agg(eligible.discord_id ORDER BY eligible.discord_id) AS discord_ids,
                           min(eligible.engine) AS engine
                    FROM eligible_mentions eligible
                    JOIN deliveries delivery USING (discord_id, fact_id, version_id)
                    WHERE eligible.fact_id = fact.id AND delivery.claim_token = %s
                    HAVING count(*) > 0
                ) matching ON true
                WHERE fact.id = %s AND EXISTS (SELECT 1 FROM discord_destination WHERE channel_id = %s)
            """, (batch.token, batch.fact_id, str(channel_id)))
            return cursor.fetchone()

    def finish(
        self, batch: Batch, eligible_ids: list[str], outcome: str,
        message_id: str | None = None, retry_after: float = 0,
    ) -> None:
        with self.cursor() as cursor:
            cursor.execute("""
                UPDATE deliveries
                SET outcome = CASE WHEN discord_id <> ALL(%s::text[]) THEN 'suppressed' ELSE %s END,
                    message_id = CASE WHEN discord_id = ANY(%s::text[]) THEN %s ELSE NULL END,
                    ready_at = clock_timestamp() + (%s * interval '1 second'), claim_token = NULL
                WHERE claim_token = %s AND fact_id = %s
            """, (
                eligible_ids, None if retry_after else outcome, eligible_ids, message_id,
                retry_after, batch.token, batch.fact_id,
            ))
