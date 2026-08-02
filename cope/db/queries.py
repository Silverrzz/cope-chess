from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass

from cope.core.models import EngineSpec, HardwareInfo

from .repo import (
    GameRecord,
    TournamentRecord,
    _engine_from_row,
    _game_from_row,
    list_tournaments,
)


@dataclass(frozen=True, slots=True)
class OpeningPositionRecord:
    name: str
    start_fen: str
    moves: tuple[str, ...]
    fen: str


@dataclass(frozen=True, slots=True)
class RatingRowRecord:
    engine: EngineSpec
    elo: float
    error_margin: float | None
    games_played: int
    average_opponent_elo: float | None
    average_opponent_elo_delta: float | None
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class WorkerActivityRecord:
    assignment_status: str
    game_id: int
    round: int
    white_engine_id: int
    black_engine_id: int
    tournament_id: int
    tournament_name: str
    plies: int
    active_assignment_count: int
    progress_stage: str | None
    progress_detail: str | None


_DB_STAT_TABLES = (
    "rating_lists",
    "engines",
    "tournaments",
    "games",
    "workers",
    "opening_suites",
)


def get_engine_name(connection: sqlite3.Connection, engine_id: int) -> str:
    row = connection.execute(
        """SELECT engine.name, version.version
           FROM engine_versions version JOIN engines engine ON engine.id = version.engine_id
           WHERE version.id = ?""",
        (engine_id,),
    ).fetchone()
    if row is None:
        return f"Engine {engine_id}"
    return " ".join(part for part in (row["name"], row["version"]) if part)


def get_opening_position(
    connection: sqlite3.Connection,
    opening_id: int | None,
) -> OpeningPositionRecord | None:
    if opening_id is None:
        return None

    row = connection.execute(
        "SELECT name, start_fen, moves, fen FROM openings WHERE id = ?",
        (opening_id,),
    ).fetchone()
    if row is None:
        return None
    return OpeningPositionRecord(
        name=row["name"] or "Opening",
        start_fen=row["start_fen"],
        moves=tuple(json.loads(row["moves"])),
        fen=row["fen"],
    )


def list_active_games(
    connection: sqlite3.Connection,
    *,
    tournament_id: int | None = None,
    limit: int | None = None,
) -> tuple[GameRecord, ...]:
    conditions = "status IN ('live', 'assigned')"
    parameters: list[int] = []
    if tournament_id is not None:
        conditions += " AND tournament_id = ?"
        parameters.append(tournament_id)
    limit_sql = ""
    if limit is not None:
        limit_sql = " LIMIT ?"
        parameters.append(limit)
    rows = connection.execute(
        f"""
        SELECT * FROM games
        WHERE {conditions}
        ORDER BY CASE status WHEN 'live' THEN 0 ELSE 1 END, id DESC
        {limit_sql}
        """,
        tuple(parameters),
    )
    games = tuple(_game_from_row(row) for row in rows)
    return tuple(
        sorted(
            games,
            key=lambda game: (
                game.tournament_id,
                min(game.white_engine_id, game.black_engine_id),
                max(game.white_engine_id, game.black_engine_id),
                game.opening_id if game.opening_id is not None else -1,
                game.match_id if game.match_id is not None else -1,
                (game.game_number - 1) // 2,
                game.tiebreak_kind or "",
                game.game_number,
                game.id,
            ),
        )
    )


def list_upcoming_games(
    connection: sqlite3.Connection,
    *,
    limit: int,
) -> tuple[GameRecord, ...]:
    rows = connection.execute(
        """
        SELECT * FROM games
        WHERE status = 'pending'
        ORDER BY id ASC
        LIMIT ?
        """,
        (limit,),
    )
    return tuple(_game_from_row(row) for row in rows)


def list_games_by_status(
    connection: sqlite3.Connection,
    status: str,
    *,
    limit: int,
) -> tuple[GameRecord, ...]:
    rows = connection.execute(
        """
        SELECT * FROM games
        WHERE status = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (status, limit),
    )
    return tuple(_game_from_row(row) for row in rows)


def list_engine_games(
    connection: sqlite3.Connection,
    engine_id: int,
    *,
    limit: int = 50,
) -> tuple[GameRecord, ...]:
    rows = connection.execute(
        """
        SELECT * FROM games
        WHERE result IS NOT NULL
          AND (white_engine_id = ? OR black_engine_id = ?)
        ORDER BY id DESC
        LIMIT ?
        """,
        (engine_id, engine_id, limit),
    )
    return tuple(_game_from_row(row) for row in rows)


def engine_result_summary(
    connection: sqlite3.Connection,
    engine_id: int,
) -> dict[str, int]:
    row = connection.execute(
        """
        SELECT
          COUNT(*) AS games,
          COALESCE(SUM(CASE WHEN result = '1/2-1/2' THEN 1 ELSE 0 END), 0) AS draws,
          COALESCE(SUM(CASE
            WHEN result = '1-0' AND white_engine_id = ? THEN 1
            WHEN result = '0-1' AND black_engine_id = ? THEN 1
            ELSE 0
          END), 0) AS wins,
          COALESCE(SUM(CASE
            WHEN result = '0-1' AND white_engine_id = ? THEN 1
            WHEN result = '1-0' AND black_engine_id = ? THEN 1
            ELSE 0
          END), 0) AS losses
        FROM games
        WHERE result IS NOT NULL
          AND (white_engine_id = ? OR black_engine_id = ?)
        """,
        (engine_id, engine_id, engine_id, engine_id, engine_id, engine_id),
    ).fetchone()
    return {
        "wins": int(row["wins"]),
        "draws": int(row["draws"]),
        "losses": int(row["losses"]),
        "games": int(row["games"]),
    }


def list_rating_rows(
    connection: sqlite3.Connection,
    rating_list_id: int,
) -> tuple[RatingRowRecord, ...]:
    if not _table_exists(connection, "rating_list_ratings"):
        return ()

    rows = connection.execute(
        """
        SELECT version.*, engine.name, engine.author, engine.active AS engine_active,
               ratings.elo, ratings.games_played, ratings.updated_at
        FROM rating_list_ratings ratings
        JOIN engine_versions version ON version.id = ratings.engine_id
        JOIN engines engine ON engine.id = version.engine_id
        WHERE ratings.rating_list_id = ?
        ORDER BY ratings.elo DESC, engine.name, version.version
        """,
        (rating_list_id,),
    )
    history = connection.execute(
        """
        SELECT
          rating_history.engine_id,
          rating_history.elo_before AS engine_elo,
          rating_history.expected_score,
          opponent.elo_before AS opponent_elo
        FROM rating_list_history rating_history
        JOIN rating_list_history AS opponent
          ON opponent.game_id = rating_history.game_id
         AND opponent.rating_list_id = rating_history.rating_list_id
         AND opponent.engine_id = rating_history.opponent_engine_id
        WHERE rating_history.rating_list_id = ?
        """,
        (rating_list_id,),
    )
    metrics: dict[int, dict[str, float]] = {}
    for item in history:
        if item["engine_elo"] is None or item["opponent_elo"] is None:
            continue
        values = metrics.setdefault(
            item["engine_id"],
            {"count": 0.0, "opponent_total": 0.0, "delta_total": 0.0, "information": 0.0},
        )
        engine_elo = float(item["engine_elo"])
        opponent_elo = float(item["opponent_elo"])
        expected = (
            float(item["expected_score"])
            if item["expected_score"] is not None
            else 1.0 / (
                1.0
                + 10.0
                ** (max(-4000.0, min(4000.0, opponent_elo - engine_elo)) / 400.0)
            )
        )
        values["count"] += 1
        values["opponent_total"] += opponent_elo
        values["delta_total"] += opponent_elo - engine_elo
        values["information"] += expected * (1.0 - expected)

    return tuple(
        RatingRowRecord(
            engine=_engine_from_row(row),
            elo=row["elo"],
            error_margin=_rating_error_margin(metrics.get(row["id"])),
            games_played=row["games_played"],
            average_opponent_elo=_rating_metric_average(
                metrics.get(row["id"]),
                "opponent_total",
            ),
            average_opponent_elo_delta=_rating_metric_average(
                metrics.get(row["id"]),
                "delta_total",
            ),
            updated_at=row["updated_at"],
        )
        for row in rows
    )


def _rating_metric_average(
    values: dict[str, float] | None,
    field: str,
) -> float | None:
    if not values or values["count"] <= 0:
        return None
    return round(values[field] / values["count"], 6)


def _rating_error_margin(values: dict[str, float] | None) -> float | None:
    if not values or values["information"] <= 0:
        return None
    standard_error = (400.0 / math.log(10.0)) / math.sqrt(values["information"])
    return round(1.96 * standard_error, 6)


def get_worker_activity(
    connection: sqlite3.Connection,
    worker_id: int,
) -> WorkerActivityRecord | None:
    row = connection.execute(
        """
        SELECT
          game_assignments.status AS assignment_status,
          games.id AS game_id,
          games.round,
          games.white_engine_id,
          games.black_engine_id,
          tournaments.id AS tournament_id,
          tournaments.name AS tournament_name,
          (SELECT COUNT(*) FROM moves WHERE moves.game_id = games.id) AS plies,
          (
            SELECT stage_label FROM game_assignment_progress
            WHERE assignment_id = game_assignments.id
              AND assignment_key = game_assignments.assignment_key
            ORDER BY id DESC LIMIT 1
          ) AS progress_stage,
          (
            SELECT detail FROM game_assignment_progress
            WHERE assignment_id = game_assignments.id
              AND assignment_key = game_assignments.assignment_key
            ORDER BY id DESC LIMIT 1
          ) AS progress_detail,
          COUNT(*) OVER () AS active_assignment_count
        FROM game_assignments
        JOIN games ON games.id = game_assignments.game_id
        JOIN tournaments ON tournaments.id = games.tournament_id
        WHERE game_assignments.worker_id = ?
          AND game_assignments.status IN ('assigned', 'acked', 'live')
          AND games.status IN ('assigned', 'live', 'finished')
        ORDER BY game_assignments.sent_at DESC, game_assignments.id DESC
        LIMIT 1
        """,
        (worker_id,),
    ).fetchone()
    if row is None:
        return None

    return WorkerActivityRecord(
        assignment_status=row["assignment_status"],
        game_id=row["game_id"],
        round=row["round"],
        white_engine_id=row["white_engine_id"],
        black_engine_id=row["black_engine_id"],
        tournament_id=row["tournament_id"],
        tournament_name=row["tournament_name"],
        plies=row["plies"],
        active_assignment_count=row["active_assignment_count"],
        progress_stage=row["progress_stage"],
        progress_detail=row["progress_detail"],
    )


def list_worker_activities(
    connection: sqlite3.Connection,
) -> dict[int, WorkerActivityRecord]:
    rows = connection.execute(
        """
        SELECT
          game_assignments.worker_id,
          game_assignments.status AS assignment_status,
          games.id AS game_id,
          games.round,
          games.white_engine_id,
          games.black_engine_id,
          tournaments.id AS tournament_id,
          tournaments.name AS tournament_name,
          (SELECT COUNT(*) FROM moves WHERE moves.game_id = games.id) AS plies,
          (
            SELECT stage_label FROM game_assignment_progress
            WHERE assignment_id = game_assignments.id
              AND assignment_key = game_assignments.assignment_key
            ORDER BY id DESC LIMIT 1
          ) AS progress_stage,
          (
            SELECT detail FROM game_assignment_progress
            WHERE assignment_id = game_assignments.id
              AND assignment_key = game_assignments.assignment_key
            ORDER BY id DESC LIMIT 1
          ) AS progress_detail,
          COUNT(*) OVER (
            PARTITION BY game_assignments.worker_id
          ) AS active_assignment_count
        FROM game_assignments
        JOIN games ON games.id = game_assignments.game_id
        JOIN tournaments ON tournaments.id = games.tournament_id
        WHERE game_assignments.worker_id IS NOT NULL
          AND game_assignments.status IN ('assigned', 'acked', 'live')
          AND games.status IN ('assigned', 'live', 'finished')
        ORDER BY game_assignments.worker_id, game_assignments.sent_at DESC,
                 game_assignments.id DESC
        """
    )
    activities: dict[int, WorkerActivityRecord] = {}
    for row in rows:
        worker_id = int(row["worker_id"])
        if worker_id in activities:
            continue
        activities[worker_id] = WorkerActivityRecord(
            assignment_status=row["assignment_status"],
            game_id=row["game_id"],
            round=row["round"],
            white_engine_id=row["white_engine_id"],
            black_engine_id=row["black_engine_id"],
            tournament_id=row["tournament_id"],
            tournament_name=row["tournament_name"],
            plies=row["plies"],
            active_assignment_count=row["active_assignment_count"],
            progress_stage=row["progress_stage"],
            progress_detail=row["progress_detail"],
        )
    return activities


def active_engine_hardware_profiles(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> dict[int, tuple[HardwareInfo, ...]]:
    rows = connection.execute(
        """
        SELECT
          games.white_engine_id,
          games.black_engine_id,
          workers.hw AS worker_hw
        FROM game_assignments
        JOIN games ON games.id = game_assignments.game_id
        LEFT JOIN workers
          ON workers.id = game_assignments.worker_id
          AND workers.status IN ('connected', 'downloading', 'ready', 'busy')
        WHERE games.tournament_id = ?
          AND games.status IN ('assigned', 'live')
          AND game_assignments.status IN ('assigned', 'acked', 'live')
        """,
        (tournament_id,),
    )

    hardware_by_engine: dict[int, list[HardwareInfo]] = {}
    seen: dict[int, set[str]] = {}
    for row in rows:
        worker_hw = _hardware_from_json(row["worker_hw"])

        for engine_id, hw in (
            (row["white_engine_id"], worker_hw),
            (row["black_engine_id"], worker_hw),
        ):
            if hw is None:
                continue
            profile_key = hw.model_dump_json()
            engine_seen = seen.setdefault(engine_id, set())
            if profile_key in engine_seen:
                continue
            engine_seen.add(profile_key)
            hardware_by_engine.setdefault(engine_id, []).append(hw)

    return {
        engine_id: tuple(profiles)
        for engine_id, profiles in hardware_by_engine.items()
    }


def database_stats(connection: sqlite3.Connection) -> dict[str, int]:
    return {table_name: _count_rows(connection, table_name) for table_name in _DB_STAT_TABLES}


def list_uncommitted_finished_tournaments(
    connection: sqlite3.Connection,
) -> tuple[TournamentRecord, ...]:
    active_or_applied_ids = {
        row["tournament_id"]
        for row in connection.execute(
            """
            SELECT tournament_id FROM tournament_rating_list_commits
            WHERE status IN ('pending', 'claimed', 'applied')
            """
        )
    }
    return tuple(
        tournament
        for tournament in list_tournaments(connection)
        if tournament.status in {"finished", "aborted"}
        and tournament.config.rated
        and (
            tournament.status == "finished"
            or connection.execute(
                """
                SELECT 1 FROM games
                WHERE tournament_id = ? AND status = 'finished' AND result IS NOT NULL
                LIMIT 1
                """,
                (tournament.id,),
            ).fetchone()
            is not None
        )
        and tournament.id not in active_or_applied_ids
    )


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _count_rows(connection: sqlite3.Connection, table_name: str) -> int:
    if not _table_exists(connection, table_name):
        return 0
    row = connection.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
    return int(row["count"])


def _hardware_from_json(value: str | None) -> HardwareInfo | None:
    if value is None:
        return None
    try:
        return HardwareInfo.model_validate_json(value)
    except ValueError:
        return None
