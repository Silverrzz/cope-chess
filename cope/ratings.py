from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable

from cope.db.repo import RunnerCommandRecord, get_tournament, list_games, utc_now


DEFAULT_ELO = 1500.0
ELO_SCALE = 400.0
RATING_CONVERGENCE = 0.000001
RATING_ITERATIONS = 10000


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
    games = _committable_games(tournament, list_games(connection, tournament_id))
    _validate_games(tournament, games)
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
        f"SELECT id, anchor_engine_id, anchor_elo FROM rating_lists WHERE id IN ({placeholders})",
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
    connection.execute(
        f"DELETE FROM rating_list_history WHERE rating_list_id IN ({placeholders})",
        selected,
    )
    connection.execute(
        f"DELETE FROM rating_list_ratings WHERE rating_list_id IN ({placeholders})",
        selected,
    )
    commits = connection.execute(
        f"""
        SELECT * FROM tournament_rating_list_commits
        WHERE status = 'applied' AND rating_list_id IN ({placeholders})
        ORDER BY rating_list_id, COALESCE(applied_at, requested_at), tournament_id
        """,
        selected,
    ).fetchall()

    games_by_list: dict[int, list[tuple[int, object, str]]] = {
        list_id: [] for list_id in selected
    }
    games_applied = 0
    applied_at = utc_now()

    for commit in commits:
        tournament_id = int(commit["tournament_id"])
        rating_list_id = int(commit["rating_list_id"])
        tournament = get_tournament(connection, tournament_id)
        if tournament is None:
            raise RatingCommitError(f"committed tournament {tournament_id} no longer exists")
        games = _committable_games(tournament, list_games(connection, tournament_id))
        _validate_games(tournament, games)
        history_at = commit["applied_at"] or commit["requested_at"] or applied_at
        games_by_list[rating_list_id].extend(
            (tournament_id, game, history_at) for game in games
        )
        games_applied += len(games)

    ratings: dict[int, dict[int, float]] = {}
    games_played: dict[int, dict[int, int]] = {}
    for rating_list_id in selected:
        anchor_engine_id, anchor_elo = anchors[rating_list_id]
        category_games = games_by_list[rating_list_id]
        category_ratings, category_counts = _calculate_ratings_together(
            tuple(game for _, game, _ in category_games),
            anchor_engine_id=anchor_engine_id,
            anchor_elo=anchor_elo,
        )
        ratings[rating_list_id] = category_ratings
        games_played[rating_list_id] = category_counts
        for tournament_id, game, history_at in category_games:
            white_rating = category_ratings[game.white_engine_id]
            black_rating = category_ratings[game.black_engine_id]
            white_score = _white_score(game.result)
            white_expected = expected_score(white_rating, black_rating)
            _record_history(
                connection,
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
            _record_history(
                connection,
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

    engines_updated = 0
    for rating_list_id, category_ratings in ratings.items():
        for engine_id, elo in category_ratings.items():
            connection.execute(
                f"""
                INSERT INTO rating_list_ratings (engine_id, rating_list_id, elo, games_played, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (engine_id, rating_list_id, elo, games_played[rating_list_id][engine_id], applied_at),
            )
            engines_updated += 1

    return RatingRecalculationResult(
        lists_updated=len(selected),
        tournaments_applied=len(commits),
        games_applied=games_applied,
        engines_updated=engines_updated,
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
    if tournament.status == "aborted":
        return tuple(game for game in games if game.status == "finished")
    return games


def _validate_games(tournament, games: tuple) -> None:
    if not games:
        raise RatingCommitError("tournament has no finished games")
    participants = set(tournament.config.participants)
    for game in games:
        if game.status != "finished" or game.result not in {"1-0", "0-1", "1/2-1/2"}:
            raise RatingCommitError(f"game {game.id} does not have a finished result")
        if game.white_engine_id not in participants or game.black_engine_id not in participants:
            raise RatingCommitError(f"game {game.id} contains a non-participant engine")


def _calculate_ratings_together(
    games: tuple,
    *,
    anchor_engine_id: int | None,
    anchor_elo: float,
) -> tuple[dict[int, float], dict[int, int]]:
    engine_ids = {
        engine_id
        for game in games
        for engine_id in (game.white_engine_id, game.black_engine_id)
    }
    if not engine_ids:
        return {}, {}
    if anchor_engine_id is not None and anchor_engine_id not in engine_ids:
        raise RatingCommitError("the Elo anchor must be a participant in the rated games")

    ratings = {engine_id: 0.0 for engine_id in engine_ids}
    scores = {engine_id: 0.0 for engine_id in engine_ids}
    games_played = {engine_id: 0 for engine_id in engine_ids}
    for game in games:
        white_score = _white_score(game.result)
        scores[game.white_engine_id] += white_score
        scores[game.black_engine_id] += 1.0 - white_score
        games_played[game.white_engine_id] += 1
        games_played[game.black_engine_id] += 1

    fixed_engine_id = anchor_engine_id or min(engine_ids)
    for _ in range(RATING_ITERATIONS):
        expected_scores = {engine_id: 0.0 for engine_id in engine_ids}
        for game in games:
            white_expected = expected_score(
                ratings[game.white_engine_id],
                ratings[game.black_engine_id],
            )
            expected_scores[game.white_engine_id] += white_expected
            expected_scores[game.black_engine_id] += 1.0 - white_expected

        adjustments = {
            engine_id: ELO_SCALE
            * (scores[engine_id] - expected_scores[engine_id])
            / games_played[engine_id]
            for engine_id in engine_ids
            if engine_id != fixed_engine_id
        }
        if not adjustments or max(abs(value) for value in adjustments.values()) < RATING_CONVERGENCE:
            break
        for engine_id, adjustment in adjustments.items():
            ratings[engine_id] += adjustment
    else:
        raise RatingCommitError("the tournament results did not produce stable Elo ratings")

    if anchor_engine_id is None:
        offset = DEFAULT_ELO - sum(ratings.values()) / len(ratings)
    else:
        offset = anchor_elo - ratings[anchor_engine_id]
    return (
        {
            engine_id: round(rating + offset, 6)
            for engine_id, rating in ratings.items()
        },
        games_played,
    )


def _record_history(
    connection: sqlite3.Connection,
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
) -> None:
    connection.execute(
        f"""
        INSERT INTO rating_list_history (
          engine_id, rating_list_id, tournament_id, opponent_engine_id,
          elo_before, elo, elo_change, score, expected_score,
          hardware_score, opponent_hardware_score, game_id, at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
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
        ),
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
