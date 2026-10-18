from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import re

import chess


@dataclass(frozen=True, slots=True)
class DuckTurn:
    from_square: int
    to_square: int
    promotion: str = ""
    duck_square: int | None = None

    @classmethod
    def from_uci(cls, text: str) -> DuckTurn:
        match = re.fullmatch(r"([a-h][1-8])([a-h][1-8])([nbrqNBRQ]?)(?:,([a-h][1-8])([a-h][1-8]))?", text)
        if match is None or match[1] == match[2] or match[4] is not None and match[4] != match[2]:
            raise ValueError(f"Malformed duck turn: {text}")
        return cls(chess.parse_square(match[1]), chess.parse_square(match[2]), match[3].lower(),
                   None if match[5] is None else chess.parse_square(match[5]))

    def uci(self) -> str:
        destination = chess.square_name(self.to_square)
        text = chess.square_name(self.from_square) + destination + self.promotion
        return text if self.duck_square is None else text + "," + destination + chess.square_name(self.duck_square)

    def __str__(self) -> str:
        return self.uci()


class DuckLegalMoves:
    def __init__(self, board: DuckBoard):
        self.board = board

    def __contains__(self, turn: object) -> bool:
        return isinstance(turn, DuckTurn) and self.board.is_legal(turn)

    def __iter__(self):
        if self.board._missing_king():
            return
        for source, piece in enumerate(self.board.squares):
            if not piece or piece.isupper() != self.board.turn:
                continue
            for target in range(64):
                promotions = "nbrq" if piece.lower() == "p" and target // 8 in (0, 7) else ("",)
                for promotion in promotions:
                    move = DuckTurn(source, target, promotion)
                    if not self.board._piece_legal(move):
                        continue
                    if self.board.squares[target].lower() == "k":
                        yield move
                    for duck in range(64):
                        move = DuckTurn(source, target, promotion, duck)
                        if self.board.is_legal(move):
                            yield move


class DuckBoard:
    def __init__(self, fen: str = chess.STARTING_FEN, *, chess960: bool = False):
        self.chess960 = chess960
        self.variant = "duck960" if chess960 else "duck"
        self.move_stack: list[DuckTurn] = []
        self._set_fen(fen)
        self._root_fen = self.fen()
        self._positions = Counter({self.repetition_key(): 1})

    def _set_fen(self, fen: str) -> None:
        fields = fen.split()
        if len(fields) == 4:
            fields += ["0", "1"]
        if len(fields) != 6 or fields[1] not in ("w", "b"):
            raise ValueError("Expected a six-field duck FEN")
        self.squares = [""] * 64
        self.duck_square = None
        ranks = fields[0].split("/")
        if len(ranks) != 8:
            raise ValueError("Duck FEN must contain eight ranks")
        for index, rank in enumerate(ranks):
            file = 0
            for char in rank:
                if char in "12345678":
                    file += int(char)
                elif char in "PNBRQKpnbrqk*" and file < 8:
                    square = (7 - index) * 8 + file
                    if char == "*":
                        if self.duck_square is not None:
                            raise ValueError("Duck FEN contains multiple ducks")
                        self.duck_square = square
                    else:
                        if char.lower() == "p" and index in (0, 7):
                            raise ValueError("Unpromoted pawn on the last rank")
                        self.squares[square] = char
                    file += 1
                else:
                    raise ValueError("Invalid duck FEN placement")
            if file != 8:
                raise ValueError("Invalid duck FEN rank width")
        kings = (self.squares.count("K"), self.squares.count("k"))
        if max(kings) > 1 or sum(kings) == 0:
            raise ValueError("Invalid duck FEN kings")
        self.turn = fields[1] == "w"
        self.rooks: dict[int, int] = {}
        if fields[2] != "-":
            for right in fields[2]:
                white = right.isupper()
                base = 0 if white else 56
                king = next((sq for sq in range(base, base + 8) if self.squares[sq] == ("K" if white else "k")), None)
                if king is None:
                    raise ValueError("Castling king is missing")
                rook_piece = "R" if white else "r"
                if not self.chess960:
                    if right not in "KQkq" or king != base + 4:
                        raise ValueError("Invalid castling rights")
                    rook = base + (7 if right.lower() == "k" else 0)
                elif right.lower() in "kq":
                    files = range(7, king % 8, -1) if right.lower() == "k" else range(king % 8)
                    rook = next((base + f for f in files if self.squares[base + f] == rook_piece), None)
                elif right.lower() in "abcdefgh":
                    rook = base + ord(right.lower()) - ord("a")
                else:
                    raise ValueError("Invalid Chess960 castling rights")
                if rook is None or rook == king or self.squares[rook] != rook_piece:
                    raise ValueError("Castling rook is missing")
                side = (0 if white else 2) + int(rook < king)
                if side in self.rooks:
                    raise ValueError("Duplicate castling right")
                self.rooks[side] = rook
        self.ep_square = None if fields[3] == "-" else chess.parse_square(fields[3])
        if self.ep_square is not None:
            ep = self.ep_square
            direction = -8 if self.turn else 8
            if ep // 8 != (5 if self.turn else 2) or self.squares[ep] or self.squares[ep + direction] != ("p" if self.turn else "P") or self.squares[ep - direction]:
                raise ValueError("Invalid en-passant square")
        if not re.fullmatch(r"\d+", fields[4]) or not re.fullmatch(r"\d+", fields[5]):
            raise ValueError("Invalid move counters")
        self.halfmove_clock, self.fullmove_number = int(fields[4]), int(fields[5])
        if not 0 <= self.halfmove_clock <= 1_000_000_000 or not 1 <= self.fullmove_number <= 1_000_000_000:
            raise ValueError("Invalid move counters")

    @property
    def legal_moves(self) -> DuckLegalMoves:
        return DuckLegalMoves(self)

    def _missing_king(self) -> bool:
        return "K" not in self.squares or "k" not in self.squares

    def _castle(self, move: DuckTurn) -> int | None:
        if self.squares[move.from_square] != ("K" if self.turn else "k"):
            return None
        base = 0 if self.turn else 56
        if move.from_square // 8 != base // 8:
            return None
        for side, rook in self.rooks.items():
            if (side < 2) == self.turn and move.to_square == (rook if self.chess960 else base + (2 if side % 2 else 6)):
                return side
        return None

    def _piece_legal(self, move: DuckTurn) -> bool:
        source, target = move.from_square, move.to_square
        if not 0 <= source < 64 or not 0 <= target < 64 or source == target or target == self.duck_square:
            return False
        piece, captured = self.squares[source], self.squares[target]
        if not piece or piece.isupper() != self.turn:
            return False
        castle = self._castle(move)
        if castle is not None:
            base = 0 if self.turn else 56
            king_to, rook_to = base + (2 if castle % 2 else 6), base + (3 if castle % 2 else 5)
            rook = self.rooks[castle]
            if move.promotion or self.squares[rook] != ("R" if self.turn else "r"):
                return False
            return all(sq != self.duck_square and (sq in (source, rook) or not self.squares[sq])
                       for start, end in ((source, king_to), (rook, rook_to))
                       for sq in range(min(start, end), max(start, end) + 1))
        if captured and captured.isupper() == self.turn:
            return False
        kind = piece.lower()
        promoting = kind == "p" and target // 8 == (7 if self.turn else 0)
        if promoting != bool(move.promotion) or move.promotion and move.promotion not in "nbrq":
            return False
        dx, dy = target % 8 - source % 8, target // 8 - source // 8
        ax, ay = abs(dx), abs(dy)
        if kind == "p":
            direction = 1 if self.turn else -1
            if ax == 1 and dy == direction:
                return bool(captured) or target == self.ep_square and self.squares[target - 8 * direction] == ("p" if self.turn else "P")
            if dx or captured:
                return False
            if dy == direction:
                return True
            return dy == 2 * direction and source // 8 == (1 if self.turn else 6) and not self.squares[source + 8 * direction] and source + 8 * direction != self.duck_square
        if kind == "n":
            return ax * ay == 2
        if kind == "k":
            return max(ax, ay) == 1
        if kind == "b" and ax != ay or kind == "r" and dx != 0 and dy != 0 or kind == "q" and ax != ay and dx != 0 and dy != 0:
            return False
        step = (1 if dx > 0 else -1 if dx < 0 else 0) + 8 * (1 if dy > 0 else -1 if dy < 0 else 0)
        return all(not self.squares[sq] and sq != self.duck_square for sq in range(source + step, target, step))

    def is_legal(self, move: DuckTurn) -> bool:
        if not isinstance(move, DuckTurn) or self._missing_king() or not self._piece_legal(move):
            return False
        duck = move.duck_square
        if duck is None:
            return self.squares[move.to_square].lower() == "k"
        if duck == self.duck_square or not 0 <= duck < 64:
            return False
        castle = self._castle(move)
        if castle is not None:
            base = 0 if self.turn else 56
            if duck in (base + (2 if castle % 2 else 6), base + (3 if castle % 2 else 5)):
                return False
            return duck in (move.from_square, self.rooks[castle]) or not self.squares[duck]
        if duck == move.from_square:
            return True
        if duck == move.to_square:
            return False
        if self.squares[move.from_square].lower() == "p" and move.to_square == self.ep_square and not self.squares[move.to_square] and move.from_square % 8 != move.to_square % 8 and duck == move.to_square + (-8 if self.turn else 8):
            return True
        return not self.squares[duck]

    def parse_uci(self, text: str) -> DuckTurn:
        move = DuckTurn.from_uci(text)
        if not self.is_legal(move):
            raise ValueError(f"Illegal duck turn: {text}")
        return move

    def push(self, move: DuckTurn) -> None:
        if not self.is_legal(move):
            raise ValueError(f"Illegal duck turn: {move}")
        source, target = move.from_square, move.to_square
        piece, captured = self.squares[source], self.squares[target]
        castle = self._castle(move)
        if castle is not None:
            base = 0 if self.turn else 56
            self.squares[source] = self.squares[self.rooks[castle]] = ""
            self.squares[base + (2 if castle % 2 else 6)] = piece
            self.squares[base + (3 if castle % 2 else 5)] = "R" if self.turn else "r"
            self.halfmove_clock += 1
        else:
            if piece.lower() == "p" and target == self.ep_square and not captured and source % 8 != target % 8:
                self.squares[target + (-8 if self.turn else 8)] = ""
            self.squares[source] = ""
            self.squares[target] = (move.promotion.upper() if self.turn else move.promotion) if move.promotion else piece
            self.halfmove_clock = 0 if piece.lower() == "p" or captured else self.halfmove_clock + 1
        self.rooks = {side: rook for side, rook in self.rooks.items()
                      if not (piece.lower() == "k" and (side < 2) == self.turn)
                      and not (captured.lower() == "k" and (side < 2) != self.turn)
                      and (castle is not None or rook not in (source, target))}
        self.ep_square = (source + target) // 2 if piece.lower() == "p" and abs(target - source) == 16 else None
        if move.duck_square is not None:
            self.duck_square = move.duck_square
        self.fullmove_number += int(not self.turn)
        self.turn = not self.turn
        self.move_stack.append(move)
        self._positions[self.repetition_key()] += 1

    def push_uci(self, text: str) -> DuckTurn:
        move = self.parse_uci(text)
        self.push(move)
        return move

    def fen(self, *, en_passant: str = "fen", shredder: bool = False) -> str:
        ranks = []
        for rank in range(7, -1, -1):
            text, empty = "", 0
            for file in range(8):
                sq = rank * 8 + file
                piece = "*" if sq == self.duck_square else self.squares[sq]
                if not piece:
                    empty += 1
                else:
                    text += (str(empty) if empty else "") + piece
                    empty = 0
            ranks.append(text + (str(empty) if empty else ""))
        rights = "".join(chr((ord("A") if side < 2 else ord("a")) + self.rooks[side] % 8) if self.chess960 else "KQkq"[side] for side in sorted(self.rooks)) or "-"
        ep = self.ep_square
        if en_passant == "legal" and not self._effective_ep():
            ep = None
        return f"{'/'.join(ranks)} {'w' if self.turn else 'b'} {rights} {'-' if ep is None else chess.square_name(ep)} {self.halfmove_clock} {self.fullmove_number}"

    def _effective_ep(self) -> bool:
        return self.ep_square is not None and self.ep_square != self.duck_square and any(
            piece.lower() == "p" and self._piece_legal(DuckTurn(sq, self.ep_square))
            for sq, piece in enumerate(self.squares))

    def repetition_key(self) -> tuple[str, ...]:
        return tuple(self.fen(en_passant="legal").split()[:4])

    def root(self) -> DuckBoard:
        return DuckBoard(self._root_fen, chess960=self.chess960)

    def copy(self, *, stack: bool = True) -> DuckBoard:
        return deepcopy(self) if stack else DuckBoard(self.fen(), chess960=self.chess960)

    def ply(self) -> int:
        return (self.fullmove_number - 1) * 2 + int(not self.turn)

    def is_valid(self) -> bool:
        return self.squares.count("K") == self.squares.count("k") == 1

    def has_insufficient_material(self, color: chess.Color) -> bool:
        return False

    def outcome(self, *, claim_draw: bool = True) -> chess.Outcome | None:
        if ("K" if self.turn else "k") not in self.squares:
            return chess.Outcome(chess.Termination.VARIANT_LOSS, not self.turn)
        if ("k" if self.turn else "K") not in self.squares:
            return chess.Outcome(chess.Termination.VARIANT_WIN, self.turn)
        if not any(self._piece_legal(DuckTurn(source, target, "q" if piece.lower() == "p" and target // 8 in (0, 7) else ""))
                   for source, piece in enumerate(self.squares) if piece and piece.isupper() == self.turn for target in range(64)):
            return chess.Outcome(chess.Termination.VARIANT_WIN, self.turn)
        if self.halfmove_clock >= 100:
            return chess.Outcome(chess.Termination.FIFTY_MOVES, None)
        if self._positions[self.repetition_key()] >= 3:
            return chess.Outcome(chess.Termination.THREEFOLD_REPETITION, None)
        return None

    def is_game_over(self, *, claim_draw: bool = True) -> bool:
        return self.outcome() is not None

    def san(self, move: DuckTurn) -> str:
        if not self.is_legal(move):
            raise ValueError(f"Illegal duck turn: {move}")
        piece = self.squares[move.from_square]
        kind = piece.lower()
        castle = self._castle(move)
        if castle is not None:
            text = "O-O-O" if castle % 2 else "O-O"
        else:
            capture = bool(self.squares[move.to_square]) or kind == "p" and move.from_square % 8 != move.to_square % 8
            text = "" if kind == "p" else piece.upper()
            others = [sq for sq, other in enumerate(self.squares) if sq != move.from_square and other == piece
                      and self._piece_legal(DuckTurn(sq, move.to_square, move.promotion, move.duck_square))] if kind != "p" else []
            if others:
                same_file = any(sq % 8 == move.from_square % 8 for sq in others)
                same_rank = any(sq // 8 == move.from_square // 8 for sq in others)
                if not same_file or same_rank:
                    text += chess.square_name(move.from_square)[0]
                if same_file:
                    text += chess.square_name(move.from_square)[1]
            if capture:
                if kind == "p":
                    text += chess.square_name(move.from_square)[0]
                text += "x"
            text += chess.square_name(move.to_square)
            if move.promotion:
                text += "=" + move.promotion.upper()
        return text if move.duck_square is None else text + "@" + chess.square_name(move.duck_square)

    def parse_san(self, text: str) -> DuckTurn:
        text = text.rstrip("!?").replace("0", "O")
        try:
            return self.parse_uci(text)
        except ValueError:
            pass
        duck = None
        if "@" in text:
            duck = chess.parse_square(text.rsplit("@", 1)[1])
        matches = []
        for source, piece in enumerate(self.squares):
            if not piece or piece.isupper() != self.turn:
                continue
            for target in range(64):
                for promotion in ("nbrq" if piece.lower() == "p" and target // 8 in (0, 7) else ("",)):
                    move = DuckTurn(source, target, promotion, duck)
                    if self.is_legal(move) and self.san(move) == text:
                        matches.append(move)
        if len(matches) != 1:
            raise ValueError(f"Invalid or ambiguous duck notation: {text}")
        return matches[0]
