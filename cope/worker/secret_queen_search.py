from __future__ import annotations

import chess

from cope.variants.secret_queen import SecretQueenBoard


MATE = 30000
VALUES = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330, chess.ROOK: 500, chess.QUEEN: 900}


def material(board: SecretQueenBoard, color: chess.Color) -> int:
    native = board._board
    value = sum(price * (len(native.pieces(piece, color)) - len(native.pieces(piece, not color)))
                for piece, price in VALUES.items())
    return value + 800 * (int(board.concealed(color)) - int(board.concealed(not color)))


def concealment(board: SecretQueenBoard, color: chess.Color, survivors=None) -> int:
    balance = material(board, color)
    hidden = board._secrets if survivors is None else survivors
    return (int(hidden[color] is not None) * (300 + min(280, max(0, -balance) // 3))
            - int(hidden[not color] is not None) * (300 + min(280, max(0, balance) // 3)))


def terminal(board: SecretQueenBoard, color: chess.Color, ply: int = 0) -> int | None:
    if board._board.king(color) is None:
        return -MATE + ply
    if board._board.king(not color) is None:
        return MATE - ply
    if board.secret_capture() is not None:
        return MATE - ply if board.turn == color else -MATE + ply
    outcome = board.outcome()
    if outcome is not None:
        return 0 if outcome.winner is None else MATE - ply if outcome.winner == color else -MATE + ply
    return None


def move_order(board: SecretQueenBoard, move: chess.Move) -> int:
    native = board._board
    victim = move.to_square
    if native.is_en_passant(move) and move.from_square != board.concealed_square(board.turn):
        victim += -8 if board.turn else 8
    gain = 900 if victim == board.concealed_square(not board.turn) else VALUES.get(native.piece_type_at(victim), 0)
    mover = 900 if move.from_square == board.concealed_square(board.turn) else VALUES.get(native.piece_type_at(move.from_square), 0)
    return 16 * gain - mover + (VALUES.get(move.promotion, 0) if move.promotion else 0)


def exchange(board: SecretQueenBoard, square: chess.Square, cache: dict | None = None) -> int:
    if cache is None:
        cache = {}
    key = board._key(), square
    if key in cache:
        return cache[key]
    before = material(board, board.turn)
    best = 0
    for move in board.captures_of(square):
        child = board.copy(stack=False)
        child.push(move, record=False)
        gain = material(child, board.turn) - before
        best = max(best, gain - exchange(child, move.to_square, cache))
    cache[key] = best
    return best


def exposure(board: SecretQueenBoard, cache: dict) -> int:
    targets = set()
    for move in board.legal_moves:
        victim = move.to_square
        if board._board.piece_at(victim) is None:
            if move.from_square == board.concealed_square(board.turn) or not board._board.is_en_passant(move):
                continue
            victim += -8 if board.turn else 8
        targets.add(victim)
    return max((exchange(board, square, cache) for square in targets), default=0)
