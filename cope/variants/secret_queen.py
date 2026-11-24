from __future__ import annotations

from collections import Counter
from copy import copy
import json

import chess


POSITION_PREFIX = "position secretqueen "
SEARCH_WORKERS = 8
FILES = range(8)


class SecretQueenBoard:
    variant = "secret-queen"
    chess960 = False

    def __init__(self, fen: str = chess.STARTING_FEN, *, files: tuple[int | None, int | None] = (None, None)):
        self._board = chess.Board(fen)
        self._start = self._board.fen(en_passant="fen")
        self._files = files
        self._secrets = tuple(
            None if file is None else chess.square(file, 1 if color else 6)
            for color, file in enumerate(files)
        )
        if any(
            square is not None and self._board.piece_at(square) != chess.Piece(chess.PAWN, bool(color))
            for color, square in enumerate(self._secrets)
        ):
            raise ValueError("A secret queen must start on its pawn square")
        self.move_stack: list[chess.Move] = []
        self._history: list[tuple[str, str]] = []
        self._counts = Counter({self._key(): 1})

    @property
    def turn(self) -> chess.Color:
        return self._board.turn

    @property
    def legal_moves(self) -> tuple[chess.Move, ...]:
        return tuple(self.generate_legal_moves())

    def generate_legal_moves(self):
        if self._board.king(self.turn) is None or self._board.king(not self.turn) is None:
            return
        secret = self._secrets[self.turn]
        yield from (move for move in self._board.legal_moves if move.from_square != secret)
        if secret is None:
            return
        projected = self._with_queen(secret, self.turn)
        targets = projected.attacks_mask(secret) & ~projected.occupied_co[self.turn]
        moves = (chess.Move(secret, target) for target in chess.scan_forward(targets))
        yield from (move for move in moves if self._legal_secret(move, projected))

    def is_legal(self, move: chess.Move) -> bool:
        if move.from_square != self._secrets[self.turn]:
            return self._board.is_legal(move)
        return self._legal_secret(move, self._with_queen(move.from_square, self.turn))

    def _legal_secret(self, move: chess.Move, projected: chess.Board) -> bool:
        if move.promotion is not None or move.drop is not None or not move:
            return False
        target = chess.BB_SQUARES[move.to_square]
        if not projected.attacks_mask(move.from_square) & target or projected.occupied_co[self.turn] & target:
            return False
        if move.to_square == self._board.king(not self.turn):
            return True
        after = projected.copy(stack=False)
        after.push(move)
        king = after.king(self.turn)
        return king is not None and not after.is_attacked_by(not self.turn, king)

    def secret_capture(self) -> chess.Move | None:
        source = self._secrets[self.turn]
        target = self._board.king(not self.turn)
        if source is None or target is None:
            return None
        rows = chess.square_rank(source) - chess.square_rank(target)
        files = chess.square_file(source) - chess.square_file(target)
        if rows and files and abs(rows) != abs(files):
            return None
        if chess.between(source, target) & self._board.occupied:
            return None
        return chess.Move(source, target)

    def reveals(self, move: chess.Move) -> bool:
        if move.from_square != self._secrets[self.turn]:
            return False
        target_rank = chess.square_rank(move.to_square)
        if target_rank == (7 if self.turn else 0):
            return True
        delta = move.to_square - move.from_square
        forward = 8 if self.turn else -8
        target = self._board.piece_at(move.to_square)
        if delta == forward and target is None:
            return False
        if delta == 2 * forward and target is None and chess.square_rank(move.from_square) == (1 if self.turn else 6):
            return self._board.piece_at(move.from_square + forward) is not None
        adjacent = abs(chess.square_file(move.to_square) - chess.square_file(move.from_square)) == 1
        return not (adjacent and delta in (forward - 1, forward + 1) and target is not None and target.color != self.turn)

    def push(self, move: chess.Move) -> None:
        mover = self.turn
        secrets = list(self._secrets)
        moving_secret = move.from_square == secrets[mover]
        reveal = moving_secret and self.reveals(move)
        victim = move.to_square
        if not moving_secret and self._board.is_en_passant(move):
            victim += -8 if mover else 8
        if victim == secrets[not mover]:
            secrets[not mover] = None
        if moving_secret:
            secrets[mover] = None if reveal else move.to_square
        if reveal:
            self._board = self._with_queen(move.from_square, mover)
        self._board.push(move)
        self._board.clear_stack()
        self._secrets = tuple(secrets)
        self.move_stack.append(move)
        self._history.append((move.uci(), self.fen(en_passant="fen")))
        self._counts[self._key()] += 1

    def _with_queen(self, square: chess.Square, color: chess.Color) -> chess.Board:
        board = self._board.copy(stack=False)
        board.set_piece_at(square, chess.Piece(chess.QUEEN, color))
        return board

    def projection(self, *, show: tuple[chess.Color, ...]) -> chess.Board | None:
        board = self._board.copy(stack=False)
        for color in show:
            square = self._secrets[color]
            if square is not None:
                board.set_piece_at(square, chess.Piece(chess.QUEEN, color))
        if board.ep_square is not None:
            victim = board.ep_square + (-8 if board.turn else 8)
            if board.piece_at(victim) != chess.Piece(chess.PAWN, not board.turn) or not board.has_legal_en_passant():
                board.ep_square = None
        if not board.is_valid():
            return None
        return board

    def concealed(self, color: chess.Color) -> bool:
        return self._secrets[color] is not None

    def _key(self) -> tuple[str, ...]:
        fen = self.fen(en_passant="fen").split()
        if self._board.ep_square is not None and not any(
            move.from_square != self._secrets[self.turn] for move in self._board.generate_legal_ep()
        ):
            fen[3] = "-"
        return (*fen[:4], *("-" if square is None else chess.square_name(square) for square in self._secrets))

    def outcome(self) -> chess.Outcome | None:
        if self._board.king(self.turn) is None:
            return chess.Outcome(chess.Termination.VARIANT_LOSS, not self.turn)
        if self.secret_capture() is not None:
            return None
        if next(self.generate_legal_moves(), None) is None:
            return chess.Outcome(chess.Termination.CHECKMATE, not self.turn) if self._board.is_check() else chess.Outcome(chess.Termination.STALEMATE, None)
        if self._board.occupied == self._board.kings:
            return chess.Outcome(chess.Termination.INSUFFICIENT_MATERIAL, None)
        if self._counts[self._key()] >= 3:
            return chess.Outcome(chess.Termination.THREEFOLD_REPETITION, None)
        if self._board.halfmove_clock >= 100:
            return chess.Outcome(chess.Termination.FIFTY_MOVES, None)
        return None

    def has_insufficient_material(self, color: chess.Color) -> bool:
        return self._board.occupied_co[color] == self._board.kings & self._board.occupied_co[color]

    def fen(self, **kwargs) -> str:
        return self._board.fen(**kwargs)

    def ply(self) -> int:
        return self._board.ply()

    @property
    def fullmove_number(self) -> int:
        return self._board.fullmove_number

    def root(self) -> SecretQueenBoard:
        return SecretQueenBoard(self._start, files=self._files)

    def copy(self, *, stack: bool = True) -> SecretQueenBoard:
        board = copy(self)
        board._board = self._board.copy(stack=False)
        board.move_stack = self.move_stack.copy() if stack else []
        board._history = self._history.copy() if stack else []
        board._counts = self._counts.copy()
        return board

    def parse_uci(self, text: str) -> chess.Move:
        move = chess.Move.from_uci(text)
        if not self.is_legal(move):
            raise chess.IllegalMoveError(f"Illegal Secret Queen move: {text}")
        return move

    def push_uci(self, text: str) -> chess.Move:
        move = self.parse_uci(text)
        self.push(move)
        return move

    def san(self, move: chess.Move) -> str:
        if self.reveals(move) or move == self.secret_capture():
            capture = self._board.piece_at(move.to_square) is not None
            return f"Q{chess.square_name(move.from_square)}{'x' if capture else '-'}{chess.square_name(move.to_square)}{'#' if move == self.secret_capture() else ''}"
        return self._board.san(move)

    def seat_position(self) -> str:
        return POSITION_PREFIX + json.dumps({"color": self.turn, "file": self._files[self.turn], "moves": self._history}, separators=(",", ":"))

    def native_position(self) -> str:
        native = chess.Board(self._start)
        start = self._start
        suffix = []
        for uci, fen in self._history:
            move = chess.Move.from_uci(uci)
            if native.is_legal(move):
                native.push(move)
                if native.fen(en_passant="fen") == fen:
                    suffix.append(uci)
                    continue
            native = chess.Board(fen)
            start = native.fen()
            suffix.clear()
        return f"position fen {start}" + (" moves " + " ".join(suffix) if suffix else "")

    @classmethod
    def beliefs(cls, color: chess.Color, own_file: int, history: list[tuple[str, str]]) -> tuple[SecretQueenBoard, ...]:
        worlds = []
        actions = [(chess.Move.from_uci(move), fen) for move, fen in history]
        for opposing_file in FILES:
            files = (opposing_file, own_file) if color else (own_file, opposing_file)
            world = cls(files=files)
            for move, fen in actions:
                if not world.is_legal(move):
                    break
                world.push(move)
                if world.fen(en_passant="fen") != fen or world.outcome() is not None:
                    break
            else:
                if world.turn == color:
                    worlds.append(world)
        return tuple(worlds)
