from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from math import log
from typing import Iterable

from cope.db.repo import (
    RunnerCommandRecord,
    get_tournament,
    list_games,
    list_games_for_tournaments,
    list_tournaments_by_ids,
    utc_now,
)


DEFAULT_ELO = 1500.0
ELO_SCALE = 400.0
ELO_LOGISTIC_FACTOR = log(10.0) / ELO_SCALE
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
    if connection.execute(
        "SELECT 1 FROM engine_relay_fixtures WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchone() is not None:
        raise RatingCommitError("engine relay tournaments are never eligible for ratings")
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

    games_by_list: dict[int, list[tuple[int, object, str]]] = {
        list_id: [] for list_id in selected
    }
    games_applied = 0
    applied_at = utc_now()

    for commit in commits:
        tournament_id = int(commit["tournament_id"])
        rating_list_id = int(commit["rating_list_id"])
        tournament = tournaments.get(tournament_id)
        if tournament is None:
            raise RatingCommitError(f"committed tournament {tournament_id} no longer exists")
        games = _committable_games(tournament, tuple(games_by_tournament[tournament_id]))
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
        rated_engine_ids = category_ratings.keys()
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
            connection.executemany(
                """
                INSERT INTO rating_list_history (
                  engine_id, rating_list_id, tournament_id, opponent_engine_id,
                  elo_before, elo, elo_change, score, expected_score,
                  hardware_score, opponent_hardware_score, game_id, at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                history_rows,
            )

    engines_updated = 0
    rating_rows = []
    for rating_list_id, category_ratings in ratings.items():
        for engine_id, elo in category_ratings.items():
            rating_rows.append(
                (
                    engine_id,
                    rating_list_id,
                    elo,
                    games_played[rating_list_id][engine_id],
                    applied_at,
                )
            )
            engines_updated += 1
    if rating_rows:
        connection.executemany(
            """
            INSERT INTO rating_list_ratings (
              engine_id, rating_list_id, elo, games_played, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            rating_rows,
        )

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
