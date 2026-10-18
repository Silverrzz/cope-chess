from __future__ import annotations

import re
from pathlib import Path

from cope.core.models import OpeningLine
from cope.variants import create_board, get_variant


UploadedTextFiles = list[tuple[str, str]]


def format_opening(opening: OpeningLine) -> str:
    prefix = f"{opening.name}; " if opening.name or opening.moves else ""
    suffix = f"; {' '.join(opening.moves)}" if opening.moves else ""
    return f"{prefix}{opening.start_fen}{suffix}"


def parse_openings(text: str, variant: str = "standard") -> list[OpeningLine]:
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
            board = create_board(start_fen, variant=variant)
            if not board.is_valid() or board.is_game_over():
                raise ValueError("Opening must be a valid, non-terminal position")
            normalized_start_fen = board.fen()
            for uci in moves:
                move = board.parse_uci(uci)
                if move not in board.legal_moves:
                    raise ValueError(f"illegal opening move {uci} at ply {board.ply() + 1}")
                board.push(move)
            if board.is_game_over():
                raise ValueError("Opening line ends in a terminal position")
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


def parse_opening_uploads(files: UploadedTextFiles, variant: str = "standard") -> list[OpeningLine]:
    openings: list[OpeningLine] = []
    for filename, text in files:
        if not text.strip():
            continue
        suffix = Path(filename).suffix.lower()
        if suffix == ".pgn":
            openings.extend(_parse_variant_pgn(text, variant))
        elif suffix == ".epd":
            openings.extend(_parse_epd_openings(text, variant))
        else:
            openings.extend(parse_openings(text, variant))
    return openings


def _parse_pgn_openings(text: str) -> list[OpeningLine]:
    return _parse_variant_pgn(text, "standard")


def parse_opening_input(text: str, files: UploadedTextFiles, variant: str = "standard") -> list[OpeningLine]:
    get_variant(variant)
    openings = parse_openings(text, variant)
    openings.extend(parse_opening_uploads(files, variant))
    return openings


def _parse_epd_openings(text: str, variant: str = "standard") -> list[OpeningLine]:
    openings: list[OpeningLine] = []
    for index, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 4:
            raise ValueError(f"EPD {index}: expected four position fields")
        halfmove = re.search(r"\bhmvc\s+(\d+)\s*;", line)
        fullmove = re.search(r"\bfmvn\s+(\d+)\s*;", line)
        board = create_board(" ".join(fields[:4]) + f" {halfmove[1] if halfmove else '0'} {fullmove[1] if fullmove else '1'}", variant=variant)
        if not board.is_valid() or board.is_game_over():
            raise ValueError(f"EPD {index}: opening must be valid and non-terminal")
        openings.append(
            OpeningLine(
                name=f"EPD {index}",
                start_fen=board.fen(),
                fen=board.fen(),
            )
        )
    return openings


def _parse_variant_pgn(text: str, variant: str) -> list[OpeningLine]:
    definition = get_variant(variant)
    headers: dict[str, str] = {}
    turns: list[str] = []
    openings: list[OpeningLine] = []
    depth = 0
    started = False
    token_pattern = re.compile(r'\[\s*\w+\s+"(?:\\.|[^"\\])*"\s*\]|\{[^}]*\}|;[^\n]*|\$\d+|[()]|[^\s(){};$\[]+')

    def finish() -> None:
        nonlocal headers, turns, started
        if not headers and not turns:
            return
        named = headers.get("Variant", "").lower()
        aliases = {"standard": {"", "standard", "chess"}, "dfrc": {"", "chess960", "fischerandom", "dfrc"},
                   "duck": {"", "duck", "duck chess"}, "duck960": {"", "duck960"}}
        if named not in aliases[variant] or headers.get("VariantRules", definition.rules_revision) != definition.rules_revision:
            raise ValueError(f"PGN {len(openings) + 1}: variant does not match {definition.name}")
        board = create_board(headers.get("FEN"), variant=variant)
        if definition.requires_openings and "FEN" not in headers:
            raise ValueError(f"{definition.name} PGN openings require a FEN header")
        if not board.is_valid() or board.is_game_over():
            raise ValueError("PGN opening must be valid and non-terminal")
        start = board.fen()
        moves = []
        for ply, token in enumerate(turns, 1):
            try:
                move = board.parse_san(token)
                moves.append(move.uci())
                board.push(move)
            except ValueError as exc:
                raise ValueError(f"PGN {len(openings) + 1}, turn {ply}: {token}: {exc}") from exc
        if board.is_game_over():
            raise ValueError("PGN opening line ends in a terminal position")
        openings.append(OpeningLine(name=headers.get("Opening") or headers.get("Event") or f"PGN line {len(openings) + 1}",
                                    start_fen=start, moves=tuple(moves), fen=board.fen()))
        headers, turns, started = {}, [], False

    cursor = 0
    for match in token_pattern.finditer(text):
        if text[cursor:match.start()].strip():
            raise ValueError("Malformed PGN input")
        cursor = match.end()
        token = match[0]
        if token.startswith(("{", ";")):
            continue
        if token == "(":
            depth += 1
            continue
        if token == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("Unbalanced PGN variation")
            continue
        if depth:
            continue
        if token.startswith("["):
            if started:
                finish()
            header = re.fullmatch(r'\[\s*(\w+)\s+"((?:\\.|[^"\\])*)"\s*\]', token)
            if header is None:
                raise ValueError("Malformed PGN header")
            headers[header[1]] = re.sub(r'\\(["\\])', r'\1', header[2])
            continue
        token = re.sub(r"^\d+\.(?:\.\.)?", "", token)
        if not token or token == "..." or token.startswith("$"):
            continue
        if token in {"1-0", "0-1", "1/2-1/2", "*"}:
            finish()
            continue
        started = True
        turns.append(token)
    if depth or text[cursor:].strip():
        raise ValueError("Malformed PGN input")
    finish()
    return openings
