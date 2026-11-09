from __future__ import annotations


def record_game_donor(connection, game_id: int, threads: int | None) -> None:
    worker = connection.execute(
        """
        SELECT worker.id, worker.label
        FROM game_assignments assignment
        JOIN workers worker ON worker.id = assignment.worker_id
        WHERE assignment.game_id = ?
        """,
        (game_id,),
    ).fetchone()
    if worker is None:
        return
    connection.execute(
        """
        INSERT INTO hardware_donor_workers (worker_id, worker_label)
        VALUES (?, ?) ON CONFLICT (worker_id) DO NOTHING
        """,
        (worker["id"], worker["label"]),
    )
    connection.execute(
        """
        INSERT INTO hardware_donor_games (game_id, worker_id, thread_count)
        VALUES (?, ?, ?)
        ON CONFLICT (game_id) DO UPDATE SET
            worker_id = EXCLUDED.worker_id, thread_count = EXCLUDED.thread_count
        """,
        (game_id, worker["id"], threads),
    )


def preserve_worker_donor_history(connection, worker_id: int) -> None:
    connection.execute(
        """
        INSERT INTO hardware_donor_workers (worker_id, worker_label)
        SELECT id, label FROM workers WHERE id = ?
        ON CONFLICT (worker_id) DO UPDATE SET worker_label = EXCLUDED.worker_label
        """,
        (worker_id,),
    )
    connection.execute(
        """
        INSERT INTO hardware_donor_games (game_id, worker_id)
        SELECT game.id, assignment.worker_id
        FROM game_assignments assignment
        JOIN games game ON game.id = assignment.game_id
        WHERE assignment.worker_id = ? AND game.status = 'finished' AND game.result IS NOT NULL
        ON CONFLICT (game_id) DO NOTHING
        """,
        (worker_id,),
    )


def list_donor_workers(connection) -> list[dict]:
    return connection.execute(
        """
        SELECT COALESCE(worker.id, profile.worker_id) AS id,
               COALESCE(worker.label, profile.worker_label) AS label,
               profile.donor_name, worker.id IS NULL AS retired
        FROM workers worker
        FULL JOIN hardware_donor_workers profile ON profile.worker_id = worker.id
        ORDER BY retired, lower(COALESCE(worker.label, profile.worker_label)), id
        """
    ).fetchall()


def set_donor_group(connection, worker_ids: list[int], name: str | None) -> bool:
    updated = connection.execute(
        """
        INSERT INTO hardware_donor_workers (worker_id, worker_label, donor_name)
        SELECT COALESCE(worker.id, profile.worker_id),
               COALESCE(worker.label, profile.worker_label), ?
        FROM workers worker
        FULL JOIN hardware_donor_workers profile ON profile.worker_id = worker.id
        WHERE COALESCE(worker.id, profile.worker_id) = ANY(CAST(? AS BIGINT[]))
        ORDER BY COALESCE(worker.id, profile.worker_id)
        ON CONFLICT (worker_id) DO UPDATE SET
            worker_label = EXCLUDED.worker_label, donor_name = EXCLUDED.donor_name
        """,
        (name, worker_ids),
    ).rowcount
    return updated == len(worker_ids)


def donor_leaderboard(connection) -> dict:
    rows = connection.execute(
        """
        WITH tournament_threads AS MATERIALIZED (
            SELECT id, COALESCE((config::jsonb ->> 'engine_threads')::integer, 1) AS threads
            FROM tournaments
        ), historical_policy AS MATERIALIZED (
            SELECT profile.worker_id AS fallback_worker, migration.applied_at AS cutoff
            FROM hardware_donor_workers profile
            JOIN application_migrations migration ON migration.version = '0020_historical_donor_attribution.sql'
            WHERE profile.historical_anchor = 'ellie'
        ), styx_hardware AS MATERIALIZED (
            SELECT worker_id, historical_styx_cpu AS cpu
            FROM hardware_donor_workers
            WHERE historical_anchor = 'styx'
        ), worker_totals AS (
            SELECT CASE
                       WHEN policy.fallback_worker IS NULL THEN recorded.worker_id
                       WHEN matched.worker_id IS NOT NULL THEN
                           CASE WHEN profile.historical_styx_cpu = matched.cpu THEN recorded.worker_id
                                ELSE matched.worker_id END
                       WHEN profile.historical_styx_cpu IS NOT NULL THEN policy.fallback_worker
                       ELSE COALESCE(recorded.worker_id, policy.fallback_worker)
                   END AS worker_id,
                   COUNT(*) AS games,
                   COUNT(runtime.seconds) AS timed_games,
                   SUM(runtime.seconds * COALESCE(credit.thread_count, tournament.threads)) / 3600 AS cpu_hours
            FROM games game
            JOIN tournament_threads tournament ON tournament.id = game.tournament_id
            LEFT JOIN game_assignments assignment ON assignment.game_id = game.id
            LEFT JOIN hardware_donor_games credit ON credit.game_id = game.id
            CROSS JOIN LATERAL (SELECT COALESCE(credit.worker_id, assignment.worker_id) AS worker_id) recorded
            LEFT JOIN hardware_donor_workers profile ON profile.worker_id = recorded.worker_id
            CROSS JOIN LATERAL (
                SELECT GREATEST(
                    CASE WHEN pg_input_is_valid(game.started_at, 'timestamptz')
                         THEN game.started_at::timestamptz END,
                    CASE WHEN pg_input_is_valid(assignment.acked_at, 'timestamptz')
                         THEN assignment.acked_at::timestamptz END
                ) AS started_at,
                CASE WHEN pg_input_is_valid(game.finished_at, 'timestamptz')
                     THEN game.finished_at::timestamptz END AS finished_at
            ) timing
            LEFT JOIN historical_policy policy ON timing.finished_at IS NULL OR timing.finished_at <= policy.cutoff
            LEFT JOIN LATERAL (
                SELECT candidate.worker_id, candidate.cpu
                FROM styx_hardware candidate
                WHERE policy.fallback_worker IS NOT NULL AND candidate.cpu = COALESCE(
                    (SELECT min(lower(btrim(hardware.hw::jsonb ->> 'cpu_model')))
                     FROM game_clock_calibrations calibration
                     JOIN benchmark_hardware hardware ON hardware.hardware_key = calibration.worker_hardware_key
                     WHERE calibration.game_id = game.id
                       AND (assignment.assignment_key IS NULL OR calibration.assignment_key = assignment.assignment_key)
                     HAVING COUNT(DISTINCT lower(btrim(hardware.hw::jsonb ->> 'cpu_model'))) = 1),
                    NULLIF(lower(btrim(CASE WHEN pg_input_is_valid(game.white_hw, 'jsonb')
                        THEN game.white_hw::jsonb ->> 'cpu_model' ELSE game.white_hw END)), ''),
                    NULLIF(lower(btrim(CASE WHEN pg_input_is_valid(game.black_hw, 'jsonb')
                        THEN game.black_hw::jsonb ->> 'cpu_model' ELSE game.black_hw END)), ''),
                    profile.historical_styx_cpu
                )
                ORDER BY candidate.worker_id
                LIMIT 1
            ) matched ON TRUE
            CROSS JOIN LATERAL (
                SELECT CASE WHEN isfinite(timing.started_at) AND isfinite(timing.finished_at)
                                 AND timing.finished_at >= timing.started_at
                       THEN EXTRACT(EPOCH FROM timing.finished_at - timing.started_at)
                       END AS seconds
            ) runtime
            WHERE game.status = 'finished' AND game.result IS NOT NULL
            GROUP BY 1
        ), named_totals AS (
            SELECT CASE
                       WHEN totals.worker_id IS NULL THEN NULL
                       WHEN profile.donor_name IS NOT NULL THEN 'donor:' || lower(profile.donor_name)
                       ELSE 'worker:' || totals.worker_id
                   END AS donor_key,
                   COALESCE(profile.donor_name, NULLIF(btrim(worker.label), ''),
                            NULLIF(btrim(profile.worker_label), ''), 'Worker #' || totals.worker_id) AS name,
                   totals.games, totals.timed_games, totals.cpu_hours
            FROM worker_totals totals
            LEFT JOIN hardware_donor_workers profile ON profile.worker_id = totals.worker_id
            LEFT JOIN workers worker ON worker.id = totals.worker_id
        )
        SELECT donor_key, MIN(name) AS name, SUM(games)::bigint AS games,
               SUM(timed_games)::bigint AS timed_games, SUM(cpu_hours)::double precision AS cpu_hours
        FROM named_totals
        GROUP BY donor_key
        ORDER BY cpu_hours DESC NULLS LAST, games DESC, lower(MIN(name)), donor_key
        """
    ).fetchall()
    return {
        "donors": [row for row in rows if row["donor_key"] is not None],
        "completed_games": sum(row["games"] for row in rows),
        "unattributed_games": sum(row["games"] for row in rows if row["donor_key"] is None),
    }
