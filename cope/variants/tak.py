from __future__ import annotations

import re
from copy import copy
from dataclasses import dataclass


TAK_SIZE = 6
TAK_FLATS = 30
TAK_CAPS = 1
TAK_HALF_KOMI = 4
TAK_START = "x6/x6/x6/x6/x6/x6 1 1"
NO_PROGRESS_PLIES = 50
_DIRECTIONS = {">": (1, 0), "<": (-1, 0), "+": (0, 1), "-": (0, -1)}
_PLACEMENT = re.compile(r"([FSC]?)([a-f][1-6])")
_SPREAD = re.compile(r"([1-6]?)([a-f][1-6])([<>+-])([1-6]*)[SC]?\*?")


@dataclass(frozen=True, slots=True)
class TakMove:
    square: str
    piece: str = "F"
    direction: str = ""
    drops: tuple[int, ...] = ()

    def uci(self) -> str:
        if not self.direction:
            return f"{self.piece if self.piece != 'F' else ''}{self.square}"
        count = sum(self.drops)
        carry = str(count) if count > 1 else ""
        drops = "".join(map(str, self.drops)) if len(self.drops) > 1 else ""
        return f"{carry}{self.square}{self.direction}{drops}"

    def path(self) -> tuple[int, ...]:
        x, y = ord(self.square[0]) - ord("a"), int(self.square[1]) - 1
        if not self.direction:
            return (y * TAK_SIZE + x,)
        dx, dy = _DIRECTIONS[self.direction]
        squares = [y * TAK_SIZE + x]
        for _ in self.drops:
            x += dx
            y += dy
            if not (0 <= x < TAK_SIZE and 0 <= y < TAK_SIZE):
                return ()
            squares.append(y * TAK_SIZE + x)
        return tuple(squares)


class TakBoard:
    variant = "tak"
    chess960 = False

    def __init__(self, tps: str = TAK_START):
        rows, player, fullmove = tps.split()
        if player not in {"1", "2"} or not fullmove.isdecimal() or int(fullmove) < 1:
            raise ValueError("Invalid TPS turn")
        ranks = rows.split("/")
        if len(ranks) != TAK_SIZE:
            raise ValueError("Tak requires a 6×6 TPS board")
        self._squares: list[str] = []
        for rank in reversed(ranks):
            squares = []
            for token in rank.split(","):
                if re.fullmatch(r"x[1-6]?", token):
                    squares.extend([""] * int(token[1:] or "1"))
                elif re.fullmatch(r"[12]+[SC]?", token):
                    squares.append(token)
                else:
                    raise ValueError("Invalid TPS stack")
            if len(squares) != TAK_SIZE:
                raise ValueError("Invalid TPS rank width")
            self._squares.extend(squares)
        self.turn = player == "1"
        self.fullmove_number = int(fullmove)
        self._reserves = {side: [TAK_FLATS, TAK_CAPS] for side in "12"}
        for stack in filter(None, self._squares):
            for stone in self._stones(stack):
                self._reserves[stone[0]][int(stone.endswith("C"))] -= 1
        if any(min(reserve) < 0 for reserve in self._reserves.values()):
            raise ValueError("TPS exceeds Tak reserves")
        if self.ply() < 2:
            stones = [stack for stack in self._squares if stack]
            if stones != (["2"] if self.ply() == 1 else []):
                raise ValueError("Invalid Tak swap opening")
        self._quiet_plies = 0
        self._root_tps = self.fen()
        self.move_stack: list[TakMove] = []

    @staticmethod
    def _stones(stack: str) -> list[str]:
        return re.findall(r"[12][SC]?", stack)

    def ply(self) -> int:
        return 2 * (self.fullmove_number - 1) + int(not self.turn)

    def fen(self) -> str:
        ranks = []
        for offset in range(TAK_SIZE * (TAK_SIZE - 1), -1, -TAK_SIZE):
            rank = ",".join(stack or "x" for stack in self._squares[offset:offset + TAK_SIZE])
            ranks.append(re.sub(r"x(?:,x)+", lambda match: f"x{match[0].count('x')}", rank))
        return f"{'/'.join(ranks)} {1 if self.turn else 2} {self.fullmove_number}"

    def root(self) -> TakBoard:
        return TakBoard(self._root_tps)

    def copy(self, *, stack: bool = True) -> TakBoard:
        board = copy(self)
        board._squares = self._squares.copy()
        board._reserves = {side: reserve.copy() for side, reserve in self._reserves.items()}
        board.move_stack = self.move_stack.copy() if stack else []
        if not stack:
            board._root_tps = self.fen()
        return board

    def parse_uci(self, text: str) -> TakMove:
        if match := _PLACEMENT.fullmatch(text):
            piece, square = match.groups()
            return TakMove(square, piece or "F")
        if match := _SPREAD.fullmatch(text):
            carry, square, direction, drops = match.groups()
            count = int(carry or "1")
            drop_counts = tuple(map(int, drops)) if drops else (count,)
            if sum(drop_counts) == count:
                return TakMove(square, direction=direction, drops=drop_counts)
        raise ValueError(f"Invalid PTN move: {text}")

    @property
    def legal_moves(self) -> TakBoard:
        return self

    def __contains__(self, move: TakMove) -> bool:
        return self.is_legal(move)

    def is_legal(self, move: TakMove) -> bool:
        path = move.path()
        if not path:
            return False
        source = self._squares[path[0]]
        side = "1" if self.turn else "2"
        if self.ply() < 2:
            return not source and not move.direction and move.piece == "F"
        if not move.direction:
            return not source and self._reserves[side][int(move.piece == "C")] > 0
        stones = self._stones(source)
        if not stones or stones[-1][0] != side or len(stones) < sum(move.drops):
            return False
        for index, (square, drop) in enumerate(zip(path[1:], move.drops)):
            destination = self._squares[square]
            if destination.endswith("C"):
                return False
            if destination.endswith("S") and not (
                stones[-1].endswith("C") and index == len(move.drops) - 1 and drop == 1
            ):
                return False
        return True

    def san(self, move: TakMove) -> str:
        flatten = move.direction and self._squares[move.path()[-1]].endswith("S")
        return move.uci() + ("*" if flatten else "")

    def push_uci(self, text: str) -> TakMove:
        move = self.parse_uci(text)
        if not self.is_legal(move):
            raise ValueError(f"Illegal Tak move: {text}")
        self.push(move)
        return move

    def push(self, move: TakMove) -> None:
        source, *destinations = move.path()
        self._quiet_plies += 1
        if move.direction:
            stones = self._stones(self._squares[source])
            split = len(stones) - sum(move.drops)
            self._squares[source] = "".join(stones[:split])
            for square, drop in zip(destinations, move.drops):
                if self._squares[square].endswith("S"):
                    self._squares[square] = self._squares[square][:-1]
                    self._quiet_plies = 0
                self._squares[square] += "".join(stones[split:split + drop])
                split += drop
        else:
            side = "1" if self.turn != (self.ply() < 2) else "2"
            self._squares[source] = side + ("" if move.piece == "F" else move.piece)
            self._reserves[side][int(move.piece == "C")] -= 1
            self._quiet_plies = 0
        self.move_stack.append(move)
        self.fullmove_number += int(not self.turn)
        self.turn = not self.turn

    def result(self) -> tuple[bool | None, str] | None:
        mover = not self.turn
        for side in (mover, self.turn):
            if self._has_road("1" if side else "2"):
                return side, "road"
        if all(self._squares) or any(sum(reserve) == 0 for reserve in self._reserves.values()):
            difference = 2 * (sum(stack.endswith("1") for stack in self._squares)
                              - sum(stack.endswith("2") for stack in self._squares)) - TAK_HALF_KOMI
            return (difference > 0 if difference else None), "flat count"
        if self._quiet_plies >= NO_PROGRESS_PLIES:
            return None, "no progress"
        return None

    def _has_road(self, side: str) -> bool:
        unvisited = {index for index, stack in enumerate(self._squares)
                     if stack.endswith((side, side + "C"))}
        while unvisited:
            pending = [unvisited.pop()]
            edges = set()
            while pending:
                index = pending.pop()
                y, x = divmod(index, TAK_SIZE)
                edges.update(edge for edge, coordinate in enumerate((x, TAK_SIZE - 1 - x, y, TAK_SIZE - 1 - y))
                             if coordinate == 0)
                if {0, 1} <= edges or {2, 3} <= edges:
                    return True
                for dx, dy in _DIRECTIONS.values():
                    nx, ny = x + dx, y + dy
                    neighbor = ny * TAK_SIZE + nx
                    if 0 <= nx < TAK_SIZE and 0 <= ny < TAK_SIZE and neighbor in unvisited:
                        unvisited.remove(neighbor)
                        pending.append(neighbor)
        return False
