import chess
from cope.variants import create_board

# Convert a PV string to SAN notation, given an optional root FEN position.
def pv_to_san(pv: str | None, root_fen: str | None = None, *, numbered: bool = True, variant: str = "dfrc") -> str | None:
    if not pv or root_fen is None:
        return pv
    try:
        board = create_board(root_fen, variant=variant)
    except ValueError:
        return pv

    tokens = pv.split()
    out: list[str] = []
    for index, token in enumerate(tokens):
        try:
            move = board.parse_uci(token)
        except ValueError:
            out.extend(tokens[index:])  
            break
        if not board.is_legal(move):
            out.extend(tokens[index:])
            break
        san = board.san(move)
        if numbered and board.turn == chess.WHITE:
            out.append(f"{board.fullmove_number}. {san}")
        elif numbered and not out:
            out.append(f"{board.fullmove_number}... {san}")
        else:
            out.append(san)
        board.push(move)
    return " ".join(out)
