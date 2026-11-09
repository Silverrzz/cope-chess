from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from math import log
from typing import Iterable

from cope.rating_bounds import paired_error_margins
from cope.core.models import RoundRobinFormatOptions, TournamentConfig, TournamentFormat
from cope.tournament.round_robin import round_robin_pair_counts

from cope.db.repo import (
    RunnerCommandRecord,
    create_rating_list,
    create_tournament,
    get_rating_list,
    get_tournament,
    list_games,
    list_games_for_tournaments,
    list_tournaments_by_ids,
    update_rating_list_anchor,
    utc_now,
    validate_rating_list_variant,
)


DEFAULT_ELO = 1500.0
ELO_SCALE = 400.0
ELO_LOGISTIC_FACTOR = log(10.0) / ELO_SCALE
RATING_CONVERGENCE = 0.000001
RATING_ITERATIONS = 100000


class RatingCommitError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RatingCommitResult:
    tournament_id: int
    rating_list_id: int
    games_applied: int
    engines_updated: int


@dataclass(frozen=True, slots=True)
class RatingRecalculationResult:
    lists_updated: int
    tournaments_applied: int
    games_applied: int
    engines_updated: int


def create_historical_rating_list(
    connection: sqlite3.Connection, *, name: str, tournament_name: str,
    source_rating_list_id: int, engine_version_ids: list[int], expected_game_count: int,
) -> tuple[int, int, RatingRecalculationResult]:
    source_list = connection.execute(
        "SELECT * FROM rating_lists WHERE id = ? FOR SHARE", (source_rating_list_id,),
    ).fetchone()
    if source_list is None or source_list["monthly_refresh"] or source_list["hardware_scaled"]:
        raise RatingCommitError("Choose a cumulative rating list with fixed clocks")
    if len(set(engine_version_ids)) != len(engine_version_ids) or min(engine_version_ids) <= 0:
        raise RatingCommitError("Provide distinct engine version IDs")
    games = connection.execute(
        """SELECT game.id, game.tournament_id, game.white_engine_id, game.black_engine_id,
                  game.started_at, game.finished_at
           FROM tournament_rating_list_commits source
           JOIN games game ON game.tournament_id = source.tournament_id
           JOIN engine_versions white ON white.id = game.white_engine_id
           JOIN engine_versions black ON black.id = game.black_engine_id
           WHERE source.rating_list_id = ? AND source.status = 'applied'
             AND game.record_eligible = 1 AND game.status = 'finished'
             AND game.result IN ('1-0', '0-1', '1/2-1/2')
             AND white.id = ANY(?) AND black.id = ANY(?) AND white.engine_id <> black.engine_id""",
        (source_rating_list_id, engine_version_ids, engine_version_ids),
    ).fetchall()
    if len(games) != expected_game_count:
        raise RatingCommitError(f"Expected {expected_game_count} historical games, found {len(games)}")
    sources = list_tournaments_by_ids(connection, (game["tournament_id"] for game in games))
    original = sources[0].config
    if any(
        (item.config.variant, item.config.rules_revision, item.config.time_control)
        != (original.variant, original.rules_revision, original.time_control)
        for item in sources
    ):
        raise RatingCommitError("Historical games must share a variant, rules revision, and time control")
    participants = sorted({game[side] for game in games for side in ("white_engine_id", "black_engine_id")})
    config = TournamentConfig.model_validate({
        **original.model_dump(mode="json"),
        "format": "round_robin",
        "format_options": {"cycles": 1},
        "participants": participants,
        "concurrency": 1,
        "opening_suite_id": original.opening_suite_id if all(
            item.config.opening_suite_id == original.opening_suite_id for item in sources
        ) else None,
    })
    rating_list_id = create_rating_list(connection, name, original.variant)
    tournament_id = create_tournament(connection, tournament_name, config, status="finished")
    connection.execute(
        "UPDATE tournaments SET historical = TRUE, started_at = ?, finished_at = ? WHERE id = ?",
        (
            min((game["started_at"] for game in games if game["started_at"]), default=None),
            max((game["finished_at"] for game in games if game["finished_at"]), default=None),
            tournament_id,
        ),
    )
    connection.copy_rows(
        "COPY historical_tournament_games (tournament_id, game_id) FROM STDIN",
        ((tournament_id, game["id"]) for game in games),
    )
    anchor = connection.execute(
        """SELECT engine_id, elo FROM rating_list_ratings WHERE rating_list_id = ? AND engine_id = ANY(?)
           ORDER BY elo DESC, engine_id LIMIT 1""",
        (source_rating_list_id, participants),
    ).fetchone()
    if anchor is not None:
        update_rating_list_anchor(connection, rating_list_id, anchor["engine_id"], anchor["elo"])
    now = utc_now()
    connection.execute(
        """INSERT INTO tournament_rating_list_commits
           (tournament_id, rating_list_id, status, requested_at, applied_at)
           VALUES (?, ?, 'applied', ?, ?)""",
        (tournament_id, rating_list_id, now, now),
    )
    return rating_list_id, tournament_id, recalculate_ratings(connection, rating_list_ids=(rating_list_id,))


def expected_score(
    rating: float,
    opponent_rating: float,
) -> float:
    rating_difference = max(-4000.0, min(4000.0, rating - opponent_rating))
    return 1.0 / (1.0 + 10.0 ** (-rating_difference / ELO_SCALE))


def apply_tournament_rating_commit(
    connection: sqlite3.Connection,
    command: RunnerCommandRecord,
) -> RatingCommitResult:
    tournament_id = _payload_id(command.payload, "tournament_id")
    rating_list_id = _payload_id(command.payload, "rating_list_id")

    commit = connection.execute(
        "SELECT * FROM tournament_rating_list_commits WHERE tournament_id = ? AND rating_list_id = ?",
        (tournament_id, rating_list_id),
    ).fetchone()
    if commit is None:
        raise RatingCommitError("rating commit request no longer exists")
    if commit["command_id"] is None:
        connection.execute(
            f"""
            UPDATE tournament_rating_list_commits
            SET command_id = ?
            WHERE tournament_id = ? AND rating_list_id = ? AND command_id IS NULL AND status = 'pending'
            """,
            (command.id, tournament_id, rating_list_id),
        )
        commit = connection.execute(
            "SELECT * FROM tournament_rating_list_commits WHERE tournament_id = ? AND rating_list_id = ?",
            (tournament_id, rating_list_id),
        ).fetchone()
    if commit is None or commit["command_id"] != command.id:
        raise RatingCommitError("rating commit command has been superseded")
    if commit["rating_list_id"] != rating_list_id:
        raise RatingCommitError("rating commit list does not match its request")
    if commit["status"] not in {"pending", "claimed"}:
        raise RatingCommitError(f"rating commit is already {commit['status']}")

    connection.execute(
        f"""
        UPDATE tournament_rating_list_commits
        SET status = 'claimed', error = NULL
        WHERE tournament_id = ? AND rating_list_id = ? AND command_id = ?
        """,
        (tournament_id, rating_list_id, command.id),
    )

    tournament = get_tournament(connection, tournament_id)
    if tournament is None:
        raise RatingCommitError("tournament no longer exists")
    if tournament.status not in {"finished", "aborted"}:
        raise RatingCommitError("tournament is not finished or aborted")
    if not tournament.config.rated:
        raise RatingCommitError("tournament is not rated")
    try:
        validate_rating_list_variant(connection, rating_list_id, tournament.config)
    except ValueError as exc:
        raise RatingCommitError(str(exc)) from exc
    if connection.execute(
        "SELECT 1 FROM engine_relay_fixtures WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchone() is not None:
        raise RatingCommitError("engine relay tournaments are never eligible for ratings")
    games = _committable_games(tournament, list_games(connection, tournament_id))
    rating_list = get_rating_list(connection, rating_list_id)
    _validate_games(connection, tournament, games, monthly_refresh=rating_list.monthly_refresh)
    applied_at = utc_now()
    cursor = connection.execute(
        f"""
        UPDATE tournament_rating_list_commits
        SET status = 'applied', applied_at = ?, error = NULL
        WHERE tournament_id = ? AND rating_list_id = ? AND command_id = ? AND status = 'claimed'
        """,
        (applied_at, tournament_id, rating_list_id, command.id),
    )
    if cursor.rowcount != 1:
        raise RatingCommitError("rating commit request changed while it was being applied")

    engines = {
        engine_id
        for game in games
        for engine_id in (game.white_engine_id, game.black_engine_id)
    }
    return RatingCommitResult(
        tournament_id=tournament_id,
        rating_list_id=rating_list_id,
        games_applied=len(games),
        engines_updated=len(engines),
    )


def recalculate_ratings(
    connection: sqlite3.Connection,
    *,
    rating_list_ids: Iterable[int] | None = None,
) -> RatingRecalculationResult:
    """Rebuild ratings and history deterministically from applied tournaments."""
    if rating_list_ids is None:
        selected = tuple(
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM rating_lists ORDER BY id"
            )
        )
    else:
        selected = tuple(sorted(set(int(value) for value in rating_list_ids)))
    if not selected:
        return RatingRecalculationResult(0, 0, 0, 0)
    if any(rating_list_id <= 0 for rating_list_id in selected):
        raise RatingCommitError("rating list ids must be positive")

    placeholders = ", ".join("?" for _ in selected)
    anchor_rows = connection.execute(
        f"SELECT id, anchor_engine_id, anchor_elo, monthly_refresh FROM rating_lists WHERE id IN ({placeholders})",
        selected,
    ).fetchall()
    if len(anchor_rows) != len(selected):
        raise RatingCommitError("one or more rating lists do not exist")
    anchors = {
        int(row["id"]): (
            int(row["anchor_engine_id"]) if row["anchor_engine_id"] is not None else None,
            float(row["anchor_elo"]),
        )
        for row in anchor_rows
    }
    monthly_lists = {int(row["id"]) for row in anchor_rows if row["monthly_refresh"]}
    commits = connection.execute(
        f"""
        WITH editions AS (
            SELECT rating_commit.*, list.monthly_refresh,
                   row_number() OVER (
                     PARTITION BY rating_commit.rating_list_id
                     ORDER BY (CASE WHEN list.monthly_refresh THEN tournament.started_at::timestamptz END) DESC NULLS LAST,
                              rating_commit.tournament_id DESC
                   ) AS edition_rank
            FROM tournament_rating_list_commits rating_commit
            JOIN rating_lists list ON list.id = rating_commit.rating_list_id
            JOIN tournaments tournament ON tournament.id = rating_commit.tournament_id
            WHERE rating_commit.status = 'applied' AND rating_commit.rating_list_id IN ({placeholders})
        )
        SELECT * FROM editions WHERE NOT monthly_refresh OR edition_rank = 1
        ORDER BY rating_list_id, COALESCE(applied_at, requested_at), tournament_id
        """,
        selected,
    ).fetchall()

    committed_tournament_ids = tuple(
        dict.fromkeys(int(commit["tournament_id"]) for commit in commits)
    )
    tournaments = {
        tournament.id: tournament
        for tournament in list_tournaments_by_ids(connection, committed_tournament_ids)
    }
    games_by_tournament: dict[int, list[object]] = {
        tournament_id: [] for tournament_id in committed_tournament_ids
    }
    for game in list_games_for_tournaments(connection, committed_tournament_ids):
        games_by_tournament[game.tournament_id].append(game)

    games_by_list: dict[int, dict[int, tuple[int, object, str]]] = {
        list_id: {} for list_id in selected
    }
    games_applied = 0
    applied_at = utc_now()

    for commit in commits:
        tournament_id = int(commit["tournament_id"])
        rating_list_id = int(commit["rating_list_id"])
        tournament = tournaments.get(tournament_id)
        if tournament is None:
            raise RatingCommitError(f"committed tournament {tournament_id} no longer exists")
        try:
            validate_rating_list_variant(connection, rating_list_id, tournament.config)
        except ValueError as exc:
            raise RatingCommitError(str(exc)) from exc
        games = _committable_games(tournament, tuple(games_by_tournament[tournament_id]))
        _validate_games(connection, tournament, games, monthly_refresh=rating_list_id in monthly_lists)
        history_at = commit["applied_at"] or commit["requested_at"] or applied_at
        category_games = games_by_list[rating_list_id]
        previous_count = len(category_games)
        for game in games:
            category_games.setdefault(game.id, (tournament_id, game, history_at))
        games_applied += len(category_games) - previous_count

    ratings: dict[int, dict[int, float]] = {}
    games_played: dict[int, dict[int, int]] = {}
    rating_metrics: dict[int, dict[int, dict[str, float]]] = {}
    rating_errors: dict[int, dict[int, float | None]] = {}
    for rating_list_id in selected:
        anchor_engine_id, anchor_elo = anchors[rating_list_id]
        category_games = games_by_list[rating_list_id].values()
        category_ratings, category_counts = _calculate_ratings_together(
            tuple(game for _, game, _ in category_games),
            anchor_engine_id=anchor_engine_id,
            anchor_elo=anchor_elo,
        )
        ratings[rating_list_id] = category_ratings
        games_played[rating_list_id] = category_counts
        rating_errors[rating_list_id] = paired_error_margins(
            tuple(game for _, game, _ in category_games), category_ratings, anchor_engine_id,
        )
        rated_engine_ids = category_ratings.keys()
        category_metrics: dict[int, dict[str, float]] = {}
        history_rows = []
        for tournament_id, game, history_at in category_games:
            if (
                game.white_engine_id not in rated_engine_ids
                or game.black_engine_id not in rated_engine_ids
            ):
                continue
            white_rating = category_ratings[game.white_engine_id]
            black_rating = category_ratings[game.black_engine_id]
            white_score = _white_score(game.result)
            white_expected = expected_score(white_rating, black_rating)
            white_metrics = category_metrics.setdefault(
                game.white_engine_id,
                {"count": 0.0, "opponent_total": 0.0, "delta_total": 0.0},
            )
            white_metrics["count"] += 1.0
            white_metrics["opponent_total"] += black_rating
            white_metrics["delta_total"] += black_rating - white_rating
            black_metrics = category_metrics.setdefault(
                game.black_engine_id,
                {"count": 0.0, "opponent_total": 0.0, "delta_total": 0.0},
            )
            black_metrics["count"] += 1.0
            black_metrics["opponent_total"] += white_rating
            black_metrics["delta_total"] += white_rating - black_rating
            history_rows.append(
                _history_row(
                    engine_id=game.white_engine_id,
                    opponent_engine_id=game.black_engine_id,
                    rating_list_id=rating_list_id,
                    tournament_id=tournament_id,
                    game_id=game.id,
                    elo_before=white_rating,
                    elo=white_rating,
                    score=white_score,
                    expected_score=white_expected,
                    at=history_at,
                )
            )
            history_rows.append(
                _history_row(
                    engine_id=game.black_engine_id,
                    opponent_engine_id=game.white_engine_id,
                    rating_list_id=rating_list_id,
                    tournament_id=tournament_id,
                    game_id=game.id,
                    elo_before=black_rating,
                    elo=black_rating,
                    score=1.0 - white_score,
                    expected_score=1.0 - white_expected,
                    at=history_at,
                )
            )
        if history_rows:
            _store_rating_history(connection, history_rows)
        rating_metrics[rating_list_id] = category_metrics

    engines_updated = 0
    rating_rows = []
    for rating_list_id, category_ratings in ratings.items():
        for engine_id, elo in category_ratings.items():
            metrics = rating_metrics[rating_list_id].get(engine_id)
            count = metrics["count"] if metrics is not None else 0.0
            rating_rows.append(
                (
                    engine_id,
                    rating_list_id,
                    elo,
                    games_played[rating_list_id][engine_id],
                    rating_errors[rating_list_id].get(engine_id),
                    (
                        round(metrics["opponent_total"] / count, 6)
                        if metrics is not None and count > 0
                        else None
                    ),
                    (
                        round(metrics["delta_total"] / count, 6)
                        if metrics is not None and count > 0
                        else None
                    ),
                    applied_at,
                )
            )
            engines_updated += 1
    if rating_rows:
        connection.executemany(
            """
            INSERT INTO rating_list_ratings (
              engine_id, rating_list_id, elo, games_played,
              error_margin, average_opponent_elo,
              average_opponent_elo_delta, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (engine_id, rating_list_id) DO UPDATE SET
              elo = EXCLUDED.elo,
              games_played = EXCLUDED.games_played,
              error_margin = EXCLUDED.error_margin,
              average_opponent_elo = EXCLUDED.average_opponent_elo,
              average_opponent_elo_delta = EXCLUDED.average_opponent_elo_delta,
              updated_at = EXCLUDED.updated_at
            """,
            rating_rows,
        )
        connection.executemany(
            """
            INSERT INTO engine_elo_history (
              engine_id, rating_list_id, elo, games_played, calculated_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                (engine_id, rating_list_id, elo, games, applied_at)
                for engine_id, rating_list_id, elo, games, *_ in rating_rows
            ),
        )

    for rating_list_id, category_ratings in ratings.items():
        engine_ids = tuple(category_ratings)
        if engine_ids:
            engine_placeholders = ", ".join("?" for _ in engine_ids)
            connection.execute(
                f"""
                DELETE FROM rating_list_ratings
                WHERE rating_list_id = ? AND engine_id NOT IN ({engine_placeholders})
                """,
                (rating_list_id, *engine_ids),
            )
        else:
            connection.execute(
                "DELETE FROM rating_list_ratings WHERE rating_list_id = ?",
                (rating_list_id,),
            )

    return RatingRecalculationResult(
        lists_updated=len(selected),
        tournaments_applied=len(commits),
        games_applied=games_applied,
        engines_updated=engines_updated,
    )


def _store_rating_history(connection: sqlite3.Connection, rows: list[tuple]) -> None:
    columns = (
        "engine_id, rating_list_id, tournament_id, opponent_engine_id, "
        "elo_before, elo, elo_change, score, expected_score, "
        "hardware_score, opponent_hardware_score, game_id, at"
    )
    connection.execute(
        f"""
        CREATE TEMP TABLE IF NOT EXISTS pg_temp.cope_rating_history_stage ON COMMIT DROP
        AS SELECT {columns}, 0::bigint AS row_order
        FROM rating_list_history WITH NO DATA
        """
    )
    connection.execute("TRUNCATE pg_temp.cope_rating_history_stage")
    connection.copy_rows(
        f"COPY pg_temp.cope_rating_history_stage ({columns}, row_order) FROM STDIN",
        ((*row, index) for index, row in enumerate(rows)),
    )
    connection.execute(
        f"""
        INSERT INTO rating_list_history ({columns})
        SELECT {', '.join(f'stage.{column.strip()}' for column in columns.split(','))}
        FROM pg_temp.cope_rating_history_stage stage
        LEFT JOIN rating_list_history existing
          ON existing.rating_list_id = stage.rating_list_id
         AND existing.game_id = stage.game_id
         AND existing.engine_id = stage.engine_id
        WHERE existing.id IS NULL OR (
          existing.tournament_id, existing.opponent_engine_id,
          existing.elo_before, existing.elo, existing.elo_change,
          existing.score, existing.expected_score,
          existing.hardware_score, existing.opponent_hardware_score, existing.at
        ) IS DISTINCT FROM (
          stage.tournament_id, stage.opponent_engine_id,
          stage.elo_before, stage.elo, stage.elo_change,
          stage.score, stage.expected_score,
          stage.hardware_score, stage.opponent_hardware_score, stage.at
        )
        ORDER BY stage.row_order
        ON CONFLICT (rating_list_id, game_id, engine_id) DO UPDATE SET
          tournament_id = EXCLUDED.tournament_id,
          opponent_engine_id = EXCLUDED.opponent_engine_id,
          elo_before = EXCLUDED.elo_before,
          elo = EXCLUDED.elo,
          elo_change = EXCLUDED.elo_change,
          score = EXCLUDED.score,
          expected_score = EXCLUDED.expected_score,
          hardware_score = EXCLUDED.hardware_score,
          opponent_hardware_score = EXCLUDED.opponent_hardware_score,
          at = EXCLUDED.at
        """
    )


def uncommit_tournament_ratings(
    connection: sqlite3.Connection,
    tournament_id: int,
    rating_list_id: int | None = None,
) -> None:
    if rating_list_id is None:
        row = connection.execute(
            "SELECT rating_list_id FROM tournament_rating_list_commits WHERE tournament_id = ? ORDER BY rating_list_id LIMIT 1",
            (tournament_id,),
        ).fetchone()
        if row is None:
            raise RatingCommitError("tournament ratings are not committed")
        rating_list_id = int(row["rating_list_id"])
    commit = connection.execute(
        "SELECT * FROM tournament_rating_list_commits WHERE tournament_id = ? AND rating_list_id = ?",
        (tournament_id, rating_list_id),
    ).fetchone()
    if commit is None or commit["status"] != "applied":
        raise RatingCommitError("tournament ratings are not committed")
    connection.execute(
        "DELETE FROM tournament_rating_list_commits WHERE tournament_id = ? AND rating_list_id = ?",
        (tournament_id, rating_list_id),
    )
    connection.execute(
        "DELETE FROM rating_list_history WHERE tournament_id = ? AND rating_list_id = ?",
        (tournament_id, rating_list_id),
    )


def _committable_games(tournament, games: tuple) -> tuple:
    games = tuple(game for game in games if game.record_eligible)
    if tournament.status == "aborted":
        return tuple(game for game in games if game.status == "finished")
    return games


def _validate_games(connection, tournament, games: tuple, *, monthly_refresh: bool) -> None:
    if not games:
        raise RatingCommitError("tournament has no finished games")
    participants = set(tournament.config.participants)
    for game in games:
        if game.status != "finished" or game.result not in {"1-0", "0-1", "1/2-1/2"}:
            raise RatingCommitError(f"game {game.id} does not have a finished result")
        if game.white_engine_id not in participants or game.black_engine_id not in participants:
            raise RatingCommitError(f"game {game.id} contains a non-participant engine")
    if monthly_refresh:
        if tournament.status != "finished" or tournament.started_at is None or tournament.config.format != TournamentFormat.ROUND_ROBIN:
            raise RatingCommitError("A monthly edition requires a finished round robin")
        pair_counts = Counter((game.white_engine_id, game.black_engine_id) for game in games)
        options = tournament.config.format_options
        if isinstance(options, RoundRobinFormatOptions) and options.games_per_engine is not None:
            expected = round_robin_pair_counts(tournament.config.participants, options.games_per_engine)
            if len(pair_counts) != 2 * len(expected) or any(
                pair_counts[white, black] != count or pair_counts[black, white] != count
                for (white, black), count in expected.items()
            ):
                raise RatingCommitError("A monthly edition must complete its exact schedule for every pairing and colour")
        elif (
            any(white == black for white, black in pair_counts)
            or len(pair_counts) != len(participants) * (len(participants) - 1)
            or len(set(pair_counts.values())) != 1
        ):
            raise RatingCommitError("A monthly edition requires equal game counts for every pairing and colour")
    if tournament.config.clock_reference_hardware_key is not None:
        missing = connection.execute(
            """SELECT game.id FROM games game
               WHERE game.id = ANY(?) AND NOT EXISTS (
                 SELECT 1 FROM game_assignments assignment
                 JOIN game_clock_calibrations calibration
                   ON calibration.game_id = assignment.game_id AND calibration.assignment_key = assignment.assignment_key
                 WHERE assignment.game_id = game.id AND calibration.reference_hardware_key = ?
               ) LIMIT 1""",
            ([game.id for game in games], tournament.config.clock_reference_hardware_key),
        ).fetchone()
        if missing is not None:
            raise RatingCommitError(f"game {missing['id']} has no hardware clock calibration")


def _calculate_ratings_together(
    games: tuple,
    *,
    anchor_engine_id: int | None,
    anchor_elo: float,
) -> tuple[dict[int, float], dict[int, int]]:
    original_engine_ids = {
        engine_id
        for game in games
        for engine_id in (game.white_engine_id, game.black_engine_id)
    }
    if not original_engine_ids:
        return {}, {}
    if anchor_engine_id is not None and anchor_engine_id not in original_engine_ids:
        raise RatingCommitError("the Elo anchor must be a participant in the rated games")

    games = _calculable_games(games)
    engine_ids = sorted(
        {
            engine_id
            for game in games
            for engine_id in (game.white_engine_id, game.black_engine_id)
        }
    )
    if not engine_ids:
        return {}, {}

    engine_indices = {engine_id: index for index, engine_id in enumerate(engine_ids)}
    ratings = [0.0] * len(engine_ids)
    scores = [0.0] * len(engine_ids)
    game_counts = [0] * len(engine_ids)
    pair_counts: dict[tuple[int, int], int] = {}
    for game in games:
        white_index = engine_indices[game.white_engine_id]
        black_index = engine_indices[game.black_engine_id]
        white_score = _white_score(game.result)
        scores[white_index] += white_score
        scores[black_index] += 1.0 - white_score
        game_counts[white_index] += 1
        game_counts[black_index] += 1
        pair = (
            (white_index, black_index)
            if white_index < black_index
            else (black_index, white_index)
        )
        pair_counts[pair] = pair_counts.get(pair, 0) + 1

    effective_anchor_engine_id = (
        anchor_engine_id if anchor_engine_id in engine_indices else None
    )
    fixed_index = (
        engine_indices[effective_anchor_engine_id]
        if effective_anchor_engine_id is not None
        else 0
    )
    adjustable_indices = tuple(
        index for index in range(len(engine_ids)) if index != fixed_index
    )
    pairings = tuple(
        (first_index, second_index, count)
        for (first_index, second_index), count in pair_counts.items()
    )
    for _ in range(RATING_ITERATIONS):
        expected_scores = [0.0] * len(engine_ids)
        information = [0.0] * len(engine_ids)
        for first_index, second_index, count in pairings:
            first_expected = expected_score(
                ratings[first_index],
                ratings[second_index],
            )
            expected_scores[first_index] += count * first_expected
            expected_scores[second_index] += count * (1.0 - first_expected)
            if first_index != second_index:
                pairing_information = count * first_expected * (1.0 - first_expected)
                information[first_index] += pairing_information
                information[second_index] += pairing_information

        residuals = [0.0] * len(engine_ids)
        largest_adjustment = 0.0
        for index in adjustable_indices:
            residual = scores[index] - expected_scores[index]
            residuals[index] = residual
            legacy_adjustment = ELO_SCALE * residual / game_counts[index]
            largest_adjustment = max(largest_adjustment, abs(legacy_adjustment))
        if largest_adjustment < RATING_CONVERGENCE:
            break
        for index in adjustable_indices:
            if information[index] == 0.0:
                continue
            adjustment = (
                0.5
                * residuals[index]
                / (ELO_LOGISTIC_FACTOR * information[index])
            )
            ratings[index] += max(-ELO_SCALE, min(ELO_SCALE, adjustment))
    else:
        raise RatingCommitError("the tournament results did not produce stable Elo ratings")

    if effective_anchor_engine_id is None:
        offset = DEFAULT_ELO - sum(ratings) / len(ratings)
    else:
        offset = anchor_elo - ratings[fixed_index]
    return (
        {
            engine_id: round(ratings[index] + offset, 6)
            for index, engine_id in enumerate(engine_ids)
        },
        {
            engine_id: game_counts[index]
            for index, engine_id in enumerate(engine_ids)
        },
    )


def _calculable_games(games: tuple) -> tuple:
    remaining = games
    while remaining:
        scores: dict[int, float] = {}
        game_counts: dict[int, int] = {}
        for game in remaining:
            white_score = _white_score(game.result)
            scores[game.white_engine_id] = (
                scores.get(game.white_engine_id, 0.0) + white_score
            )
            scores[game.black_engine_id] = (
                scores.get(game.black_engine_id, 0.0) + 1.0 - white_score
            )
            game_counts[game.white_engine_id] = (
                game_counts.get(game.white_engine_id, 0) + 1
            )
            game_counts[game.black_engine_id] = (
                game_counts.get(game.black_engine_id, 0) + 1
            )
        excluded = {
            engine_id
            for engine_id, score in scores.items()
            if score == 0.0 or score == game_counts[engine_id]
        }
        if not excluded:
            return remaining
        remaining = tuple(
            game
            for game in remaining
            if game.white_engine_id not in excluded
            and game.black_engine_id not in excluded
        )
    return ()


def _history_row(
    *,
    engine_id: int,
    opponent_engine_id: int,
    rating_list_id: int,
    tournament_id: int,
    game_id: int,
    elo_before: float,
    elo: float,
    score: float,
    expected_score: float,
    at: str,
) -> tuple:
    return (
        engine_id,
        rating_list_id,
        tournament_id,
        opponent_engine_id,
        elo_before,
        elo,
        round(elo - elo_before, 6),
        score,
        expected_score,
        1.0,
        1.0,
        game_id,
        at,
    )


def _payload_id(payload: dict, field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RatingCommitError(f"rating commit payload has an invalid {field}")
    return value


def _white_score(result: str) -> float:
    if result == "1-0":
        return 1.0
    if result == "0-1":
        return 0.0
    return 0.5
