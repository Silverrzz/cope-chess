from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class PuzzleSuiteRecord:
    id: int
    name: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class PuzzleSuitePuzzleRecord:
    id: int
    suite_id: int
    position: int
    title: str
    fen: str
    solutions: tuple[str, ...]
    included: bool
    uniqueness_status: str
    verified_solution: str
    best_move: str
    second_move: str
    best_sigmoid: float | None
    second_sigmoid: float | None
    sigmoid_gap: float | None
    uniqueness_depth: int | None
    uniqueness_nodes: int | None
    uniqueness_time_ms: int | None
    uniqueness_error: str
    difficulty_elo: float | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class PuzzleSuiteRunRecord:
    id: int
    suite_id: int
    job_id: int
    stage: str
    rating_list_id: int | None
    settings: dict[str, Any]
    created_at: str


@dataclass(frozen=True, slots=True)
class PuzzleSuiteEngineResultRecord:
    id: int
    run_id: int
    puzzle_id: int
    engine_version_id: int
    engine_name: str
    engine_version: str
    engine_elo: float
    estimate_elo: float | None
    status: str
    best_move: str
    solution_nodes: int | None
    final_nodes: int | None
    depth: int | None
    time_ms: int
    error: str
    created_at: str


def create_puzzle_suite(
    connection: sqlite3.Connection,
    *,
    name: str,
    puzzles: Iterable[dict[str, Any]],
) -> PuzzleSuiteRecord:
    normalized_name = " ".join(name.split())
    values = tuple(puzzles)
    if not normalized_name:
        raise ValueError("enter a suite name")
    if not values:
        raise ValueError("add at least one puzzle")
    now = _utc_now()
    row = connection.execute(
        """
        INSERT INTO puzzle_suites (name, created_at, updated_at)
        VALUES (?, ?, ?)
        RETURNING id
        """,
        (normalized_name, now, now),
    ).fetchone()
    if row is None:
        raise RuntimeError("puzzle suite was not created")
    suite_id = int(row["id"])
    connection.executemany(
        """
        INSERT INTO puzzle_suite_puzzles (
          suite_id, position, title, fen, solutions, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (
                suite_id,
                position,
                str(puzzle.get("title") or ""),
                str(puzzle["fen"]),
                json.dumps(tuple(puzzle["solutions"])),
                now,
                now,
            )
            for position, puzzle in enumerate(values)
        ),
    )
    suite = get_puzzle_suite(connection, suite_id)
    if suite is None:
        raise RuntimeError("puzzle suite disappeared after creation")
    return suite


def get_puzzle_suite(
    connection: sqlite3.Connection,
    suite_id: int,
) -> PuzzleSuiteRecord | None:
    row = connection.execute(
        "SELECT * FROM puzzle_suites WHERE id = ?",
        (suite_id,),
    ).fetchone()
    return None if row is None else _suite_from_row(row)


def list_puzzle_suites(
    connection: sqlite3.Connection,
    *,
    limit: int = 100,
) -> tuple[PuzzleSuiteRecord, ...]:
    return tuple(
        _suite_from_row(row)
        for row in connection.execute(
            """
            SELECT * FROM puzzle_suites
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (max(1, min(limit, 500)),),
        )
    )


def list_puzzle_suite_puzzles(
    connection: sqlite3.Connection,
    suite_id: int,
    *,
    included_only: bool = False,
) -> tuple[PuzzleSuitePuzzleRecord, ...]:
    included = "AND included = 1" if included_only else ""
    return tuple(
        _puzzle_from_row(row)
        for row in connection.execute(
            f"""
            SELECT * FROM puzzle_suite_puzzles
            WHERE suite_id = ? {included}
            ORDER BY position, id
            """,
            (suite_id,),
        )
    )


def set_puzzle_suite_puzzle_included(
    connection: sqlite3.Connection,
    *,
    suite_id: int,
    puzzle_id: int,
    included: bool,
) -> None:
    if included:
        cursor = connection.execute(
            """
            UPDATE puzzle_suite_puzzles
            SET included = 1, updated_at = ?
            WHERE id = ? AND suite_id = ? AND uniqueness_status = 'unique'
            """,
            (_utc_now(), puzzle_id, suite_id),
        )
    else:
        cursor = connection.execute(
            """
            UPDATE puzzle_suite_puzzles
            SET included = 0, updated_at = ?
            WHERE id = ? AND suite_id = ?
            """,
            (_utc_now(), puzzle_id, suite_id),
        )
    if cursor.rowcount != 1:
        raise ValueError("puzzle cannot be included in its current state")
    _touch_suite(connection, suite_id)


def prepare_puzzle_suite_run(
    connection: sqlite3.Connection,
    *,
    suite_id: int,
    job_id: int,
    stage: str,
    rating_list_id: int | None,
    settings: dict[str, Any],
) -> PuzzleSuiteRunRecord:
    if stage not in {"uniqueness", "difficulty"}:
        raise ValueError("invalid puzzle suite stage")
    if get_puzzle_suite(connection, suite_id) is None:
        raise ValueError("puzzle suite not found")
    now = _utc_now()
    row = connection.execute(
        """
        INSERT INTO puzzle_suite_runs (
          suite_id, job_id, stage, rating_list_id, settings, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (suite_id, job_id, stage, rating_list_id, json.dumps(settings), now),
    ).fetchone()
    if row is None:
        raise RuntimeError("puzzle suite run was not created")
    if stage == "uniqueness":
        connection.execute(
            """
            UPDATE puzzle_suite_puzzles
            SET included = 0, uniqueness_status = 'pending', verified_solution = '',
                best_move = '', second_move = '', best_sigmoid = NULL,
                second_sigmoid = NULL, sigmoid_gap = NULL,
                uniqueness_depth = NULL, uniqueness_nodes = NULL,
                uniqueness_time_ms = NULL, uniqueness_error = '',
                difficulty_elo = NULL, updated_at = ?
            WHERE suite_id = ?
            """,
            (now, suite_id),
        )
    elif stage == "difficulty" and settings.get("suite_stage") != "miss_finetuning":
        connection.execute(
            """
            UPDATE puzzle_suite_puzzles SET difficulty_elo = NULL, updated_at = ?
            WHERE suite_id = ?
            """,
            (now, suite_id),
        )
    _touch_suite(connection, suite_id, now=now)
    run = get_puzzle_suite_run_for_job(connection, job_id)
    if run is None:
        raise RuntimeError("puzzle suite run disappeared after creation")
    return run


def get_puzzle_suite_run_for_job(
    connection: sqlite3.Connection,
    job_id: int,
) -> PuzzleSuiteRunRecord | None:
    row = connection.execute(
        "SELECT * FROM puzzle_suite_runs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    return None if row is None else _run_from_row(row)


def list_puzzle_suite_runs(
    connection: sqlite3.Connection,
    suite_id: int,
    *,
    limit: int = 30,
) -> tuple[PuzzleSuiteRunRecord, ...]:
    return tuple(
        _run_from_row(row)
        for row in connection.execute(
            """
            SELECT * FROM puzzle_suite_runs
            WHERE suite_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (suite_id, max(1, min(limit, 200))),
        )
    )


def record_puzzle_suite_result(
    connection: sqlite3.Connection,
    *,
    job_id: int,
    puzzle_id: int,
    engine_version_id: int,
    status: str,
    best_move: str,
    second_move: str,
    best_sigmoid: float | None,
    second_sigmoid: float | None,
    sigmoid_gap: float | None,
    solution_nodes: int | None,
    final_nodes: int | None,
    depth: int | None,
    time_ms: int,
    error: str,
) -> None:
    run = get_puzzle_suite_run_for_job(connection, job_id)
    if run is None:
        raise ValueError("puzzle suite run not found")
    puzzle = connection.execute(
        "SELECT solutions FROM puzzle_suite_puzzles WHERE id = ? AND suite_id = ?",
        (puzzle_id, run.suite_id),
    ).fetchone()
    if puzzle is None:
        raise ValueError("puzzle does not belong to the assigned suite")
    now = _utc_now()
    if run.stage == "uniqueness":
        if status not in {"unique", "ambiguous", "failed"}:
            raise ValueError("invalid uniqueness result")
        solutions = tuple(json.loads(puzzle["solutions"] or "[]"))
        verified_solution = best_move if status == "unique" and best_move in solutions else ""
        normalized_status = status if status != "unique" or verified_solution else "ambiguous"
        normalized_error = error
        if status == "unique" and not verified_solution:
            normalized_error = "engine best move did not match the supplied solution"
        connection.execute(
            """
            UPDATE puzzle_suite_puzzles
            SET included = ?, uniqueness_status = ?, verified_solution = ?,
                best_move = ?, second_move = ?, best_sigmoid = ?, second_sigmoid = ?,
                sigmoid_gap = ?, uniqueness_depth = ?, uniqueness_nodes = ?,
                uniqueness_time_ms = ?, uniqueness_error = ?, updated_at = ?
            WHERE id = ? AND suite_id = ?
            """,
            (
                1 if normalized_status == "unique" else 0,
                normalized_status,
                verified_solution,
                best_move,
                second_move,
                best_sigmoid,
                second_sigmoid,
                sigmoid_gap,
                depth,
                final_nodes,
                time_ms,
                normalized_error[-8000:],
                now,
                puzzle_id,
                run.suite_id,
            ),
        )
    else:
        if status not in {"solved", "unsolved", "failed"}:
            raise ValueError("invalid difficulty result")
        engine_elos = run.settings.get("engine_elos", {})
        raw_elo = engine_elos.get(str(engine_version_id), engine_elos.get(engine_version_id))
        if not isinstance(raw_elo, (int, float)):
            raise ValueError("assigned engine has no rating")
        engine_elo = float(raw_elo)
        estimate = _difficulty_estimate(
            engine_elo,
            status=status,
            solution_nodes=solution_nodes,
            final_nodes=final_nodes,
        )
        connection.execute(
            """
            INSERT INTO puzzle_suite_engine_results (
              run_id, puzzle_id, engine_version_id, engine_elo, estimate_elo,
              status, best_move, solution_nodes, final_nodes, depth,
              time_ms, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, puzzle_id, engine_version_id) DO UPDATE SET
              engine_elo = EXCLUDED.engine_elo,
              estimate_elo = EXCLUDED.estimate_elo,
              status = EXCLUDED.status,
              best_move = EXCLUDED.best_move,
              solution_nodes = EXCLUDED.solution_nodes,
              final_nodes = EXCLUDED.final_nodes,
              depth = EXCLUDED.depth,
              time_ms = EXCLUDED.time_ms,
              error = EXCLUDED.error,
              created_at = EXCLUDED.created_at
            """,
            (
                run.id,
                puzzle_id,
                engine_version_id,
                engine_elo,
                estimate,
                status,
                best_move,
                solution_nodes,
                final_nodes,
                depth,
                time_ms,
                error[-8000:],
                now,
            ),
        )
        row = connection.execute(
            """
            SELECT AVG(estimate_elo) AS difficulty
            FROM puzzle_suite_engine_results
            WHERE run_id = ? AND puzzle_id = ? AND estimate_elo IS NOT NULL
            """,
            (run.id, puzzle_id),
        ).fetchone()
        difficulty = None if row is None or row["difficulty"] is None else float(row["difficulty"])
        connection.execute(
            """
            UPDATE puzzle_suite_puzzles SET difficulty_elo = ?, updated_at = ?
            WHERE id = ? AND suite_id = ?
            """,
            (difficulty, now, puzzle_id, run.suite_id),
        )
    _touch_suite(connection, run.suite_id, now=now)


def list_puzzle_suite_engine_results(
    connection: sqlite3.Connection,
    run_id: int,
) -> tuple[PuzzleSuiteEngineResultRecord, ...]:
    return tuple(
        _engine_result_from_row(row)
        for row in connection.execute(
            """
            SELECT result.*, engine.name AS engine_name, version.version AS engine_version
            FROM puzzle_suite_engine_results result
            JOIN engine_versions version ON version.id = result.engine_version_id
            JOIN engines engine ON engine.id = version.engine_id
            WHERE result.run_id = ?
            ORDER BY result.puzzle_id, result.engine_elo, result.engine_version_id
            """,
            (run_id,),
        )
    )


def _difficulty_estimate(
    engine_elo: float,
    *,
    status: str,
    solution_nodes: int | None,
    final_nodes: int | None,
) -> float | None:
    if status == "failed":
        return None
    nodes = solution_nodes if status == "solved" else final_nodes
    if nodes is None or nodes <= 0:
        return None
    unsolved_bonus = 100.0 if status == "unsolved" else 0.0
    return round(engine_elo + 100.0 * math.log2(nodes / 10_000.0) + unsolved_bonus, 3)


def _touch_suite(connection: sqlite3.Connection, suite_id: int, *, now: str | None = None) -> None:
    connection.execute(
        "UPDATE puzzle_suites SET updated_at = ? WHERE id = ?",
        (now or _utc_now(), suite_id),
    )


def _suite_from_row(row) -> PuzzleSuiteRecord:
    return PuzzleSuiteRecord(
        id=int(row["id"]),
        name=str(row["name"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _puzzle_from_row(row) -> PuzzleSuitePuzzleRecord:
    return PuzzleSuitePuzzleRecord(
        id=int(row["id"]),
        suite_id=int(row["suite_id"]),
        position=int(row["position"]),
        title=str(row["title"] or ""),
        fen=str(row["fen"]),
        solutions=tuple(json.loads(row["solutions"] or "[]")),
        included=bool(row["included"]),
        uniqueness_status=str(row["uniqueness_status"]),
        verified_solution=str(row["verified_solution"] or ""),
        best_move=str(row["best_move"] or ""),
        second_move=str(row["second_move"] or ""),
        best_sigmoid=None if row["best_sigmoid"] is None else float(row["best_sigmoid"]),
        second_sigmoid=None if row["second_sigmoid"] is None else float(row["second_sigmoid"]),
        sigmoid_gap=None if row["sigmoid_gap"] is None else float(row["sigmoid_gap"]),
        uniqueness_depth=None if row["uniqueness_depth"] is None else int(row["uniqueness_depth"]),
        uniqueness_nodes=None if row["uniqueness_nodes"] is None else int(row["uniqueness_nodes"]),
        uniqueness_time_ms=None if row["uniqueness_time_ms"] is None else int(row["uniqueness_time_ms"]),
        uniqueness_error=str(row["uniqueness_error"] or ""),
        difficulty_elo=None if row["difficulty_elo"] is None else float(row["difficulty_elo"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _run_from_row(row) -> PuzzleSuiteRunRecord:
    return PuzzleSuiteRunRecord(
        id=int(row["id"]),
        suite_id=int(row["suite_id"]),
        job_id=int(row["job_id"]),
        stage=str(row["stage"]),
        rating_list_id=None if row["rating_list_id"] is None else int(row["rating_list_id"]),
        settings=json.loads(row["settings"] or "{}"),
        created_at=str(row["created_at"]),
    )


def _engine_result_from_row(row) -> PuzzleSuiteEngineResultRecord:
    return PuzzleSuiteEngineResultRecord(
        id=int(row["id"]),
        run_id=int(row["run_id"]),
        puzzle_id=int(row["puzzle_id"]),
        engine_version_id=int(row["engine_version_id"]),
        engine_name=str(row["engine_name"]),
        engine_version=str(row["engine_version"]),
        engine_elo=float(row["engine_elo"]),
        estimate_elo=None if row["estimate_elo"] is None else float(row["estimate_elo"]),
        status=str(row["status"]),
        best_move=str(row["best_move"] or ""),
        solution_nodes=None if row["solution_nodes"] is None else int(row["solution_nodes"]),
        final_nodes=None if row["final_nodes"] is None else int(row["final_nodes"]),
        depth=None if row["depth"] is None else int(row["depth"]),
        time_ms=int(row["time_ms"]),
        error=str(row["error"] or ""),
        created_at=str(row["created_at"]),
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
