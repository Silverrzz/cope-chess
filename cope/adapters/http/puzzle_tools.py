from __future__ import annotations

import math
import re
import sqlite3
from typing import Any

import chess
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from cope.adapters.http.responses import _json, _publish_admin_change
from cope.db import (
    create_puzzle_suite,
    create_tool_job,
    get_puzzle_suite,
    get_rating_list,
    get_tool_job,
    list_engine_records,
    list_puzzle_suite_engine_results,
    list_puzzle_suite_puzzles,
    list_puzzle_suite_runs,
    list_puzzle_suites,
    list_rating_lists,
    list_rating_rows,
    list_workers,
    prepare_puzzle_suite_run,
    set_puzzle_suite_puzzle_included,
)
from cope.domains.tools.catalogue import PUZZLE_DIFFICULTY, PUZZLE_UNIQUENESS
from cope.domains.tools.views import tool_job_view


class PuzzleSuiteCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    puzzles: str = Field(min_length=1, max_length=2_000_000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("enter a suite name")
        return normalized


class PuzzleSuiteUniquenessPayload(BaseModel):
    engine_id: int = Field(gt=0)
    movetime_ms: int = Field(default=30_000, ge=100, le=3_600_000)
    multipv: int = Field(default=2, ge=2, le=20)
    threads: int = Field(default=1, gt=0, le=1024)
    hash_mb: int = Field(default=256, gt=0, le=1_048_576)
    min_sigmoid_gap: float = Field(default=0.15, gt=0, le=1, allow_inf_nan=False)


class PuzzleSuiteDifficultyPayload(BaseModel):
    engine_ids: list[int] = Field(min_length=1, max_length=100)
    rating_list_id: int = Field(gt=0)
    movetime_ms: int = Field(default=30_000, ge=100, le=3_600_000)
    threads: int = Field(default=1, gt=0, le=1024)
    hash_mb: int = Field(default=256, gt=0, le=1_048_576)

    @field_validator("engine_ids")
    @classmethod
    def validate_engine_ids(cls, value: list[int]) -> list[int]:
        if any(engine_id <= 0 for engine_id in value):
            raise ValueError("engine ids must be positive")
        if len(set(value)) != len(value):
            raise ValueError("engine ids must be unique")
        return value


class PuzzleSuitePuzzleIncludePayload(BaseModel):
    included: bool


def register_puzzle_tools_routes(app: FastAPI) -> None:
    from cope.web import app as web_app

    @app.get("/api/admin/tools/puzzle-suite-manager")
    def admin_puzzle_suite_manager(
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        workers = list_workers(connection)
        return _json(
            {
                "engines": [
                    {
                        "id": engine.id,
                        "family_id": engine.engine_id,
                        "name": engine.name,
                        "author": engine.author,
                        "version": engine.version,
                        "distribution": engine.distribution,
                        "artifact_ready": engine.artifact is not None,
                        "active": engine.engine_active,
                    }
                    for engine in list_engine_records(connection)
                ],
                "rating_lists": [
                    {
                        "id": rating_list.id,
                        "name": rating_list.name,
                        "ratings": [
                            {"engine_id": row.engine.engine_id, "elo": row.elo}
                            for row in list_rating_rows(connection, rating_list.id)
                        ],
                    }
                    for rating_list in list_rating_lists(connection)
                ],
                "suites": [
                    _puzzle_suite_summary_payload(connection, suite)
                    for suite in list_puzzle_suites(connection)
                ],
                "workers": [
                    {
                        "id": worker.id,
                        "label": worker.label,
                        "status": worker.status,
                        "threads": None if worker.capacity is None else worker.capacity.threads,
                        "hash_mb": None if worker.capacity is None else worker.capacity.hash_mb,
                    }
                    for worker in workers
                    if worker.status in {"connected", "ready", "busy"}
                ],
            }
        )

    @app.post("/api/admin/tools/puzzle-suite-manager/suites")
    def admin_create_puzzle_suite(
        payload: PuzzleSuiteCreatePayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            puzzles = _parse_puzzle_suite_block(payload.puzzles)
            suite = create_puzzle_suite(connection, name=payload.name, puzzles=puzzles)
            connection.commit()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "suite": _puzzle_suite_detail_payload(connection, suite),
                "message": f"Imported {len(puzzles)} puzzles.",
            },
            status_code=201,
        )

    @app.get("/api/admin/tools/puzzle-suite-manager/suites/{suite_id}")
    def admin_puzzle_suite(
        suite_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        suite = get_puzzle_suite(connection, suite_id)
        if suite is None:
            raise HTTPException(status_code=404, detail="Puzzle suite not found.")
        return _json({"suite": _puzzle_suite_detail_payload(connection, suite)})

    @app.post("/api/admin/tools/puzzle-suite-manager/suites/{suite_id}/uniqueness")
    def admin_start_puzzle_suite_uniqueness(
        suite_id: int,
        payload: PuzzleSuiteUniquenessPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        suite = get_puzzle_suite(connection, suite_id, lock=True)
        if suite is None:
            raise HTTPException(status_code=404, detail="Puzzle suite not found.")
        _ensure_puzzle_suite_idle(connection, suite_id)
        puzzles = list_puzzle_suite_puzzles(connection, suite_id)
        puzzle_input = [
            {
                "id": puzzle.id,
                "fen": puzzle.fen,
                "solutions": list(puzzle.solutions),
            }
            for puzzle in puzzles
        ]
        settings = payload.model_dump(mode="json")
        try:
            job = create_tool_job(
                connection,
                tool_name=PUZZLE_UNIQUENESS.key,
                input_data={
                    "suite_id": suite_id,
                    "stage": "uniqueness",
                    "puzzles": puzzle_input,
                    **settings,
                },
                engine_version_ids=(payload.engine_id,),
                required_threads=payload.threads,
                required_hash_mb=payload.hash_mb,
            )
            prepare_puzzle_suite_run(
                connection,
                suite_id=suite_id,
                job_id=job.id,
                stage="uniqueness",
                rating_list_id=None,
                settings=settings,
            )
            connection.commit()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "job": tool_job_view(connection, job, include_items=True),
                "message": f"Uniqueness analysis queued for {len(puzzles)} puzzles.",
            },
            status_code=201,
        )

    @app.post("/api/admin/tools/puzzle-suite-manager/suites/{suite_id}/difficulty")
    def admin_start_puzzle_suite_difficulty(
        suite_id: int,
        payload: PuzzleSuiteDifficultyPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        suite = get_puzzle_suite(connection, suite_id, lock=True)
        if suite is None:
            raise HTTPException(status_code=404, detail="Puzzle suite not found.")
        _ensure_puzzle_suite_idle(connection, suite_id)
        rating_list = get_rating_list(connection, payload.rating_list_id)
        if rating_list is None:
            raise HTTPException(status_code=422, detail="Rating list not found.")
        ratings = {row.engine.engine_id: row.elo for row in list_rating_rows(connection, rating_list.id)}
        missing = [engine_id for engine_id in payload.engine_ids if engine_id not in ratings]
        if missing:
            raise HTTPException(
                status_code=422,
                detail="Every selected engine must have an Elo in the selected rating list.",
            )
        suite_puzzles = list_puzzle_suite_puzzles(connection, suite_id)
        unfiltered = bool(suite_puzzles) and all(
            puzzle.uniqueness_status == "pending" for puzzle in suite_puzzles
        )
        if unfiltered:
            puzzles = tuple(
                (puzzle, puzzle.solutions[0])
                for puzzle in suite_puzzles
                if len(puzzle.solutions) == 1
            )
            if len(puzzles) != len(suite_puzzles):
                raise HTTPException(
                    status_code=422,
                    detail="Every unfiltered puzzle must have exactly one supplied solution before rating.",
                )
        else:
            puzzles = tuple(
                (puzzle, puzzle.verified_solution)
                for puzzle in suite_puzzles
                if puzzle.included
                and puzzle.uniqueness_status == "unique"
                and puzzle.verified_solution
            )
        if not puzzles:
            raise HTTPException(
                status_code=422,
                detail="Add an unfiltered suite with one supplied solution per puzzle, or include at least one verified unique puzzle.",
            )
        engine_elos = {str(engine_id): ratings[engine_id] for engine_id in payload.engine_ids}
        settings = {
            **payload.model_dump(mode="json"),
            "engine_elos": engine_elos,
            "rating_list_name": rating_list.name,
        }
        puzzle_input = [
            {
                "id": puzzle.id,
                "fen": puzzle.fen,
                "solutions": [solution],
            }
            for puzzle, solution in puzzles
        ]
        try:
            job = create_tool_job(
                connection,
                tool_name=PUZZLE_DIFFICULTY.key,
                input_data={
                    "suite_id": suite_id,
                    "stage": "difficulty",
                    "puzzles": puzzle_input,
                    "multipv": 1,
                    **settings,
                },
                engine_version_ids=payload.engine_ids,
                required_threads=payload.threads,
                required_hash_mb=payload.hash_mb,
            )
            prepare_puzzle_suite_run(
                connection,
                suite_id=suite_id,
                job_id=job.id,
                stage="difficulty",
                rating_list_id=rating_list.id,
                settings=settings,
            )
            connection.commit()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "job": tool_job_view(connection, job, include_items=True),
                "message": f"Difficulty analysis queued across {len(payload.engine_ids)} engines.",
            },
            status_code=201,
        )

    @app.post("/api/admin/tools/puzzle-suite-manager/suites/{suite_id}/miss-finetuning")
    def admin_start_puzzle_suite_miss_finetuning(
        suite_id: int,
        payload: PuzzleSuiteDifficultyPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        suite = get_puzzle_suite(connection, suite_id, lock=True)
        if suite is None:
            raise HTTPException(status_code=404, detail="Puzzle suite not found.")
        _ensure_puzzle_suite_idle(connection, suite_id)
        rating_list = get_rating_list(connection, payload.rating_list_id)
        if rating_list is None:
            raise HTTPException(status_code=422, detail="Rating list not found.")
        ratings = {row.engine.engine_id: row.elo for row in list_rating_rows(connection, rating_list.id)}
        missing = [engine_id for engine_id in payload.engine_ids if engine_id not in ratings]
        if missing:
            raise HTTPException(
                status_code=422,
                detail="Every selected engine must have an Elo in the selected rating list.",
            )
        miss_selection = _puzzle_suite_missed_puzzle_ids(connection, suite_id)
        if miss_selection is None:
            raise HTTPException(
                status_code=422,
                detail="Complete a difficulty rating run before miss finetuning.",
            )
        source_difficulty_run_id, missed_puzzle_ids = miss_selection
        if not missed_puzzle_ids:
            raise HTTPException(
                status_code=422,
                detail="Every puzzle was solved in the latest completed difficulty run.",
            )
        puzzles = []
        for puzzle in list_puzzle_suite_puzzles(connection, suite_id):
            if puzzle.id not in missed_puzzle_ids:
                continue
            solution = puzzle.verified_solution
            if not solution and len(puzzle.solutions) == 1:
                solution = puzzle.solutions[0]
            if solution:
                puzzles.append((puzzle, solution))
        if len(puzzles) != len(missed_puzzle_ids):
            raise HTTPException(
                status_code=422,
                detail="One or more missed puzzles no longer has exactly one target solution.",
            )
        thread_elo_bonus = 80.0 * math.log2(payload.threads)
        base_engine_elos = {
            str(engine_id): ratings[engine_id] for engine_id in payload.engine_ids
        }
        engine_elos = {
            engine_id: round(float(elo) + thread_elo_bonus, 3)
            for engine_id, elo in base_engine_elos.items()
        }
        settings = {
            **payload.model_dump(mode="json"),
            "engine_elos": engine_elos,
            "base_engine_elos": base_engine_elos,
            "thread_elo_bonus": round(thread_elo_bonus, 3),
            "thread_elo_per_doubling": 80,
            "rating_list_name": rating_list.name,
            "source_difficulty_run_id": source_difficulty_run_id,
            "suite_stage": "miss_finetuning",
        }
        puzzle_input = [
            {
                "id": puzzle.id,
                "fen": puzzle.fen,
                "solutions": [solution],
            }
            for puzzle, solution in puzzles
        ]
        try:
            job = create_tool_job(
                connection,
                tool_name=PUZZLE_DIFFICULTY.key,
                input_data={
                    "suite_id": suite_id,
                    "stage": "difficulty",
                    "suite_stage": "miss_finetuning",
                    "puzzles": puzzle_input,
                    "multipv": 1,
                    **settings,
                },
                engine_version_ids=payload.engine_ids,
                required_threads=payload.threads,
                required_hash_mb=payload.hash_mb,
            )
            prepare_puzzle_suite_run(
                connection,
                suite_id=suite_id,
                job_id=job.id,
                stage="difficulty",
                rating_list_id=rating_list.id,
                settings=settings,
            )
            connection.commit()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "job": tool_job_view(connection, job, include_items=True),
                "message": (
                    f"Miss finetuning queued for {len(puzzles)} puzzles with "
                    f"a {thread_elo_bonus:.1f} Elo thread adjustment."
                ),
            },
            status_code=201,
        )

    @app.patch("/api/admin/tools/puzzle-suite-manager/suites/{suite_id}/puzzles/{puzzle_id}")
    def admin_update_puzzle_suite_puzzle(
        suite_id: int,
        puzzle_id: int,
        payload: PuzzleSuitePuzzleIncludePayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if get_puzzle_suite(connection, suite_id, lock=True) is None:
            raise HTTPException(status_code=404, detail="Puzzle suite not found.")
        try:
            set_puzzle_suite_puzzle_included(
                connection,
                suite_id=suite_id,
                puzzle_id=puzzle_id,
                included=payload.included,
            )
            connection.commit()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json({"message": "Puzzle selection updated."})


def _parse_puzzle_suite_block(value: str) -> list[dict[str, Any]]:
    lines = tuple(
        (number, line.strip())
        for number, line in enumerate(value.splitlines(), start=1)
        if line.strip()
    )
    if not lines:
        raise ValueError("paste at least one puzzle")
    if len(lines) > 5000:
        raise ValueError("a puzzle suite can contain at most 5,000 puzzles")
    puzzles: list[dict[str, Any]] = []
    for number, line in lines:
        if line.count("|") != 1:
            raise ValueError(f"line {number} must use fen|solution")
        fen, solution_text = (part.strip() for part in line.split("|", 1))
        raw_solutions = [part.strip() for part in re.split(r"[,/]+", solution_text) if part.strip()]
        if not fen:
            raise ValueError(f"line {number} is missing a FEN")
        if not raw_solutions:
            raise ValueError(f"line {number} is missing a solution")
        try:
            board = chess.Board(fen)
        except ValueError as exc:
            raise ValueError(f"line {number} has an invalid FEN: {exc}") from exc
        if board.is_game_over() or not any(board.legal_moves):
            raise ValueError(f"line {number} must contain a position with a legal move")
        solutions: list[str] = []
        for raw in raw_solutions:
            try:
                if re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbn]?", raw, re.IGNORECASE):
                    move = chess.Move.from_uci(raw.lower())
                    if move not in board.legal_moves:
                        raise ValueError
                else:
                    move = board.parse_san(raw)
            except ValueError as exc:
                raise ValueError(f"line {number}: {raw!r} is not a legal solution move") from exc
            if move.uci() not in solutions:
                solutions.append(move.uci())
        puzzles.append({"fen": board.fen(), "solutions": solutions, "title": ""})
    return puzzles


def _ensure_puzzle_suite_idle(connection, suite_id: int) -> None:
    row = connection.execute(
        """
        SELECT job.id
        FROM puzzle_suite_runs run
        JOIN tool_jobs job ON job.id = run.job_id
        WHERE run.suite_id = ? AND job.status IN ('queued', 'running')
        LIMIT 1
        """,
        (suite_id,),
    ).fetchone()
    if row is not None:
        raise HTTPException(status_code=409, detail="This suite already has an active stage.")


def _puzzle_suite_missed_puzzle_ids(
    connection,
    suite_id: int,
) -> tuple[int, set[int]] | None:
    for run in list_puzzle_suite_runs(connection, suite_id, limit=200):
        if (
            run.stage != "difficulty"
            or run.settings.get("suite_stage") == "miss_finetuning"
        ):
            continue
        job = get_tool_job(connection, run.job_id)
        if job is None or job.status != "completed":
            continue
        results = list_puzzle_suite_engine_results(connection, run.id)
        searched = {result.puzzle_id for result in results}
        solved = {
            result.puzzle_id for result in results if result.status == "solved"
        }
        return run.id, searched - solved
    return None


def _puzzle_suite_summary_payload(connection, suite) -> dict[str, Any]:
    puzzles = list_puzzle_suite_puzzles(connection, suite.id)
    runs = list_puzzle_suite_runs(connection, suite.id, limit=1)
    active_job = None
    if runs:
        job = get_tool_job(connection, runs[0].job_id)
        if job is not None and job.status in {"queued", "running"}:
            active_job = tool_job_view(connection, job, include_items=False)
    return {
        "id": suite.id,
        "name": suite.name,
        "puzzle_count": len(puzzles),
        "unique_count": sum(puzzle.uniqueness_status == "unique" for puzzle in puzzles),
        "included_count": sum(puzzle.included for puzzle in puzzles),
        "rated_count": sum(puzzle.difficulty_elo is not None for puzzle in puzzles),
        "active_job": active_job,
        "created_at": suite.created_at,
        "updated_at": suite.updated_at,
    }


def _puzzle_suite_detail_payload(connection, suite) -> dict[str, Any]:
    puzzles = list_puzzle_suite_puzzles(connection, suite.id)
    runs = list_puzzle_suite_runs(connection, suite.id)
    run_payloads = []
    latest_difficulty_results: tuple[Any, ...] = ()
    latest_miss_results: tuple[Any, ...] = ()
    difficulty_results_selected = False
    miss_results_selected = False
    miss_selection = _puzzle_suite_missed_puzzle_ids(connection, suite.id)
    source_difficulty_run_id = None if miss_selection is None else miss_selection[0]
    for run in runs:
        job = get_tool_job(connection, run.job_id)
        if job is None:
            continue
        run_payloads.append(
            {
                "id": run.id,
                "stage": run.settings.get("suite_stage", run.stage),
                "rating_list_id": run.rating_list_id,
                "settings": run.settings,
                "created_at": run.created_at,
                "job": tool_job_view(connection, job, include_items=True),
            }
        )
        if (
            run.stage == "difficulty"
            and run.id == source_difficulty_run_id
            and not difficulty_results_selected
        ):
            latest_difficulty_results = list_puzzle_suite_engine_results(connection, run.id)
            difficulty_results_selected = True
        if (
            run.stage == "difficulty"
            and run.settings.get("suite_stage") == "miss_finetuning"
            and not miss_results_selected
            and run.settings.get("source_difficulty_run_id") == source_difficulty_run_id
        ):
            latest_miss_results = list_puzzle_suite_engine_results(connection, run.id)
            miss_results_selected = True
    miss_result_puzzle_ids = {result.puzzle_id for result in latest_miss_results}
    displayed_engine_results = (
        tuple(
            result
            for result in latest_difficulty_results
            if result.puzzle_id not in miss_result_puzzle_ids
        )
        + latest_miss_results
    )
    return {
        **_puzzle_suite_summary_payload(connection, suite),
        "difficulty_run_id": source_difficulty_run_id,
        "miss_count": 0 if miss_selection is None else len(miss_selection[1]),
        "puzzles": [
            {
                "id": puzzle.id,
                "position": puzzle.position,
                "title": puzzle.title,
                "fen": puzzle.fen,
                "solutions": list(puzzle.solutions),
                "included": puzzle.included,
                "uniqueness_status": puzzle.uniqueness_status,
                "verified_solution": puzzle.verified_solution,
                "best_move": puzzle.best_move,
                "second_move": puzzle.second_move,
                "best_sigmoid": puzzle.best_sigmoid,
                "second_sigmoid": puzzle.second_sigmoid,
                "sigmoid_gap": puzzle.sigmoid_gap,
                "uniqueness_depth": puzzle.uniqueness_depth,
                "uniqueness_nodes": puzzle.uniqueness_nodes,
                "uniqueness_time_ms": puzzle.uniqueness_time_ms,
                "uniqueness_error": puzzle.uniqueness_error,
                "difficulty_elo": puzzle.difficulty_elo,
            }
            for puzzle in puzzles
        ],
        "runs": run_payloads,
        "engine_results": [
            {
                "id": result.id,
                "run_id": result.run_id,
                "puzzle_id": result.puzzle_id,
                "engine_id": result.engine_version_id,
                "engine_name": result.engine_name,
                "engine_version": result.engine_version,
                "engine_elo": result.engine_elo,
                "estimate_elo": result.estimate_elo,
                "status": result.status,
                "best_move": result.best_move,
                "solution_nodes": result.solution_nodes,
                "final_nodes": result.final_nodes,
                "depth": result.depth,
                "time_ms": result.time_ms,
                "error": result.error,
            }
            for result in displayed_engine_results
        ],
    }
