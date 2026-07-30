from __future__ import annotations

import io
from pathlib import Path

from cope.core.models import OpeningLine


UploadedTextFiles = list[tuple[str, str]]


def format_opening(opening: OpeningLine) -> str:
    prefix = f"{opening.name}; " if opening.name or opening.moves else ""
    suffix = f"; {' '.join(opening.moves)}" if opening.moves else ""
    return f"{prefix}{opening.start_fen}{suffix}"


def parse_openings(text: str) -> list[OpeningLine]:
    import chess

    openings: list[OpeningLine] = []
    for index, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(";")]
        if len(parts) == 1:
            name = ""
            start_fen = parts[0]
            moves: tuple[str, ...] = ()
        else:
            name = parts[0]
            start_fen = parts[1]
            moves = tuple(" ".join(parts[2:]).split())
        try:
            board = chess.Board() if start_fen == "startpos" else chess.Board(start_fen)
            normalized_start_fen = board.fen()
            for uci in moves:
                move = chess.Move.from_uci(uci)
                if move not in board.legal_moves:
                    raise ValueError(f"illegal opening move {uci} at ply {board.ply() + 1}")
                board.push(move)
        except ValueError as exc:
            raise ValueError(f"Position {index}: {exc}") from exc
        openings.append(
            OpeningLine(
                name=name,
                start_fen=normalized_start_fen,
                moves=moves,
                fen=board.fen(),
            )
        )
    return openings


def parse_opening_uploads(files: UploadedTextFiles) -> list[OpeningLine]:
    openings: list[OpeningLine] = []
    for filename, text in files:
        if not text.strip():
            continue
        suffix = Path(filename).suffix.lower()
        if suffix == ".pgn":
            openings.extend(_parse_pgn_openings(text))
        elif suffix == ".epd":
            openings.extend(_parse_epd_openings(text))
        else:
            openings.extend(parse_openings(text))
    return openings


def _parse_pgn_openings(text: str) -> list[OpeningLine]:
    import chess.pgn

    openings: list[OpeningLine] = []
    stream = io.StringIO(text)
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            break
        board = game.board()
        start_fen = board.fen()
        moves: list[str] = []
        for move in game.mainline_moves():
            moves.append(move.uci())
            board.push(move)
        name = next(
            (
                value
                for key in ("Opening", "Event")
                if (value := game.headers.get(key, "").strip()) not in {"?", "-"}
            ),
            f"PGN line {len(openings) + 1}",
        )
        openings.append(
            OpeningLine(
                name=name,
                start_fen=start_fen,
                moves=tuple(moves),
                fen=board.fen(),
            )
        )
    return openings


def _parse_epd_openings(text: str) -> list[OpeningLine]:
    import chess

    openings: list[OpeningLine] = []
    for index, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 4:
            continue
        board = chess.Board(" ".join(fields[:4]) + " 0 1")
        openings.append(
            OpeningLine(
                name=f"EPD {index}",
                start_fen=board.fen(),
                fen=board.fen(),
            )
        )
    return openings
