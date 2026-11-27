import chess
from cope.variants import Position, SecretQueenBoard
from cope.variants.tak import TAK_START, TakBoard


def setoption_command(name: str, value: str | int | bool) -> str:
    if isinstance(value, bool):
        value = "true" if value else "false"
    return f"setoption name {name} value {value}"


def position_command(board: Position) -> str:
    if isinstance(board, SecretQueenBoard):
        return board.seat_position()
    root = board.root()
    moves = " ".join(move.uci() for move in board.move_stack)

    if isinstance(root, TakBoard):
        tps = root.fen()
        command = "position startpos" if tps == TAK_START else f"position tps {tps}"
    elif root.fen() == chess.STARTING_FEN:
        command = "position startpos"
    else:
        command = f"position fen {root.fen(shredder=board.chess960)}"

    if moves:
        command = f"{command} moves {moves}"

    return command


def go_command(**kwargs: int | None) -> str:
    parts = ["go"]
    for key in ("wtime", "btime", "winc", "binc", "movetime", "movestogo", "nodes"):
        value = kwargs.get(key)
        if value is not None:
            parts.extend([key, str(value)])
    return " ".join(parts)
