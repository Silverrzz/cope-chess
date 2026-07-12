from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import cope.ratings as ratings
from cope.db.repo import RunnerCommandRecord


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE tournament_rating_commits (
          tournament_id INTEGER PRIMARY KEY,
          category_id INTEGER NOT NULL,
          command_id INTEGER,
          status TEXT NOT NULL,
          requested_at TEXT NOT NULL,
          applied_at TEXT,
          error TEXT
        );
        CREATE TABLE ratings (
          engine_id INTEGER NOT NULL,
          category_id INTEGER NOT NULL,
          elo REAL NOT NULL,
          games_played INTEGER NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (engine_id, category_id)
        );
        CREATE TABLE rating_history (
          engine_id INTEGER NOT NULL,
          category_id INTEGER NOT NULL,
          tournament_id INTEGER NOT NULL,
          opponent_engine_id INTEGER NOT NULL,
          elo_before REAL NOT NULL,
          elo REAL NOT NULL,
          elo_change REAL NOT NULL,
          score REAL NOT NULL,
          game_id INTEGER NOT NULL,
          at TEXT NOT NULL
        );
        INSERT INTO tournament_rating_commits (
          tournament_id, category_id, command_id, status, requested_at
        ) VALUES (1, 2, 9, 'pending', '2026-01-01T00:00:00+00:00');
        """
    )
    return connection


def test_aborted_rating_commit_applies_only_finished_games(monkeypatch) -> None:
    connection = _connection()
    tournament = SimpleNamespace(
        status="aborted",
        category_id=2,
        config=SimpleNamespace(
            rated=True,
            category_settings_linked=True,
            participants=[10, 20],
        ),
    )
    finished = SimpleNamespace(
        id=100,
        status="finished",
        result="1-0",
        white_engine_id=10,
        black_engine_id=20,
    )
    abandoned = SimpleNamespace(
        id=101,
        status="abandoned",
        result=None,
        white_engine_id=20,
        black_engine_id=10,
    )
    monkeypatch.setattr(ratings, "get_tournament", lambda *_args: tournament)
    monkeypatch.setattr(ratings, "list_games", lambda *_args: (finished, abandoned))
    command = RunnerCommandRecord(
        id=9,
        command="commit_tournament_results",
        payload={"tournament_id": 1, "category_id": 2},
        status="claimed",
        created_at="2026-01-01T00:00:00+00:00",
        claimed_at="2026-01-01T00:00:01+00:00",
        finished_at=None,
        error=None,
    )

    result = ratings.apply_tournament_rating_commit(connection, command)

    assert result.games_applied == 1
    assert result.engines_updated == 2
    assert connection.execute("SELECT COUNT(*) FROM rating_history").fetchone()[0] == 2
    assert connection.execute("SELECT SUM(games_played) FROM ratings").fetchone()[0] == 2
    commit = connection.execute(
        "SELECT status FROM tournament_rating_commits WHERE tournament_id = 1"
    ).fetchone()
    assert commit["status"] == "applied"


def test_aborted_rating_commit_requires_a_finished_game(monkeypatch) -> None:
    connection = _connection()
    tournament = SimpleNamespace(
        status="aborted",
        category_id=2,
        config=SimpleNamespace(
            rated=True,
            category_settings_linked=True,
            participants=[10, 20],
        ),
    )
    abandoned = SimpleNamespace(
        id=101,
        status="abandoned",
        result=None,
        white_engine_id=20,
        black_engine_id=10,
    )
    monkeypatch.setattr(ratings, "get_tournament", lambda *_args: tournament)
    monkeypatch.setattr(ratings, "list_games", lambda *_args: (abandoned,))
    command = RunnerCommandRecord(
        id=9,
        command="commit_tournament_results",
        payload={"tournament_id": 1, "category_id": 2},
        status="claimed",
        created_at="2026-01-01T00:00:00+00:00",
        claimed_at="2026-01-01T00:00:01+00:00",
        finished_at=None,
        error=None,
    )

    try:
        ratings.apply_tournament_rating_commit(connection, command)
    except ratings.RatingCommitError as exc:
        assert str(exc) == "tournament has no finished games"
    else:
        raise AssertionError("commit unexpectedly accepted an aborted tournament with no results")
