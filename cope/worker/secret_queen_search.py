from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import threading
import time

import chess

from cope.variants.secret_queen import SecretQueenBoard


MATE = 30000
VALUES = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330, chess.ROOK: 500, chess.QUEEN: 900}


@dataclass(frozen=True, slots=True)
class SearchResult:
    values: tuple[int, ...]
    depth: int
    nodes: int

    @property
    def mean(self) -> float:
        return sum(self.values) / len(self.values)

    @property
    def losses(self) -> int:
        return sum(value <= -MATE + 128 for value in self.values)

    @property
    def wins(self) -> int:
        return sum(value >= MATE - 128 for value in self.values)


class SearchExpired(Exception):
    pass


def material(board: SecretQueenBoard, color: chess.Color) -> int:
    native = board._board
    value = sum(price * (len(native.pieces(piece, color)) - len(native.pieces(piece, not color)))
                for piece, price in VALUES.items())
    return value + 800 * (int(board.concealed(color)) - int(board.concealed(not color)))


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


def threatened(board: SecretQueenBoard) -> bool:
    if board._board.is_check():
        return True
    source = board.concealed_square(not board.turn)
    king = board._board.king(board.turn)
    return (source is not None and king is not None
            and bool(chess.BB_RAYS[source][king])
            and not bool(chess.between(source, king) & board._board.occupied))


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


def mating_reply(board: SecretQueenBoard, color: chess.Color, deadline: float) -> int | None:
    if terminal(board, color) is not None:
        return None
    secret = board.concealed_square(board.turn)
    projected = board._with_queen(secret, board.turn) if secret is not None else board._board
    for move in board.legal_moves:
        if time.monotonic() >= deadline:
            return None
        native = projected if board.reveals(move) else board._board
        if not native.gives_check(move):
            continue
        child = board.copy(stack=False)
        child.push(move, record=False)
        end = terminal(child, color, 2)
        if end is not None and end < 0:
            return end
    return None


def fork_reply(board: SecretQueenBoard, color: chess.Color, deadline: float, stop: threading.Event) -> int | None:
    worst = None
    for reply in board.legal_moves:
        if stop.is_set() or time.monotonic() >= deadline:
            break
        after = board.copy(stack=False)
        after.push(reply, record=False)
        source = reply.to_square
        native = after._with_queen(source, not color) if after.concealed_square(not color) == source else after._board
        attacked = native.attacks_mask(source) & native.occupied_co[color]
        targets = [square for square in chess.scan_forward(attacked)
                   if square == after.concealed_square(color)
                   or native.piece_type_at(square) in (chess.KING, chess.QUEEN, chess.ROOK)]
        if len(targets) < 2 or terminal(after, color) is not None:
            continue
        best = -MATE
        cache = {}
        for response in after.legal_moves:
            if stop.is_set() or time.monotonic() >= deadline:
                return worst
            answered = after.copy(stack=False)
            answered.push(response, record=False)
            end = terminal(answered, color)
            value = end if end is not None else material(answered, color) - exposure(answered, cache)
            best = max(best, value)
            if best >= material(board, color):
                break
        worst = best if worst is None else min(worst, best)
    return worst


class TacticalSearch:
    def __init__(self, color: chess.Color, deadline: float, stop: threading.Event):
        self.color = color
        self.deadline = deadline
        self.stop = stop
        self.nodes = 0

    def _tick(self) -> None:
        self.nodes += 1
        if self.stop.is_set() or time.monotonic() >= self.deadline:
            raise SearchExpired

    def candidate(self, worlds: tuple[SecretQueenBoard, ...], move: chess.Move, depth: int, quiet: int = 4) -> SearchResult:
        positions = []
        for index, world in enumerate(worlds):
            after = world.copy(stack=False)
            after.push(move, record=False)
            positions.append((index, after))
        values = self._shared(tuple(positions), depth, quiet, 1)
        return SearchResult(tuple(values[index] for index in range(len(worlds))), depth, self.nodes)

    def _single(self, board, depth, quiet, ply, alpha, beta):
        self._tick()
        end = terminal(board, board.turn, ply)
        if end is not None:
            return end
        urgent = threatened(board)
        stand = material(board, board.turn)
        if depth <= 0 and quiet <= 0:
            return stand
        best = -MATE if depth > 0 or urgent else stand
        if best >= beta:
            return best
        alpha = max(alpha, best)
        moves = board.legal_moves
        if depth <= 0 and not urgent:
            moves = tuple(move for move in moves if board._board.is_capture(move) or move.promotion)
        for move in sorted(moves, key=lambda move: move_order(board, move), reverse=True):
            child = board.copy(stack=False)
            child.push(move, record=False)
            value = -self._single(child, depth - 1, quiet - int(depth <= 0), ply + 1, -beta, -alpha)
            best = max(best, value)
            alpha = max(alpha, best)
            if alpha >= beta:
                break
        return best

    def _shared(self, positions, depth, quiet, ply):
        self._tick()
        if len(positions) == 1:
            index, board = positions[0]
            value = self._single(board, depth, quiet, ply, -MATE - 1, MATE + 1)
            return {index: value if board.turn == self.color else -value}
        done = {}
        live = []
        for index, board in positions:
            end = terminal(board, self.color, ply)
            if end is None:
                live.append((index, board))
            else:
                done[index] = end
        if not live:
            return done
        values = {index: material(board, self.color) for index, board in live}
        if depth <= 0 and quiet <= 0:
            return {**done, **values}
        own = live[0][1].turn == self.color
        urgent = any(threatened(board) for _, board in live)
        forced = depth > 0 or urgent
        best = {index: (-MATE if own else MATE) for index, _ in live} if forced else values
        branches = defaultdict(list)
        for index, board in live:
            for move in board.legal_moves:
                if depth <= 0 and not urgent and not (board._board.is_capture(move) or move.promotion):
                    continue
                child = board.copy(stack=False)
                child.push(move, record=False)
                key = move.uci() if own else (move.uci(), child.fen(en_passant="fen"))
                branches[key].append((index, child))
        if own:
            moves = defaultdict(list)
            for key, children in branches.items():
                if len(children) == len(live):
                    for index, child in children:
                        moves[key].append((index, child))
            best_mean = sum(best.values())
            for children in moves.values():
                observations = defaultdict(list)
                for index, child in children:
                    observations[child.fen(en_passant="fen")].append((index, child))
                scores = {}
                for observed in observations.values():
                    scores.update(self._shared(tuple(observed), depth - 1, quiet - int(depth <= 0), ply + 1))
                average = sum(scores.values())
                if average > best_mean:
                    best, best_mean = scores, average
        else:
            for children in branches.values():
                scores = self._shared(tuple(children), depth - 1, quiet - int(depth <= 0), ply + 1)
                for index, score in scores.items():
                    best[index] = min(best[index], score)
        return {**done, **best}
