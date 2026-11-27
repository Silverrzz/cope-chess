from urllib.parse import quote

import chess
import chess.svg
import resvg_py


def parse_fen(value: str) -> tuple[str, chess.Board]:
    fen = " ".join(value.split())
    if len(fen) > 200 or len(fen.split()) != 6:
        raise ValueError("Enter a FEN with all six fields.")
    try:
        board = chess.Board(fen)
    except ValueError as error:
        raise ValueError("Invalid FEN. Check the position and its six fields.") from error
    return fen, board


def position_png(board: chess.Board) -> bytes:
    svg = chess.svg.board(
        board, size=600,
        colors={"square light": "#f0d9b5", "square dark": "#b58863"},
    )
    return resvg_py.svg_to_bytes(svg_string=svg)


def position_text(fen: str, image_url: str | None = None) -> str:
    position = quote(fen.replace(" ", "_"), safe="/")
    links = [
        f"[Lichess](<https://lichess.org/analysis/{position}>)",
        f"[ChessDB](<https://www.chessdb.cn/queryc_en/?{position}>)",
    ]
    if image_url:
        links.append(f"[Image](<{image_url}>)")
    return f"```\n{fen}\n```\n" + " | ".join(links)
