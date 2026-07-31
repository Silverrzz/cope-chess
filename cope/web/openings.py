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
    import chess
    import chess.pgn

    class OpeningVisitor(chess.pgn.BaseVisitor):
        def begin_game(self) -> None:
            self.name = ""
            self.event = ""
            self.moves: list[str] = []
            self.start_fen = ""
            self.board = None

        def visit_header(self, tagname: str, tagvalue: str) -> None:
            value = tagvalue.strip()
            if tagname == "Opening":
                self.name = value
            elif tagname == "Event":
                self.event = value

        def begin_variation(self):
            return chess.pgn.SKIP

        def visit_move(self, board, move) -> None:
            self.moves.append(move.uci())

        def visit_board(self, board) -> None:
            if not self.start_fen:
                self.start_fen = board.fen()
            self.board = board

        def result(self):
            if self.board is None:
                return None
            return self.name, self.event, self.start_fen, tuple(self.moves), self.board.fen()

    openings: list[OpeningLine] = []
    stream = io.StringIO(text)
    while True:
        parsed = chess.pgn.read_game(stream, Visitor=OpeningVisitor)
        if parsed is None:
            break
        name, event, start_fen, moves, fen = parsed
        name = next(
            (value for value in (name, event) if value not in {"", "?", "-"}),
            f"PGN line {len(openings) + 1}",
        )
        openings.append(
            OpeningLine(
                name=name,
                start_fen=start_fen,
                moves=moves,
                fen=fen,
            )
        )
    return openings


def parse_opening_input(text: str, files: UploadedTextFiles) -> list[OpeningLine]:
    openings = parse_openings(text)
    openings.extend(parse_opening_uploads(files))
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
