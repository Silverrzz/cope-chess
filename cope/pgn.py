from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator, Literal, Mapping, Sequence

import chess

from cope.db import MoveRecord


PgnResultFilter = Literal["win", "draw", "loss"]
PgnColorFilter = Literal["white", "black"]


@dataclass(frozen=True, slots=True)
class PgnExportFilters:
    game_id: int | None = None
    tournament_id: int | None = None
    rating_list_id: int | None = None
    engine_id: int | None = None
    opponent_engine_id: int | None = None
    color: PgnColorFilter | None = None
    result: PgnResultFilter | None = None

    def __post_init__(self) -> None:
        identifiers = (
            self.game_id,
            self.tournament_id,
            self.rating_list_id,
            self.engine_id,
            self.opponent_engine_id,
        )
        if any(value is not None and value <= 0 for value in identifiers):
            raise ValueError("PGN filter identifiers must be positive")
        if self.engine_id is None and any(
            value is not None for value in (self.opponent_engine_id, self.color, self.result)
        ):
            raise ValueError("opponent, color, and result filters require an engine")


@dataclass(frozen=True, slots=True)
class PgnGame:
    id: int
    tournament_id: int
    event: str
    round: int
    game_number: int
    white: str
    black: str
    result: str
    termination: str | None
    opening: str | None
    start_fen: str | None
    started_at: str | None
    time_control: Mapping[str, Any] | None
    moves: Sequence[MoveRecord]
    extra_headers: Mapping[str, Any] | None = None


def render_annotated_pgn(game: PgnGame) -> str:
    board = (
        chess.Board()
        if not game.start_fen or game.start_fen == "startpos"
        else chess.Board(game.start_fen)
    )
    date, utc_time = _date_headers(game.started_at)
    headers = [
        ("Event", game.event),
        ("Site", "COPE Chess"),
        ("Date", date),
        ("Round", str(game.round)),
        ("White", game.white),
        ("Black", game.black),
        ("Result", game.result),
    ]
    if board.fen() != chess.STARTING_FEN:
        headers.extend((("FEN", board.fen()), ("SetUp", "1")))
    headers.extend(
        (
            ("GameId", str(game.id)),
            ("TournamentId", str(game.tournament_id)),
            ("GameNumber", str(game.game_number)),
        )
    )
    if game.termination:
        headers.append(("Termination", game.termination))
    if game.opening:
        headers.append(("Opening", game.opening))
    if date != "????.??.??":
        headers.append(("UTCDate", date))
    if utc_time:
        headers.append(("UTCTime", utc_time))
    time_control = _time_control_header(game.time_control)
    if time_control:
        headers.append(("TimeControl", time_control))
    if game.time_control and game.time_control.get("category") == "movenodes":
        headers.append(("NodeLimit", str(game.time_control.get("nodes", "?"))))
    if game.extra_headers:
        headers.extend((name, value) for name, value in game.extra_headers.items())

    exported_plies = 0
    move_lines: list[str] = []
    move_line: str | None = None
    for move_record in game.moves:
        move = chess.Move.from_uci(move_record.uci)
        if move not in board.legal_moves:
            break
        mover = board.turn
        move_number = board.fullmove_number
        san = board.san(move)
        annotation = _move_annotation(move_record)
        board.push(move)
        if mover == chess.WHITE:
            if move_line is not None:
                move_lines.append(move_line)
            move_line = f"{move_number}. {san} {annotation}"
        else:
            if move_line is None:
                move_line = f"{move_number}... {san} {annotation}"
            else:
                move_line = f"{move_line} {san} {annotation}"
            move_lines.append(move_line)
            move_line = None
        exported_plies += 1

    if move_line is not None:
        move_lines.append(move_line)
    headers.append(("PlyCount", str(exported_plies)))
    header_text = "\n".join(_render_header(name, value) for name, value in headers)
    movetext = "\n".join((*move_lines, game.result))
    return f"{header_text}\n\n{movetext}"


def pgn_export_exists(
    connection: sqlite3.Connection,
    filters: PgnExportFilters,
) -> bool:
    conditions, parameters = _export_conditions(filters)
    row = connection.execute(
        f"""
        SELECT 1
        FROM games game
        JOIN tournaments tournament ON tournament.id = game.tournament_id
        WHERE {' AND '.join(conditions)}
        LIMIT 1
        """,
        parameters,
    ).fetchone()
    return row is not None


def iter_pgn_export(
    connection: sqlite3.Connection,
    filters: PgnExportFilters,
    *,
    batch_size: int = 250,
) -> Iterator[str]:
    after_id = 0
    time_controls: dict[str, Mapping[str, Any] | None] = {}
    while True:
        rows = _list_export_games(connection, filters, after_id=after_id, limit=batch_size)
        if not rows:
            return
        game_ids = tuple(int(row["id"]) for row in rows)
        moves_by_game = _list_export_moves(connection, game_ids)
        connection.commit()
        for row in rows:
            raw_config = str(row["tournament_config"])
            if raw_config not in time_controls:
                time_controls[raw_config] = _time_control_from_config(raw_config)
            game_id = int(row["id"])
            game = PgnGame(
                id=game_id,
                tournament_id=int(row["tournament_id"]),
                event=str(row["event"]),
                round=int(row["round"]),
                game_number=int(row["game_number"]),
                white=_engine_label(row["white_name"], row["white_version"]),
                black=_engine_label(row["black_name"], row["black_version"]),
                result=str(row["result"]),
                termination=row["termination"],
                opening=row["opening_name"],
                start_fen=row["start_fen"],
                started_at=row["started_at"],
                time_control=time_controls[raw_config],
                moves=moves_by_game.get(game_id, ()),
            )
            yield f"{render_annotated_pgn(game)}\n\n"
        after_id = game_ids[-1]


def safe_pgn_filename(value: str, fallback: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return f"{stem or fallback}.pgn"


def _list_export_games(
    connection: sqlite3.Connection,
    filters: PgnExportFilters,
    *,
    after_id: int,
    limit: int,
):
    conditions, parameters = _export_conditions(filters)
    conditions.append("game.id > ?")
    parameters.extend((after_id, limit))
    return connection.execute(
        f"""
        SELECT
          game.id,
          game.tournament_id,
          game.round,
          game.game_number,
          game.result,
          game.termination,
          game.started_at,
          tournament.name AS event,
          tournament.config AS tournament_config,
          white_engine.name AS white_name,
          white_version.version AS white_version,
          black_engine.name AS black_name,
          black_version.version AS black_version,
          opening.name AS opening_name,
          opening.start_fen
        FROM games game
        JOIN tournaments tournament ON tournament.id = game.tournament_id
        JOIN engine_versions white_version ON white_version.id = game.white_engine_id
        JOIN engines white_engine ON white_engine.id = white_version.engine_id
        JOIN engine_versions black_version ON black_version.id = game.black_engine_id
        JOIN engines black_engine ON black_engine.id = black_version.engine_id
        LEFT JOIN openings opening ON opening.id = game.opening_id
        WHERE {' AND '.join(conditions)}
        ORDER BY game.id
        LIMIT ?
        """,
        tuple(parameters),
    ).fetchall()


def _list_export_moves(
    connection: sqlite3.Connection,
    game_ids: Sequence[int],
) -> dict[int, tuple[MoveRecord, ...]]:
    placeholders = ", ".join("?" for _ in game_ids)
    rows = connection.execute(
        f"SELECT * FROM moves WHERE game_id IN ({placeholders}) ORDER BY game_id, ply",
        game_ids,
    )
    collected: dict[int, list[MoveRecord]] = {game_id: [] for game_id in game_ids}
    for row in rows:
        game_id = int(row["game_id"])
        collected[game_id].append(
            MoveRecord(
                game_id=game_id,
                ply=int(row["ply"]),
                uci=str(row["uci"]),
                san=str(row["san"]),
                is_book=bool(row["is_book"]),
                eval_cp=row["eval_cp"],
                eval_mate=row["eval_mate"],
                score_bound=row["score_bound"],
                depth=row["depth"],
                seldepth=row["seldepth"],
                nodes=row["nodes"],
                nps=row["nps"],
                hashfull=row["hashfull"],
                pv=row["pv"],
                info_line=row["info_line"],
                time_ms=int(row["time_ms"]),
                clock_after_ms=int(row["clock_after_ms"]),
                engine_version_id=row["engine_version_id"],
            )
        )
    return {game_id: tuple(moves) for game_id, moves in collected.items()}


def _export_conditions(filters: PgnExportFilters) -> tuple[list[str], list[int | str]]:
    conditions = [
        "game.record_eligible = 1",
        "game.status = 'finished'",
        "game.result IS NOT NULL",
        "tournament.status <> 'draft'",
    ]
    parameters: list[int | str] = []
    if filters.game_id is not None:
        conditions.append("game.id = ?")
        parameters.append(filters.game_id)
    if filters.tournament_id is not None:
        conditions.append("game.tournament_id = ?")
        parameters.append(filters.tournament_id)
    if filters.rating_list_id is not None:
        conditions.append(
            "EXISTS (SELECT 1 FROM rating_list_history history "
            "WHERE history.game_id = game.id AND history.rating_list_id = ?)"
        )
        parameters.append(filters.rating_list_id)
    if filters.engine_id is not None:
        conditions.append("(game.white_engine_id = ? OR game.black_engine_id = ?)")
        parameters.extend((filters.engine_id, filters.engine_id))
    if filters.opponent_engine_id is not None:
        conditions.append(
            "((game.white_engine_id = ? AND game.black_engine_id = ?) "
            "OR (game.white_engine_id = ? AND game.black_engine_id = ?))"
        )
        parameters.extend(
            (
                filters.engine_id,
                filters.opponent_engine_id,
                filters.opponent_engine_id,
                filters.engine_id,
            )
        )
    if filters.color == "white":
        conditions.append("game.white_engine_id = ?")
        parameters.append(filters.engine_id)
    elif filters.color == "black":
        conditions.append("game.black_engine_id = ?")
        parameters.append(filters.engine_id)
    if filters.result == "win":
        conditions.append(
            "((game.result = '1-0' AND game.white_engine_id = ?) "
            "OR (game.result = '0-1' AND game.black_engine_id = ?))"
        )
        parameters.extend((filters.engine_id, filters.engine_id))
    elif filters.result == "draw":
        conditions.append("game.result = '1/2-1/2'")
    elif filters.result == "loss":
        conditions.append(
            "((game.result = '0-1' AND game.white_engine_id = ?) "
            "OR (game.result = '1-0' AND game.black_engine_id = ?))"
        )
        parameters.extend((filters.engine_id, filters.engine_id))
    return conditions, parameters


def _move_annotation(move: MoveRecord) -> str:
    if move.is_book:
        return "{book}"
    if move.eval_mate is not None:
        score = f"{'+' if move.eval_mate >= 0 else '-'}M{abs(move.eval_mate)}"
    else:
        score = f"{(move.eval_cp or 0) / 100:+.2f}"
    depth = move.depth or 0
    engine = f" engine {move.engine_version_id}" if move.engine_version_id is not None else ""
    return f"{{{score}/{depth} {_seconds(move.time_ms)}s{engine}}}"


def _render_header(name: str, value: Any) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    escaped = " ".join(escaped.splitlines())
    return f'[{name} "{escaped}"]'


def _date_headers(value: str | None) -> tuple[str, str | None]:
    if not value:
        return "????.??.??", None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "????.??.??", None
    return parsed.strftime("%Y.%m.%d"), parsed.strftime("%H:%M:%S")


def _time_control_from_config(raw_config: str) -> Mapping[str, Any] | None:
    try:
        config = json.loads(raw_config)
    except (TypeError, json.JSONDecodeError):
        return None
    time_control = config.get("time_control") if isinstance(config, dict) else None
    return time_control if isinstance(time_control, dict) else None


def _time_control_header(time_control: Mapping[str, Any] | None) -> str | None:
    if not time_control:
        return None
    category = time_control.get("category")
    if category == "increment":
        return f"{_seconds(time_control.get('initial_ms'))}+{_seconds(time_control.get('increment_ms'))}"
    if category == "movetime":
        return f"1/{_seconds(time_control.get('move_time_ms'))}"
    if category == "movestogo":
        return f"{time_control.get('moves_to_go')}/{_seconds(time_control.get('initial_ms'))}"
    return None


def _seconds(value: Any) -> str:
    try:
        milliseconds = max(0, int(value))
    except (TypeError, ValueError):
        return "?"
    if milliseconds % 1000 == 0:
        return str(milliseconds // 1000)
    return f"{milliseconds / 1000:.3f}".rstrip("0").rstrip(".")


def _engine_label(name: Any, version: Any) -> str:
    return " ".join(part for part in (str(name or ""), str(version or "")) if part)
