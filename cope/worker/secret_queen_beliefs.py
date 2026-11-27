from __future__ import annotations

from collections import defaultdict
import math
import time

import chess

from cope.variants.secret_queen import SecretQueenBoard

from .secret_queen_search import exposure, material, terminal


class OpponentBeliefs:
    def __init__(self, color):
        self.color = color
        self.history = ()
        self.positions = tuple(SecretQueenBoard(files=(black, white)) for black in range(8) for white in range(8))
        self.weights = [1 / 8] * 8
        self.observations = 0

    def update(self, history, worlds, deadline, stop):
        if history[:len(self.history)] != self.history:
            self.__init__(self.color)
        for uci, fen in history[len(self.history):]:
            move = chess.Move.from_uci(uci)
            successors = []
            evidence = defaultdict(list)
            complete = True
            cache = {}
            for before in self.positions:
                if not before.is_legal(move):
                    continue
                after = before.copy(stack=False)
                after.push(move, record=False)
                if after.fen(en_passant="fen") != fen or after.outcome() is not None:
                    continue
                successors.append(after)
                if before.turn == self.color:
                    continue
                if stop.is_set() or time.monotonic() >= deadline:
                    complete = False
                    continue
                end = terminal(after, before.turn)
                missed_win = before.secret_capture() is not None and move != before.secret_capture()
                if missed_win or (end is not None and end < -29000):
                    regret = 600
                elif end is not None:
                    regret = 0
                else:
                    gain = material(after, before.turn) - material(before, before.turn)
                    regret = min(600, max(0, exposure(after, cache) - gain))
                evidence[before._files[before.turn]].append(regret)
            self.positions = tuple(successors)
            if complete and evidence:
                surviving = {position._files[not self.color] for position in successors}
                updated = {file: max(self.weights[file], 1e-9)
                           * (.05 + .95 * math.exp(-sum(evidence.get(file, ())) / max(1, len(evidence.get(file, ()))) / 50))
                           for file in surviving}
                total = sum(updated.values())
                self.weights = [(.95 * updated[file] / total + .05 / len(updated)) if file in updated else 0
                                for file in range(8)]
                self.observations += 1
        self.history = history
        weights = [max(self.weights[world._files[not self.color]], 1e-9) for world in worlds]
        total = sum(weights)
        return tuple(weight / total for weight in weights) if total else tuple(1 / len(worlds) for _ in worlds)
