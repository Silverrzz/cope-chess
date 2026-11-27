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
    resolved: tuple[bool, ...] = ()

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


@dataclass(frozen=True, slots=True)
class Evaluation:
    value: int
    resolved: bool = False


def material(board: SecretQueenBoard, color: chess.Color) -> int:
    native = board._board
    value = sum(price * (len(native.pieces(piece, color)) - len(native.pieces(piece, not color)))
                for piece, price in VALUES.items())
    return value + 800 * (int(board.concealed(color)) - int(board.concealed(not color)))


def concealment(board: SecretQueenBoard, color: chess.Color) -> int:
    balance = material(board, color)
    return (int(board.concealed(color)) * (300 + min(280, max(0, -balance) // 3))
            - int(board.concealed(not color)) * (300 + min(280, max(0, balance) // 3)))


def evaluate(board: SecretQueenBoard, color: chess.Color) -> int:
    native = board._board
    position = 0
    for side in chess.COLORS:
        value = 0
        for piece in (chess.KNIGHT, chess.BISHOP):
            for square in native.pieces(piece, side):
                distance = abs(2 * chess.square_file(square) - 7) + abs(2 * chess.square_rank(square) - 7)
                value += (28 - 4 * distance) if piece == chess.KNIGHT else (14 - 2 * distance)
        for square in native.pieces(chess.PAWN, side):
            rank = chess.square_rank(square) if side else 7 - chess.square_rank(square)
            value += 5 * rank + (8 if chess.square_file(square) in (3, 4) else 0)
        king = native.king(side)
        if king is not None and (native.queens or any(board.concealed(c) for c in chess.COLORS)):
            rank = chess.square_rank(king) if side else 7 - chess.square_rank(king)
            value -= 20 * rank
            if rank == 0 and chess.square_file(king) in (2, 6):
                value += 25
        position += value if side == color else -value
    return material(board, color) + concealment(board, color) + position


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
    if terminal(board, color) is not None:
        return None
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
            value = end if end is not None else evaluate(answered, color) - exposure(answered, cache)
            best = max(best, value)
            if best >= evaluate(board, color):
                break
        worst = best if worst is None else min(worst, best)
    return worst


class TacticalSearch:
    def __init__(self, color: chess.Color, deadline: float, stop: threading.Event, weights=None):
        self.color = color
        self.deadline = deadline
        self.stop = stop
        self.nodes = 0
        self.weights = weights
        self.exchanges = {}

    def _settled(self, board):
        self._tick()
        if not threatened(board):
            return Evaluation(evaluate(board, board.turn) + exposure(board, self.exchanges))
        best = Evaluation(-MATE)
        for move in board.legal_moves:
            self._tick()
            child = board.copy(stack=False)
            child.push(move, record=False)
            end = terminal(child, board.turn)
            value = Evaluation(end, True) if end is not None else Evaluation(evaluate(child, board.turn) - exposure(child, self.exchanges))
            if value.value > best.value:
                best = value
        return best

    def _mean(self, values):
        total = sum(self.weights[index] if self.weights is not None else 1 for index in values)
        if total <= 0:
            return sum(value.value for value in values.values()) / len(values)
        return sum(value.value * (self.weights[index] if self.weights is not None else 1)
                   for index, value in values.items()) / total

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
        return SearchResult(tuple(values[index].value for index in range(len(worlds))), depth, self.nodes,
                            tuple(values[index].resolved for index in range(len(worlds))))

    def _single(self, board, depth, quiet, ply, alpha, beta):
        self._tick()
        end = terminal(board, board.turn, ply)
        if end is not None:
            return Evaluation(end, True)
        urgent = threatened(board)
        stand = evaluate(board, board.turn)
        if depth <= 0 and quiet <= 0:
            return self._settled(board)
        best = Evaluation(-MATE if depth > 0 or urgent else stand)
        if best.value >= beta:
            return best
        alpha = max(alpha, best.value)
        moves = board.legal_moves
        if depth <= 0 and not urgent:
            moves = tuple(move for move in moves if board._board.is_capture(move) or move.promotion)
        for move in sorted(moves, key=lambda move: move_order(board, move), reverse=True):
            child = board.copy(stack=False)
            child.push(move, record=False)
            result = self._single(child, depth - 1, quiet - int(depth <= 0), ply + 1, -beta, -alpha)
            value = Evaluation(-result.value, result.resolved)
            if value.value > best.value:
                best = value
            alpha = max(alpha, best.value)
            if alpha >= beta:
                break
        return best

    def _shared(self, positions, depth, quiet, ply):
        self._tick()
        if len(positions) == 1:
            index, board = positions[0]
            value = self._single(board, depth, quiet, ply, -MATE - 1, MATE + 1)
            return {index: Evaluation(value.value if board.turn == self.color else -value.value, value.resolved)}
        done = {}
        live = []
        for index, board in positions:
            end = terminal(board, self.color, ply)
            if end is None:
                live.append((index, board))
            else:
                done[index] = Evaluation(end, True)
        if not live:
            return done
        values = {index: Evaluation(evaluate(board, self.color)) for index, board in live}
        if depth <= 0 and quiet <= 0:
            for index, board in live:
                value = self._settled(board)
                values[index] = Evaluation(value.value * (1 if board.turn == self.color else -1), value.resolved)
            return {**done, **values}
        own = live[0][1].turn == self.color
        urgent = any(threatened(board) for _, board in live)
        forced = depth > 0 or urgent
        best = {index: Evaluation(-MATE if own else MATE) for index, _ in live} if forced else values
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
            best_mean = self._mean(best)
            for children in moves.values():
                observations = defaultdict(list)
                for index, child in children:
                    observations[child.fen(en_passant="fen")].append((index, child))
                scores = {}
                for observed in observations.values():
                    scores.update(self._shared(tuple(observed), depth - 1, quiet - int(depth <= 0), ply + 1))
                average = self._mean(scores)
                if average > best_mean:
                    best, best_mean = scores, average
        else:
            for children in branches.values():
                scores = self._shared(tuple(children), depth - 1, quiet - int(depth <= 0), ply + 1)
                for index, score in scores.items():
                    if score.value < best[index].value:
                        best[index] = score
        return {**done, **best}
