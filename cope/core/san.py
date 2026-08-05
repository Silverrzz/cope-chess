import chess

# Convert a PV string to SAN notation, given an optional root FEN position.
def pv_to_san(pv: str | None, root_fen: str | None = None, *, numbered: bool = True) -> str | None:
    if not pv or root_fen is None:
        return pv
    try:
        board = chess.Board(root_fen) if root_fen and root_fen != "startpos" else chess.Board()
    except ValueError:
        return pv

    tokens = pv.split()
    out: list[str] = []
    for index, token in enumerate(tokens):
        try:
            move = chess.Move.from_uci(token)
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
