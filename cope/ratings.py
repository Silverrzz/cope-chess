from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable

from cope.db.repo import RunnerCommandRecord, get_tournament, list_games, utc_now


DEFAULT_ELO = 1500.0
ELO_K_FACTOR = 32.0
ELO_SCALE = 400.0


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

    ratings: dict[int, dict[int, float]] = {list_id: {} for list_id in selected}
    games_played: dict[int, dict[int, int]] = {list_id: {} for list_id in selected}
    for rating_list_id, (anchor_engine_id, _) in anchors.items():
        if anchor_engine_id is not None:
            ratings[rating_list_id][anchor_engine_id] = DEFAULT_ELO
            games_played[rating_list_id][anchor_engine_id] = 0
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
        category_ratings = ratings[rating_list_id]
        category_games = games_played[rating_list_id]

        for game in games:
            white_id = game.white_engine_id
            black_id = game.black_engine_id
            white_before = category_ratings.setdefault(white_id, DEFAULT_ELO)
            black_before = category_ratings.setdefault(black_id, DEFAULT_ELO)
            category_games.setdefault(white_id, 0)
            category_games.setdefault(black_id, 0)
            white_score = _white_score(game.result)
            black_score = 1.0 - white_score
            white_expected = expected_score(
                white_before,
                black_before,
            )
            white_change = ELO_K_FACTOR * (white_score - white_expected)
            white_after = round(white_before + white_change, 6)
            black_after = round(black_before - white_change, 6)

            _record_history(
                connection,
                engine_id=white_id,
                opponent_engine_id=black_id,
                rating_list_id=rating_list_id,
                tournament_id=tournament_id,
                game_id=game.id,
                elo_before=white_before,
                elo=white_after,
                score=white_score,
                expected_score=white_expected,
                at=history_at,
            )
            _record_history(
                connection,
                engine_id=black_id,
                opponent_engine_id=white_id,
                rating_list_id=rating_list_id,
                tournament_id=tournament_id,
                game_id=game.id,
                elo_before=black_before,
                elo=black_after,
                score=black_score,
                expected_score=1.0 - white_expected,
                at=history_at,
            )
            category_ratings[white_id] = white_after
            category_ratings[black_id] = black_after
            category_games[white_id] += 1
            category_games[black_id] += 1
            games_applied += 1

    for rating_list_id, category_ratings in ratings.items():
        anchor_engine_id, anchor_elo = anchors[rating_list_id]
        if anchor_engine_id is None:
            continue
        offset = anchor_elo - category_ratings[anchor_engine_id]
        for engine_id in category_ratings:
            category_ratings[engine_id] = round(category_ratings[engine_id] + offset, 6)
        connection.execute(
            """
            UPDATE rating_list_history
            SET elo_before = elo_before + ?, elo = elo + ?
            WHERE rating_list_id = ?
            """,
            (offset, offset, rating_list_id),
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
