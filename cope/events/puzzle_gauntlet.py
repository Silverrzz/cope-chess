from __future__ import annotations

import json
import re
import secrets
import sqlite3
from io import StringIO
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import chess
import chess.pgn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from cope.core.models import (
    AdjudicationConfig,
    MoveTimeControl,
    RoundRobinFormatOptions,
    TournamentConfig,
    TournamentFormat,
)
from cope.db import (
    create_event,
    create_event_cast_member,
    create_chat_message,
    create_game,
    create_opening_suite,
    create_tournament,
    get_common_benchmark_reference,
    get_engine,
    get_event,
    get_event_by_slug,
    get_tournament,
    list_engine_records,
    set_event_published,
    set_event_status,
    set_tournament_status,
)
from cope.db.events import EventRecord, utc_now

from .registry import EventModule, register_event_module


MODULE_KEY = "puzzle-gauntlet"
MODULE_VERSION = 1
PUZZLE_GAUNTLET_TRANSITION_MS = 2000
_UCI_MOVE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$", re.IGNORECASE)
_CHEER_CLIENT = re.compile(r"^[A-Za-z0-9-]{16,64}$")


class PuzzleInput(BaseModel):
    fen: str = Field(min_length=1, max_length=200)
    solutions: list[str] = Field(min_length=1, max_length=20)
    title: str = Field(default="", max_length=120)

    @field_validator("fen", "title")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("solutions")
    @classmethod
    def strip_solutions(cls, value: list[str]) -> list[str]:
        result = [item.strip() for item in value if item.strip()]
        if not result:
            raise ValueError("add at least one solution move")
        return result


class PuzzleBulkInput(BaseModel):
    puzzles: list[PuzzleInput] = Field(min_length=1, max_length=5000)


class PuzzleOrderInput(BaseModel):
    puzzle_ids: list[int] = Field(min_length=1, max_length=5000)


class PuzzleDeleteInput(BaseModel):
    puzzle_ids: list[int] = Field(min_length=1, max_length=5000)

    @field_validator("puzzle_ids")
    @classmethod
    def validate_puzzle_ids(cls, value: list[int]) -> list[int]:
        if any(puzzle_id <= 0 for puzzle_id in value):
            raise ValueError("puzzle ids must be positive")
        if len(set(value)) != len(value):
            raise ValueError("puzzle ids must be unique")
        return value


class GauntletEntryInput(BaseModel):
    engine_id: int = Field(gt=0)


class GauntletSettingsInput(BaseModel):
    start_time_ms: int = Field(default=30000, ge=100, le=3_600_000)
    decrement_ms: int = Field(default=2000, ge=0, le=3_600_000)
    minimum_time_ms: int = Field(default=5000, ge=100, le=3_600_000)
    threads: int = Field(default=1, gt=0, le=1024)
    hash_mb: int = Field(default=256, gt=0, le=1_048_576)
    scheduled_start_at: datetime | None = None

    @field_validator("scheduled_start_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("scheduled start time must include a timezone")
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_timing(self) -> GauntletSettingsInput:
        if self.minimum_time_ms > self.start_time_ms:
            raise ValueError("minimum time cannot exceed the opening puzzle time")
        return self


class GauntletVisibilityInput(BaseModel):
    published: bool


class GauntletActionInput(BaseModel):
    action: Literal["pause", "resume", "abort"]


class GauntletCheerPayload(BaseModel):
    side: Literal["left", "right"]


def _required_event(connection: sqlite3.Connection, event_id: int) -> EventRecord:
    event = get_event(connection, event_id)
    if event is None or event.handler_key != MODULE_KEY or event.handler_version != MODULE_VERSION:
        raise HTTPException(status_code=404, detail="Puzzle Gauntlet event not found.")
    return event


def _config_row(connection: sqlite3.Connection, event_id: int):
    row = connection.execute(
        "SELECT * FROM puzzle_gauntlet_events WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Puzzle Gauntlet configuration not found.")
    return row


def _puzzle_rows(connection: sqlite3.Connection, event_id: int):
    return tuple(
        connection.execute(
            "SELECT * FROM puzzle_gauntlet_puzzles WHERE event_id = ? ORDER BY position, id",
            (event_id,),
        )
    )


def _replace_puzzle_order(connection: sqlite3.Connection, puzzles: tuple[Any, ...]) -> None:
    if not puzzles:
        return
    opening = connection.execute(
        "SELECT suite_id FROM openings WHERE id = ?",
        (int(puzzles[0]["opening_id"]),),
    ).fetchone()
    opening_max = connection.execute(
        "SELECT COALESCE(MAX(position), -1) AS position FROM openings WHERE suite_id = ?",
        (int(opening["suite_id"]),),
    ).fetchone()
    temporary_base = max(
        max(int(puzzle["position"]) for puzzle in puzzles),
        int(opening_max["position"]),
    ) + 1
    for offset, puzzle in enumerate(puzzles):
        connection.execute(
            "UPDATE puzzle_gauntlet_puzzles SET position = ? WHERE id = ?",
            (temporary_base + offset, int(puzzle["id"])),
        )
        connection.execute(
            "UPDATE openings SET position = ? WHERE id = ?",
            (temporary_base + offset, int(puzzle["opening_id"])),
        )
    for position, puzzle in enumerate(puzzles):
        connection.execute(
            "UPDATE puzzle_gauntlet_puzzles SET position = ? WHERE id = ?",
            (position, int(puzzle["id"])),
        )
        connection.execute(
            "UPDATE openings SET position = ? WHERE id = ?",
            (position + 1, int(puzzle["opening_id"])),
        )


def _entry_rows(connection: sqlite3.Connection, event_id: int):
    return tuple(
        connection.execute(
            """
            SELECT member.*, engine.name AS engine_name, version.version AS engine_version,
                   engine.author, version.source_kind
            FROM event_cast_members member
            JOIN engine_versions version ON version.id = member.engine_version_id
            JOIN engines engine ON engine.id = version.engine_id
            WHERE member.event_id = ? AND member.kind = 'engine' AND member.parent_id IS NULL
            ORDER BY member.position, member.id
            """,
            (event_id,),
        )
    )


def _active_entries(connection: sqlite3.Connection, event_id: int):
    return tuple(row for row in _entry_rows(connection, event_id) if row["status"] == "active")


def puzzle_gauntlet_engine_ids_for_tournament(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> tuple[int, ...]:
    row = connection.execute(
        "SELECT event_id FROM puzzle_gauntlet_events WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchone()
    if row is None:
        return ()
    return tuple(
        int(entry["engine_version_id"])
        for entry in _active_entries(connection, int(row["event_id"]))
    )


def _normalize_puzzle(payload: PuzzleInput) -> tuple[str, list[str]]:
    try:
        board = chess.Board(payload.fen)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid FEN: {exc}") from exc
    if board.is_game_over() or not any(board.legal_moves):
        raise HTTPException(status_code=422, detail="The puzzle position must have a legal move.")
    solutions: list[str] = []
    for raw in payload.solutions:
        try:
            if _UCI_MOVE.fullmatch(raw):
                move = chess.Move.from_uci(raw.lower())
                if move not in board.legal_moves:
                    raise ValueError
            else:
                move = board.parse_san(raw)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"{raw!r} is not a legal solution move.") from exc
        uci = move.uci()
        if uci not in solutions:
            solutions.append(uci)
    return board.fen(), solutions


def _insert_puzzle(
    connection: sqlite3.Connection,
    event_id: int,
    suite_id: int,
    payload: PuzzleInput,
    position: int,
) -> int:
    fen, solutions = _normalize_puzzle(payload)
    opening = connection.execute(
        """
        INSERT INTO openings (suite_id, position, name, start_fen, moves, fen)
        VALUES (?, ?, ?, ?, '[]', ?)
        RETURNING id
        """,
        (suite_id, position + 1, payload.title or f"Puzzle {position + 1}", fen, fen),
    ).fetchone()
    if opening is None:
        raise RuntimeError("puzzle opening was not created")
    puzzle = connection.execute(
        """
        INSERT INTO puzzle_gauntlet_puzzles (event_id, opening_id, position, title, fen, solutions)
        VALUES (?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (event_id, int(opening["id"]), position, payload.title, fen, json.dumps(solutions)),
    ).fetchone()
    if puzzle is None:
        raise RuntimeError("puzzle was not created")
    return int(puzzle["id"])


def _attempt_rows(
    connection: sqlite3.Connection,
    event_id: int,
    puzzle_id: int | None,
) -> dict[int, Any]:
    if puzzle_id is None:
        return {}
    return {
        int(row["cast_member_id"]): row
        for row in connection.execute(
            """
            SELECT attempt.*, game.status AS game_status, game.started_at, game.finished_at,
                   move.uci AS recorded_move_uci, move.time_ms AS recorded_elapsed_ms
            FROM puzzle_gauntlet_attempts attempt
            JOIN games game ON game.id = attempt.game_id
            LEFT JOIN LATERAL (
              SELECT uci, time_ms FROM moves
              WHERE game_id = attempt.game_id AND is_book = 0
              ORDER BY ply LIMIT 1
            ) move ON TRUE
            WHERE attempt.event_id = ?
              AND attempt.puzzle_id = ?
            """,
            (event_id, puzzle_id),
        )
    }


def _time_for_position(config: Any, position: int) -> int:
    return max(
        int(config["minimum_time_ms"]),
        int(config["start_time_ms"]) - int(config["decrement_ms"]) * position,
    )


def _payload(connection: sqlite3.Connection, event: EventRecord, *, admin: bool) -> dict[str, Any]:
    config = _config_row(connection, event.id)
    puzzles = _puzzle_rows(connection, event.id)
    state = event.state or {}
    current_puzzle_id = state.get("current_puzzle_id")
    attempts = _attempt_rows(
        connection,
        event.id,
        int(current_puzzle_id) if current_puzzle_id is not None else None,
    )
    entries = []
    for row in _entry_rows(connection, event.id):
        attempt = attempts.get(int(row["id"]))
        entries.append(
            {
                "id": int(row["id"]),
                "engine_id": int(row["engine_version_id"]),
                "name": row["engine_name"],
                "version": row["engine_version"],
                "display_name": row["display_name"],
                "author": row["author"],
                "status": row["status"],
                "position": int(row["position"]),
                "winner": int(row["id"]) in state.get("winner_ids", []),
                "attempt": None
                if attempt is None
                else {
                    "id": int(attempt["id"]),
                    "game_id": int(attempt["game_id"]),
                    "game_status": attempt["game_status"],
                    "outcome": attempt["outcome"],
                    "move_uci": attempt["move_uci"] or attempt["recorded_move_uci"],
                    "elapsed_ms": (
                        attempt["elapsed_ms"]
                        if attempt["elapsed_ms"] is not None
                        else attempt["recorded_elapsed_ms"]
                    ),
                    "started_at": attempt["started_at"],
                    "finished_at": attempt["finished_at"],
                },
            }
        )
    available_puzzles = []
    for row in puzzles:
        item = {
            "id": int(row["id"]),
            "position": int(row["position"]),
            "title": row["title"],
            "fen": row["fen"],
            "time_limit_ms": _time_for_position(config, int(row["position"])),
            "completed": any(int(item.get("puzzle_id", 0)) == int(row["id"]) for item in state.get("rounds", [])),
        }
        if admin:
            item["solutions"] = json.loads(row["solutions"])
        available_puzzles.append(item)
    current_puzzle = next(
        (item for item in available_puzzles if item["id"] == current_puzzle_id),
        None,
    )
    if current_puzzle is not None and "solutions" not in current_puzzle:
        current_row = next(row for row in puzzles if int(row["id"]) == current_puzzle_id)
        current_puzzle["solutions"] = json.loads(current_row["solutions"])
    transition = state.get("transition")
    next_puzzle_id = (
        int(transition["next_puzzle_id"])
        if isinstance(transition, dict) and transition.get("next_puzzle_id") is not None
        else None
    )
    next_puzzle = next(
        (item for item in available_puzzles if item["id"] == next_puzzle_id),
        None,
    )
    tournament = None
    worker = None
    if config["tournament_id"] is not None:
        record = get_tournament(connection, int(config["tournament_id"]))
        if record is not None:
            tournament = {
                "id": record.id,
                "status": record.status,
                "current_round": record.current_round,
                "scheduled_start_at": record.scheduled_start_at,
                "started_at": record.started_at,
                "finished_at": record.finished_at,
            }
            if admin and record.status in {"scheduled", "running", "paused"}:
                worker_row = connection.execute(
                    """
                    SELECT workers.id, workers.label, workers.status, claims.claimed_at,
                           EXISTS (
                             SELECT 1
                             FROM games game
                             JOIN game_assignments assignment ON assignment.game_id = game.id
                             WHERE game.tournament_id = claims.tournament_id
                               AND assignment.worker_id = claims.worker_id
                               AND assignment.status IN ('acked', 'live')
                           ) AS prepared
                    FROM event_fixture_workers claims
                    JOIN workers ON workers.id = claims.worker_id
                    WHERE claims.tournament_id = ?
                    """,
                    (record.id,),
                ).fetchone()
                if worker_row is not None:
                    worker = dict(worker_row)
    result: dict[str, Any] = {
        "format": MODULE_KEY,
        "phase": state.get("phase", "countdown"),
        "puzzle_count": len(puzzles),
        "puzzles": available_puzzles if admin else [],
        "entries": entries,
        "current_puzzle": current_puzzle,
        "next_puzzle": next_puzzle,
        "transition": transition,
        "rounds": state.get("rounds", []),
        "winner_ids": state.get("winner_ids", []),
        "tournament": tournament,
        "worker": worker,
        "settings": {
            "start_time_ms": int(config["start_time_ms"]),
            "decrement_ms": int(config["decrement_ms"]),
            "minimum_time_ms": int(config["minimum_time_ms"]),
            "threads": int(config["threads"]),
            "hash_mb": int(config["hash_mb"]),
            "scheduled_start_at": event.scheduled_start_at,
        },
    }
    if admin:
        result["engine_options"] = [
            {
                "id": engine.id,
                "engine_id": engine.engine_id,
                "name": engine.name,
                "version": engine.version,
                "author": engine.author,
                "source_kind": engine.source_kind,
            }
            for engine in list_engine_records(connection)
            if engine.active and engine.engine_active
        ]
    return result


def _public_payload(connection: sqlite3.Connection, event: EventRecord) -> dict[str, Any]:
    return _payload(connection, event, admin=False)


def _admin_payload(connection: sqlite3.Connection, event: EventRecord) -> dict[str, Any]:
    return _payload(connection, event, admin=True)


def _touch_event(connection: sqlite3.Connection, event_id: int) -> None:
    connection.execute(
        "UPDATE events SET revision = revision + 1, updated_at = ? WHERE id = ?",
        (utc_now(), event_id),
    )


def _publish_change(request: Request, event_id: int) -> None:
    request.app.state.stream_hub.publish_to_internal(
        "runner.wake",
        {"reason": f"puzzle-gauntlet-event:{event_id}"},
    )
    request.app.state.stream_hub.publish(
        f"event.{event_id}",
        "event.changed",
        {"event_id": event_id},
        source="web",
    )
    request.app.state.stream_hub.publish(
        "admin",
        "admin.changed",
        {"event_id": event_id},
        source="web",
    )


def _json(payload: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(jsonable_encoder(payload), status_code=status_code)


def _schedule_round(
    connection: sqlite3.Connection,
    event_id: int,
    tournament_id: int,
    puzzle: Any,
    entries: tuple[Any, ...],
) -> None:
    round_number = int(puzzle["position"]) + 1
    for pair_index, entry in enumerate(entries, start=1):
        engine_id = int(entry["engine_version_id"])
        game_id = create_game(
            connection,
            tournament_id=tournament_id,
            round=round_number,
            pair_index=pair_index,
            white_engine_id=engine_id,
            black_engine_id=engine_id,
            opening_id=int(puzzle["opening_id"]),
            record_eligible=False,
        )
        connection.execute(
            """
            INSERT INTO puzzle_gauntlet_attempts
              (event_id, puzzle_id, cast_member_id, game_id)
            VALUES (?, ?, ?, ?)
            """,
            (event_id, int(puzzle["id"]), int(entry["id"]), game_id),
        )


def _set_event_state(connection: sqlite3.Connection, event_id: int, state: dict[str, Any]) -> None:
    connection.execute(
        "UPDATE events SET state = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
        (json.dumps(state, separators=(",", ":")), utc_now(), event_id),
    )


def release_puzzle_gauntlet_round(
    connection: sqlite3.Connection,
    tournament_id: int,
    puzzle_id: int,
) -> str:
    row = connection.execute(
        """
        SELECT event.id, event.status, event.state, tournament.status AS tournament_status
        FROM puzzle_gauntlet_events gauntlet
        JOIN events event ON event.id = gauntlet.event_id
        JOIN tournaments tournament ON tournament.id = gauntlet.tournament_id
        WHERE gauntlet.tournament_id = ?
        FOR UPDATE OF event
        """,
        (tournament_id,),
    ).fetchone()
    if (
        row is None
        or row["status"] not in {"live", "scheduled"}
        or row["tournament_status"] != "running"
    ):
        return "stopped"
    state = json.loads(row["state"] or "{}")
    if (
        row["status"] == "scheduled"
        and state.get("phase") == "countdown"
        and int(state.get("current_puzzle_id", 0)) == puzzle_id
    ):
        state.update({"phase": "live", "transition": None})
        _set_event_state(connection, int(row["id"]), state)
        set_event_status(connection, int(row["id"]), "live")
        return "ready"
    if state.get("phase") == "live" and int(state.get("current_puzzle_id", 0)) == puzzle_id:
        return "ready"
    transition = state.get("transition")
    if (
        state.get("phase") != "intermission"
        or not isinstance(transition, dict)
        or int(transition.get("next_puzzle_id", 0)) != puzzle_id
    ):
        return "stopped"
    starts_at = datetime.fromisoformat(str(transition["starts_at"]))
    if datetime.now(UTC) < starts_at:
        return "waiting"
    state.update(
        {
            "phase": "live",
            "current_puzzle_id": puzzle_id,
            "transition": None,
        }
    )
    _set_event_state(connection, int(row["id"]), state)
    return "ready"


def advance_puzzle_gauntlet(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> str | None:
    config = connection.execute(
        "SELECT * FROM puzzle_gauntlet_events WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchone()
    if config is None:
        return None
    event = get_event(connection, int(config["event_id"]))
    tournament = get_tournament(connection, tournament_id)
    if event is None or tournament is None:
        return None
    puzzle = connection.execute(
        """
        SELECT puzzle.*
        FROM puzzle_gauntlet_puzzles puzzle
        JOIN puzzle_gauntlet_attempts attempt ON attempt.puzzle_id = puzzle.id
        WHERE puzzle.event_id = ?
        ORDER BY puzzle.position DESC LIMIT 1
        """,
        (event.id,),
    ).fetchone()
    if puzzle is None:
        return None
    attempts = tuple(
        connection.execute(
            """
            SELECT attempt.*, member.engine_version_id,
                   move.uci, move.time_ms, game.pgn
            FROM puzzle_gauntlet_attempts attempt
            JOIN event_cast_members member ON member.id = attempt.cast_member_id
            JOIN games game ON game.id = attempt.game_id
            LEFT JOIN LATERAL (
              SELECT uci, time_ms FROM moves
              WHERE game_id = attempt.game_id AND is_book = 0
              ORDER BY ply LIMIT 1
            ) move ON TRUE
            WHERE attempt.puzzle_id = ?
            ORDER BY attempt.id
            """,
            (int(puzzle["id"]),),
        )
    )
    attempt_moves = {
        int(row["id"]): _attempt_move_uci(row)
        for row in attempts
    }
    solutions = {str(move).lower() for move in json.loads(puzzle["solutions"])}
    correct_ids = {
        int(row["cast_member_id"])
        for row in attempts
        if attempt_moves[int(row["id"])] in solutions
    }
    void_round = not correct_ids
    for row in attempts:
        member_id = int(row["cast_member_id"])
        outcome = "saved" if void_round else "correct" if member_id in correct_ids else "incorrect"
        connection.execute(
            """
            UPDATE puzzle_gauntlet_attempts
            SET outcome = ?, move_uci = ?, elapsed_ms = ?
            WHERE id = ?
            """,
            (outcome, attempt_moves[int(row["id"])], row["time_ms"], int(row["id"])),
        )
        if outcome == "incorrect":
            metadata = {
                "eliminated_puzzle_id": int(puzzle["id"]),
                "eliminated_round": int(puzzle["position"]) + 1,
            }
            connection.execute(
                "UPDATE event_cast_members SET status = 'eliminated', metadata = ? WHERE id = ?",
                (json.dumps(metadata, separators=(",", ":")), member_id),
            )
    state = dict(event.state or {})
    rounds = list(state.get("rounds", []))
    rounds.append(
        {
            "puzzle_id": int(puzzle["id"]),
            "position": int(puzzle["position"]),
            "title": puzzle["title"],
            "fen": puzzle["fen"],
            "solutions": sorted(solutions),
            "completed_at": utc_now(),
            "void": void_round,
            "correct_ids": sorted(correct_ids),
            "eliminated_ids": []
            if void_round
            else sorted(int(row["cast_member_id"]) for row in attempts if int(row["cast_member_id"]) not in correct_ids),
        }
    )
    puzzle_number = int(puzzle["position"]) + 1
    if void_round:
        create_chat_message(
            connection,
            event_id=event.id,
            display_name="System",
            text=f"Puzzle {puzzle_number}: every engine missed. Nobody was knocked out.",
        )
    else:
        create_chat_message(
            connection,
            event_id=event.id,
            display_name="System",
            text=f"Puzzle {puzzle_number} complete. {len(correct_ids)} engine{'s' if len(correct_ids) != 1 else ''} found the solution.",
        )
        entry_names = {int(row["id"]): str(row["engine_name"]) for row in _entry_rows(connection, event.id)}
        eliminated_ids = sorted(int(row["cast_member_id"]) for row in attempts if int(row["cast_member_id"]) not in correct_ids)
        for member_id in eliminated_ids:
            create_chat_message(
                connection,
                event_id=event.id,
                display_name="System",
                text=f"{entry_names.get(member_id, f'Engine {member_id}')} was knocked out on puzzle {puzzle_number}.",
            )
    active = _active_entries(connection, event.id)
    puzzles = _puzzle_rows(connection, event.id)
    last_puzzle = int(puzzle["position"]) >= len(puzzles) - 1
    if len(active) <= 1 or last_puzzle:
        winner_ids = [int(row["id"]) for row in active]
        state.update(
            {
                "phase": "completed",
                "current_puzzle_id": int(puzzle["id"]),
                "rounds": rounds,
                "winner_ids": winner_ids,
            }
        )
        _set_event_state(connection, event.id, state)
        set_event_status(connection, event.id, "completed")
        return "completed"
    next_puzzle = puzzles[int(puzzle["position"]) + 1]
    next_time_ms = _time_for_position(config, int(next_puzzle["position"]))
    config_data = tournament.config.model_dump(mode="json")
    config_data["time_control"] = {"category": "movetime", "move_time_ms": next_time_ms}
    updated_config = TournamentConfig.model_validate(config_data)
    connection.execute(
        "UPDATE tournaments SET config = ? WHERE id = ?",
        (updated_config.model_dump_json(), tournament_id),
    )
    _schedule_round(connection, event.id, tournament_id, next_puzzle, active)
    transition_started = datetime.now(UTC)
    state.update(
        {
            "phase": "intermission",
            "current_puzzle_id": int(puzzle["id"]),
            "transition": {
                "completed_puzzle_id": int(puzzle["id"]),
                "next_puzzle_id": int(next_puzzle["id"]),
                "started_at": transition_started.isoformat(timespec="milliseconds"),
                "starts_at": (
                    transition_started
                    + timedelta(milliseconds=PUZZLE_GAUNTLET_TRANSITION_MS)
                ).isoformat(timespec="milliseconds"),
            },
            "rounds": rounds,
            "winner_ids": [],
        }
    )
    _set_event_state(connection, event.id, state)
    return "advanced"


def _attempt_move_uci(row: Any) -> str | None:
    if row["uci"] is not None:
        return str(row["uci"]).lower()
    if not row["pgn"]:
        return None
    try:
        game = chess.pgn.read_game(StringIO(str(row["pgn"])))
        move = None if game is None else next(iter(game.mainline_moves()), None)
    except (UnicodeError, ValueError):
        return None
    return None if move is None else move.uci().lower()


def reset_puzzle_gauntlet(connection: sqlite3.Connection, event_id: int) -> None:
    row = connection.execute(
        "SELECT tournament_id FROM puzzle_gauntlet_events WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if row is None:
        return
    connection.execute("DELETE FROM puzzle_gauntlet_attempts WHERE event_id = ?", (event_id,))
    connection.execute(
        "UPDATE puzzle_gauntlet_events SET tournament_id = NULL WHERE event_id = ?",
        (event_id,),
    )
    connection.execute(
        "UPDATE event_cast_members SET status = 'active', metadata = '{}' WHERE event_id = ? AND kind = 'engine'",
        (event_id,),
    )


def _register_api(app: FastAPI) -> None:
    from cope.web import app as web_app

    @app.get("/api/events/{slug}/puzzle-gauntlet")
    def public_gauntlet(
        slug: str,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        event = get_event_by_slug(connection, slug)
        if event is None or event.handler_key != MODULE_KEY or event.handler_version != MODULE_VERSION:
            raise HTTPException(status_code=404, detail="Puzzle Gauntlet event not found.")
        if not web_app._event_is_public(event) and not web_app._admin_request_authenticated(request):
            raise HTTPException(status_code=401, detail="Admin session required.")
        return _json(_public_payload(connection, event))

    @app.post("/api/events/{slug}/puzzle-gauntlet/cheers")
    def cheer_for_gauntlet(
        slug: str,
        payload: GauntletCheerPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        hub = request.app.state.stream_hub
        client_host = request.client.host if request.client is not None else "unknown"
        supplied_client = request.headers.get("x-cope-cheer-client", "")
        client_key = supplied_client if _CHEER_CLIENT.fullmatch(supplied_client) else client_host
        if not hub.allow_ephemeral(
            f"cheer-host:{slug}:{client_host}",
            rate=12.0,
            burst=24,
        ):
            raise HTTPException(status_code=429, detail="Cheer rate limit exceeded.")
        if not hub.allow_ephemeral(
            f"cheer-client:{slug}:{client_host}:{client_key}",
            rate=4.0,
            burst=8,
        ):
            raise HTTPException(status_code=429, detail="Cheer rate limit exceeded.")
        event = get_event_by_slug(connection, slug)
        if event is None or event.handler_key != MODULE_KEY or event.handler_version != MODULE_VERSION:
            raise HTTPException(status_code=404, detail="Puzzle Gauntlet event not found.")
        if not web_app._event_is_public(event) and not web_app._admin_request_authenticated(request):
            raise HTTPException(status_code=401, detail="Admin session required.")
        if not hub.allow_ephemeral(
            f"cheer-event:{event.id}",
            rate=20.0,
            burst=30,
        ):
            raise HTTPException(status_code=429, detail="The crowd is cheering too quickly.")
        cheer = {
            "id": secrets.token_hex(8),
            "event_id": event.id,
            "side": payload.side,
        }
        hub.publish(
            f"event.{event.id}",
            "event.cheer",
            cheer,
            source="web",
            ephemeral=True,
        )
        return _json({"accepted": True, "cheer_id": cheer["id"]}, 202)

    @app.post("/api/admin/events/{event_id}/puzzle-gauntlet/puzzles")
    def add_puzzle(
        event_id: int,
        payload: PuzzleInput,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        config = _config_row(connection, event_id)
        if config["tournament_id"] is not None:
            raise HTTPException(status_code=409, detail="Puzzles are locked after the gauntlet is armed.")
        position = len(_puzzle_rows(connection, event_id))
        puzzle_id = _insert_puzzle(connection, event_id, int(config["opening_suite_id"]), payload, position)
        _touch_event(connection, event_id)
        connection.commit()
        _publish_change(request, event_id)
        return _json({"id": puzzle_id, "message": "Puzzle added."}, 201)

    @app.post("/api/admin/events/{event_id}/puzzle-gauntlet/puzzles/bulk")
    def add_puzzles_bulk(
        event_id: int,
        payload: PuzzleBulkInput,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        config = _config_row(connection, event_id)
        if config["tournament_id"] is not None:
            raise HTTPException(status_code=409, detail="Puzzles are locked after the gauntlet is armed.")
        start = len(_puzzle_rows(connection, event_id))
        ids = [
            _insert_puzzle(connection, event_id, int(config["opening_suite_id"]), puzzle, start + offset)
            for offset, puzzle in enumerate(payload.puzzles)
        ]
        _touch_event(connection, event_id)
        connection.commit()
        _publish_change(request, event_id)
        return _json({"ids": ids, "message": f"Added {len(ids)} puzzles."}, 201)

    @app.put("/api/admin/events/{event_id}/puzzle-gauntlet/puzzles/{puzzle_id}")
    def update_puzzle(
        event_id: int,
        puzzle_id: int,
        payload: PuzzleInput,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        config = _config_row(connection, event_id)
        if config["tournament_id"] is not None:
            raise HTTPException(status_code=409, detail="Puzzles are locked after the gauntlet is armed.")
        row = connection.execute(
            "SELECT * FROM puzzle_gauntlet_puzzles WHERE id = ? AND event_id = ?",
            (puzzle_id, event_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Puzzle not found.")
        fen, solutions = _normalize_puzzle(payload)
        connection.execute(
            "UPDATE puzzle_gauntlet_puzzles SET title = ?, fen = ?, solutions = ? WHERE id = ?",
            (payload.title, fen, json.dumps(solutions), puzzle_id),
        )
        connection.execute(
            "UPDATE openings SET name = ?, start_fen = ?, fen = ? WHERE id = ?",
            (payload.title or f"Puzzle {int(row['position']) + 1}", fen, fen, int(row["opening_id"])),
        )
        _touch_event(connection, event_id)
        connection.commit()
        _publish_change(request, event_id)
        return _json({"message": "Puzzle updated."})

    @app.delete("/api/admin/events/{event_id}/puzzle-gauntlet/puzzles/{puzzle_id}")
    def delete_puzzle(
        event_id: int,
        puzzle_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        config = _config_row(connection, event_id)
        if config["tournament_id"] is not None:
            raise HTTPException(status_code=409, detail="Puzzles are locked after the gauntlet is armed.")
        row = connection.execute(
            "SELECT opening_id FROM puzzle_gauntlet_puzzles WHERE id = ? AND event_id = ?",
            (puzzle_id, event_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Puzzle not found.")
        connection.execute("DELETE FROM openings WHERE id = ?", (int(row["opening_id"]),))
        remaining = _puzzle_rows(connection, event_id)
        _replace_puzzle_order(connection, remaining)
        _touch_event(connection, event_id)
        connection.commit()
        _publish_change(request, event_id)
        return _json({"message": "Puzzle removed."})

    @app.delete("/api/admin/events/{event_id}/puzzle-gauntlet/puzzles")
    def delete_puzzles(
        event_id: int,
        payload: PuzzleDeleteInput,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        config = _config_row(connection, event_id)
        if config["tournament_id"] is not None:
            raise HTTPException(status_code=409, detail="Puzzles are locked after the gauntlet is armed.")
        placeholders = ", ".join("?" for _ in payload.puzzle_ids)
        rows = tuple(
            connection.execute(
                f"""SELECT id, opening_id FROM puzzle_gauntlet_puzzles
                    WHERE event_id = ? AND id IN ({placeholders})""",
                (event_id, *payload.puzzle_ids),
            )
        )
        if len(rows) != len(payload.puzzle_ids):
            raise HTTPException(status_code=404, detail="One or more puzzles were not found.")
        opening_ids = tuple(int(row["opening_id"]) for row in rows)
        opening_placeholders = ", ".join("?" for _ in opening_ids)
        connection.execute(
            f"DELETE FROM openings WHERE id IN ({opening_placeholders})",
            opening_ids,
        )
        remaining = _puzzle_rows(connection, event_id)
        _replace_puzzle_order(connection, remaining)
        _touch_event(connection, event_id)
        connection.commit()
        _publish_change(request, event_id)
        return _json({"message": f"Removed {len(rows)} puzzles."})

    @app.put("/api/admin/events/{event_id}/puzzle-gauntlet/puzzle-order")
    def order_puzzles(
        event_id: int,
        payload: PuzzleOrderInput,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        config = _config_row(connection, event_id)
        if config["tournament_id"] is not None:
            raise HTTPException(status_code=409, detail="Puzzles are locked after the gauntlet is armed.")
        rows = _puzzle_rows(connection, event_id)
        by_id = {int(row["id"]): row for row in rows}
        if set(payload.puzzle_ids) != set(by_id) or len(payload.puzzle_ids) != len(by_id):
            raise HTTPException(status_code=422, detail="Puzzle order must include every puzzle exactly once.")
        _replace_puzzle_order(
            connection,
            tuple(by_id[puzzle_id] for puzzle_id in payload.puzzle_ids),
        )
        _touch_event(connection, event_id)
        connection.commit()
        _publish_change(request, event_id)
        return _json({"message": "Puzzle order saved."})

    @app.post("/api/admin/events/{event_id}/puzzle-gauntlet/entries")
    def add_entry(
        event_id: int,
        payload: GauntletEntryInput,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        config = _config_row(connection, event_id)
        if config["tournament_id"] is not None:
            raise HTTPException(status_code=409, detail="The field is locked after the gauntlet is armed.")
        engine = get_engine(connection, payload.engine_id)
        if engine is None:
            raise HTTPException(status_code=422, detail="Select an available engine version.")
        if any(int(row["engine_version_id"]) == payload.engine_id for row in _entry_rows(connection, event_id)):
            raise HTTPException(status_code=409, detail="That engine is already in the gauntlet.")
        position = len(_entry_rows(connection, event_id))
        member_id = create_event_cast_member(
            connection,
            event_id,
            member_key=f"gauntlet-engine-{payload.engine_id}",
            kind="engine",
            display_name=" ".join((engine.name, engine.version)),
            short_name=engine.name[:20],
            role="gauntlet contender",
            engine_version_id=payload.engine_id,
            position=position,
        )
        _touch_event(connection, event_id)
        connection.commit()
        _publish_change(request, event_id)
        return _json({"id": member_id, "message": "Engine entered."}, 201)

    @app.delete("/api/admin/events/{event_id}/puzzle-gauntlet/entries/{member_id}")
    def delete_entry(
        event_id: int,
        member_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        config = _config_row(connection, event_id)
        if config["tournament_id"] is not None:
            raise HTTPException(status_code=409, detail="The field is locked after the gauntlet is armed.")
        row = connection.execute(
            "SELECT id FROM event_cast_members WHERE id = ? AND event_id = ? AND kind = 'engine'",
            (member_id, event_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Gauntlet engine not found.")
        connection.execute("DELETE FROM event_cast_members WHERE id = ?", (member_id,))
        _touch_event(connection, event_id)
        connection.commit()
        _publish_change(request, event_id)
        return _json({"message": "Engine removed."})

    @app.put("/api/admin/events/{event_id}/puzzle-gauntlet/settings")
    def update_settings(
        event_id: int,
        payload: GauntletSettingsInput,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        event = _required_event(connection, event_id)
        config = _config_row(connection, event_id)
        if config["tournament_id"] is not None:
            raise HTTPException(status_code=409, detail="Timing is locked after the gauntlet is armed.")
        scheduled = None if payload.scheduled_start_at is None else payload.scheduled_start_at.isoformat(timespec="seconds")
        connection.execute(
            """
            UPDATE puzzle_gauntlet_events
            SET start_time_ms = ?, decrement_ms = ?, minimum_time_ms = ?, threads = ?, hash_mb = ?
            WHERE event_id = ?
            """,
            (
                payload.start_time_ms,
                payload.decrement_ms,
                payload.minimum_time_ms,
                payload.threads,
                payload.hash_mb,
                event_id,
            ),
        )
        connection.execute(
            "UPDATE events SET scheduled_start_at = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
            (scheduled, utc_now(), event_id),
        )
        if event.published_at is not None and event.status in {"announced", "scheduled"}:
            set_event_status(connection, event_id, "scheduled" if scheduled else "announced")
        connection.commit()
        _publish_change(request, event_id)
        return _json({"message": "Gauntlet settings saved."})

    @app.put("/api/admin/events/{event_id}/puzzle-gauntlet/visibility")
    def update_visibility(
        event_id: int,
        payload: GauntletVisibilityInput,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        event = _required_event(connection, event_id)
        if payload.published and event.status == "draft":
            set_event_status(connection, event_id, "scheduled" if event.scheduled_start_at else "announced")
        set_event_published(connection, event_id, payload.published)
        connection.commit()
        _publish_change(request, event_id)
        return _json({"published": payload.published, "message": "Visibility saved."})

    @app.post("/api/admin/events/{event_id}/puzzle-gauntlet/start")
    def start_gauntlet(
        event_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        event = _required_event(connection, event_id)
        config = _config_row(connection, event_id)
        if config["tournament_id"] is not None:
            raise HTTPException(status_code=409, detail="The gauntlet is already armed.")
        puzzles = _puzzle_rows(connection, event_id)
        entries = _active_entries(connection, event_id)
        if not puzzles:
            raise HTTPException(status_code=409, detail="Add at least one puzzle before starting.")
        if len(entries) < 2:
            raise HTTPException(status_code=409, detail="Enter at least two engines before starting.")
        engine_ids = [int(row["engine_version_id"]) for row in entries]
        for engine_id in engine_ids:
            engine = get_engine(connection, engine_id)
            if engine is None or (
                engine.distribution == "managed"
                and get_common_benchmark_reference(connection, (engine,)) is None
            ):
                raise HTTPException(
                    status_code=409,
                    detail=f"Engine version {engine_id} needs a completed benchmark before entering the gauntlet.",
                )
        now = datetime.now(UTC)
        scheduled = None if event.scheduled_start_at is None else datetime.fromisoformat(event.scheduled_start_at)
        future_start = scheduled is not None and scheduled > now
        tournament_config = TournamentConfig(
            format=TournamentFormat.ROUND_ROBIN,
            format_options=RoundRobinFormatOptions(cycles=1),
            participants=engine_ids,
            time_control=MoveTimeControl(move_time_ms=int(config["start_time_ms"])),
            concurrency=len(engine_ids),
            opening_suite_id=int(config["opening_suite_id"]),
            adjudication=AdjudicationConfig(max_moves=1),
            rated=False,
            lag_compensation_ms=0,
            engine_threads=int(config["threads"]),
            engine_hash_mb=int(config["hash_mb"]),
        )
        tournament_id = create_tournament(
            connection,
            f"{event.title} runtime",
            tournament_config,
            status="scheduled" if future_start else "running",
            scheduled_start_at=event.scheduled_start_at if future_start else None,
        )
        _schedule_round(connection, event_id, tournament_id, puzzles[0], entries)
        connection.execute(
            "UPDATE puzzle_gauntlet_events SET tournament_id = ? WHERE event_id = ?",
            (tournament_id, event_id),
        )
        if not future_start:
            set_tournament_status(connection, tournament_id, "running")
        state = {
            "phase": "countdown" if future_start else "live",
            "current_puzzle_id": int(puzzles[0]["id"]),
            "rounds": [],
            "winner_ids": [],
        }
        _set_event_state(connection, event_id, state)
        set_event_status(connection, event_id, "scheduled" if future_start else "live")
        connection.commit()
        _publish_change(request, event_id)
        return _json(
            {
                "tournament_id": tournament_id,
                "status": "scheduled" if future_start else "live",
                "message": "Gauntlet armed." if future_start else "Puzzle Gauntlet started.",
            }
        )

    @app.post("/api/admin/events/{event_id}/puzzle-gauntlet/action")
    def gauntlet_action(
        event_id: int,
        payload: GauntletActionInput,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        config = _config_row(connection, event_id)
        if config["tournament_id"] is None:
            raise HTTPException(status_code=409, detail="The gauntlet has not started.")
        tournament_id = int(config["tournament_id"])
        tournament = get_tournament(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="Gauntlet runtime not found.")
        if payload.action == "pause":
            if tournament.status != "running":
                raise HTTPException(status_code=409, detail="Only a live gauntlet can be paused.")
            set_tournament_status(connection, tournament_id, "paused")
            set_event_status(connection, event_id, "intermission")
        elif payload.action == "resume":
            if tournament.status != "paused":
                raise HTTPException(status_code=409, detail="Only a paused gauntlet can resume.")
            set_tournament_status(connection, tournament_id, "running")
            set_event_status(connection, event_id, "live")
        else:
            if tournament.status not in {"scheduled", "running", "paused"}:
                raise HTTPException(status_code=409, detail="This gauntlet can no longer be aborted.")
            set_tournament_status(connection, tournament_id, "aborted")
            set_event_status(connection, event_id, "cancelled")
            current = get_event(connection, event_id)
            state = dict(current.state if current is not None else {})
            state.update({"phase": "completed", "winner_ids": []})
            _set_event_state(connection, event_id, state)
        connection.commit()
        _publish_change(request, event_id)
        return _json({"status": payload.action, "message": f"Gauntlet {payload.action} applied."})


def _provision(connection: sqlite3.Connection) -> int:
    event_id = create_event(
        connection,
        slug="puzzle-gauntlet",
        handler_key=MODULE_KEY,
        handler_version=MODULE_VERSION,
        title="Puzzle Gauntlet",
        subtitle="Every engine. Every puzzle. One shared clock.",
        summary="Every engine faces the same tactical positions as the clock tightens round after round.",
        description="A live engine survival event built around shared tactical tests, visible calculation, and a steadily shrinking clock.",
        rules="Every active engine searches the same position independently. Engines that miss the accepted move are eliminated. If every engine misses, the round is void and everyone survives. A sole survivor wins immediately; if the puzzle list is exhausted, every remaining engine wins.",
        status="draft",
        featured=True,
        theme={
            "primary": "#8b5cf6",
            "accent": "#22d3ee",
            "background": "#090713",
            "surface": "#151126",
            "text": "#f8f4ff",
        },
        config={"module": MODULE_KEY},
        state={"phase": "countdown", "rounds": [], "winner_ids": []},
    )
    suite_id = create_opening_suite(
        connection,
        name=f"Puzzle Gauntlet #{event_id}",
        description="Puzzle Gauntlet runtime positions",
    )
    connection.execute(
        "INSERT INTO puzzle_gauntlet_events (event_id, opening_suite_id) VALUES (?, ?)",
        (event_id, suite_id),
    )
    return event_id


register_event_module(
    EventModule(
        key=MODULE_KEY,
        label="Puzzle Gauntlet",
        version=MODULE_VERSION,
        provision=_provision,
        public_payload=_public_payload,
        admin_payload=_admin_payload,
        register_api=_register_api,
    )
)
