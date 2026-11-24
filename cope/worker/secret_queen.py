from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
import json
import re
import threading
import time

import chess

from cope.variants.secret_queen import POSITION_PREFIX, SEARCH_WORKERS, SecretQueenBoard

from .uci_engine import UciEngineProcess


@dataclass(frozen=True, slots=True)
class Probe:
    move: chess.Move
    score: tuple[int, int] | None


_STATISTICS = re.compile(r"\b(depth|seldepth|nodes|hashfull) ([0-9]{1,15})(?= |$)")


class SearchStatistics:
    def __init__(self, started, callback):
        self._started = started
        self._callback = callback
        self._values: dict[str, int] = {}
        self._lock = threading.Lock()

    def record(self, lines: list[str]) -> None:
        values: dict[str, int] = {}
        for line in lines:
            if line.startswith("info ") and not line.startswith("info string "):
                values.update((key, int(value)) for key, value in _STATISTICS.findall(line.partition(" pv ")[0]))
        if not values:
            return
        with self._lock:
            for key, value in values.items():
                previous = self._values.get(key, 0)
                self._values[key] = previous + value if key == "nodes" else max(previous, value)
            if self._callback is not None:
                self._callback(self._line())

    def _line(self) -> str:
        elapsed = max(1, round((time.monotonic() - self._started) * 1000))
        values = {**self._values, "time": elapsed}
        if "nodes" in values:
            values["nps"] = values["nodes"] * 1000 // elapsed
        return "info " + " ".join(f"{key} {value}" for key, value in values.items())

    def result(self, move: chess.Move) -> list[str]:
        lines = [f"bestmove {move.uci()}"]
        with self._lock:
            return [self._line(), *lines] if self._values else lines


def _probe_result(lines: list[str]) -> Probe | None:
    words = lines[-1].split() if lines else []
    if len(words) < 2 or words[0] != "bestmove":
        return None
    try:
        move = chess.Move.from_uci(words[1])
    except ValueError:
        return None
    for line in reversed(lines[:-1]):
        words = line.split()
        if not words or words[0] != "info" or any(bound in words for bound in ("lowerbound", "upperbound")):
            continue
        try:
            if words[words.index("pv") + 1] != move.uci():
                continue
            index = words.index("score")
            kind, value = words[index + 1], int(words[index + 2])
        except (ValueError, IndexError):
            continue
        if kind == "cp":
            return Probe(move, (0, value))
        if kind == "mate":
            return Probe(move, (1, -value) if value > 0 else (-1, -value))
    return Probe(move, None)


def _outcome_score(board: SecretQueenBoard, color: chess.Color) -> tuple[int, int] | None:
    if board.secret_capture() is not None:
        return (2 if board.turn == color else -2, 0)
    outcome = board.outcome()
    if outcome:
        return (0, 0) if outcome.winner is None else (2 if outcome.winner == color else -2, 0)
    return None


class SecretQueenEngine(UciEngineProcess):
    def __init__(self, spec, *, server_url: str, credential: str, progress_callback=None):
        super().__init__(spec, server_url=server_url, credential=credential, progress_callback=progress_callback)
        self._position: dict | None = None
        self._stop = threading.Event()
        self._options: dict[str, str] = {}
        self._pool = ThreadPoolExecutor(max_workers=SEARCH_WORKERS)
        self._active: set[UciEngineProcess] = set()
        self._active_lock = threading.Lock()
        self._closed = False
        self._lanes: list[UciEngineProcess | None] = [self] + [None] * (SEARCH_WORKERS - 1)
        self._observers: list[UciEngineProcess | None] = [None] * SEARCH_WORKERS
        self._new_process = lambda: UciEngineProcess(spec, server_url=server_url, credential=credential)

    def handle_command(self, command, line_callback=None):
        if command.startswith(POSITION_PREFIX):
            try:
                self._position = json.loads(command[len(POSITION_PREFIX):])
            except ValueError:
                raise RuntimeError("Invalid Secret Queen seat position") from None
            return []
        if command.startswith("setoption name "):
            key = command.split(" value", 1)[0].lower()
            self._options[key] = command
        if command == "ucinewgame":
            self._position = None
        if command.startswith("go") and self._position is not None:
            self._stop.clear()
            return self._turn(command, line_callback)
        return super().handle_command(command, line_callback)

    def stop_search(self) -> None:
        self._stop.set()
        with self._active_lock:
            processes = tuple(self._active)
        for engine in processes:
            with suppress(RuntimeError):
                UciEngineProcess.stop_search(engine)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.stop_search()
        self._pool.shutdown(wait=True, cancel_futures=True)
        for engine in (*self._lanes[1:], *self._observers):
            if engine is not None:
                engine.close()
        super().close()

    def _process_for(self, lane: int, observe: bool, deadline: float | None = None) -> UciEngineProcess:
        pool = self._observers if observe else self._lanes
        engine = pool[lane]
        if engine is None:
            engine = self._new_process()
            pool[lane] = engine
        if not engine.process_started:
            try:
                engine._send("uci")
                timeout = .4 if deadline is None else max(0, min(.4, deadline - time.monotonic() - .045))
                engine._read_until(lambda line: line == "uciok", timeout_s=timeout)
                for option in self._options.values():
                    engine._send(option)
                engine._send("ucinewgame")
                engine._send("isready")
                timeout = .4 if deadline is None else max(0, min(.4, deadline - time.monotonic() - .045))
                engine._read_until(lambda line: line == "readyok", timeout_s=timeout)
            except (OSError, RuntimeError):
                self._discard(engine)
                raise
        return engine

    @staticmethod
    def _discard(engine: UciEngineProcess) -> None:
        process = engine._process
        if process is not None and process.poll() is None:
            with suppress(OSError):
                process.kill()
        UciEngineProcess.close(engine)

    def _probe(self, lane, board, deadline, *, observe=False, moves=(), limit_ms=None, statistics=None):
        if board is None or self._stop.is_set() or deadline - time.monotonic() < .045:
            return None
        engine = None
        try:
            engine = self._process_for(lane, observe, deadline)
            with self._active_lock:
                if self._stop.is_set():
                    return None
                self._active.add(engine)
            remaining = int((deadline - time.monotonic()) * 1000) - 25
            if remaining < 20:
                return None
            window = remaining if limit_ms is None else min(remaining, limit_ms)
            command = f"go movetime {window}"
            if moves:
                command += " searchmoves " + " ".join(move.uci() for move in moves)
            engine._send("position fen " + board.fen())
            engine._send(command)
            try:
                lines = engine._read_until(lambda line: line.startswith("bestmove"), timeout_s=(window + 12) / 1000)
            except RuntimeError:
                UciEngineProcess.stop_search(engine)
                lines = engine._read_until(lambda line: line.startswith("bestmove"), timeout_s=.025)
            if statistics is not None:
                statistics.record(lines)
            result = _probe_result(lines)
            return result if result and result.move in board.legal_moves and (not moves or result.move in moves) else None
        except (OSError, RuntimeError):
            if engine is not None:
                self._discard(engine)
            return None
        finally:
            with self._active_lock:
                self._active.discard(engine)

    def _turn(self, command, line_callback):
        started = time.monotonic()
        color = self._position["color"]
        worlds = SecretQueenBoard.beliefs(color, self._position["file"], self._position["moves"])
        if not worlds:
            raise RuntimeError("No legal Secret Queen position matches the public history")
        first = worlds[0]
        moves = tuple(sorted(first.legal_moves, key=chess.Move.uci))
        if not moves:
            return ["bestmove 0000"]
        if first.secret_capture() is not None:
            return [f"bestmove {first.secret_capture().uci()}"]
        if all(not world.concealed(color) and not world.concealed(not color) for world in worlds):
            self._process_for(0, False)
            with self._active_lock:
                self._active.add(self)
            try:
                super().handle_command(first.native_position())
                lines = super().handle_command(command, line_callback)
                result = _probe_result(lines)
                return lines if result and result.move in moves else [f"bestmove {moves[0].uci()}"]
            finally:
                with self._active_lock:
                    self._active.discard(self)

        words = command.split()
        clock_key = "wtime" if color else "btime"
        increment_key = "winc" if color else "binc"
        remaining = int(words[words.index(clock_key) + 1]) if clock_key in words else 1000
        increment = int(words[words.index(increment_key) + 1]) if increment_key in words else 0
        budget_ms = max(0, min(remaining - 65, remaining // 22 + increment * 3 // 4, 5000))
        deadline = started + budget_ms / 1000
        risks = Counter()
        wins = Counter()
        for move in moves:
            for world in worlds:
                after = world.copy(stack=False)
                after.push(move)
                value = _outcome_score(after, color)
                risks[move] += value == (-2, 0)
                wins[move] += value == (2, 0)
            if self._stop.is_set():
                return [f"bestmove {move.uci()}"]
        certain_wins = [move for move in moves if wins[move] == len(worlds)]
        if certain_wins:
            return [f"bestmove {certain_wins[0].uci()}"]
        minimum = min(risks.values())
        safe = tuple(move for move in moves if risks[move] == minimum)
        fallback = max(safe, key=lambda move: wins[move])
        if len(safe) == 1 or deadline - time.monotonic() < .09:
            return [f"bestmove {fallback.uci()}"]
        statistics = SearchStatistics(started, line_callback)
        anchor = first.projection(show=(color,))
        native = tuple(move for move in safe if anchor is not None and move in anchor.legal_moves)
        restricted = native if anchor is not None and len(native) < anchor.legal_moves.count() else ()
        incumbent = self._probe(
            0, anchor, min(deadline, started + budget_ms * .35 / 1000), moves=restricted, statistics=statistics,
        ) if native else None
        chosen = incumbent.move if incumbent and incumbent.move in safe else fallback
        if deadline - time.monotonic() < .25:
            return statistics.result(chosen)

        finalists = [chosen]
        opposite = tuple(move for move in native if first.reveals(move) != first.reveals(chosen))
        end_nomination = min(deadline, started + budget_ms * .55 / 1000)
        nomination = self._probe(
            0, anchor, end_nomination, moves=opposite, limit_ms=125, statistics=statistics,
        ) if opposite else None
        if nomination:
            finalists.append(nomination.move)
        votes = Counter()
        def nominate(index, world):
            projection = world.projection(show=(chess.WHITE, chess.BLACK))
            options = tuple(move for move in safe if move not in finalists and projection is not None and move in projection.legal_moves)
            return self._probe(index, projection, end_nomination, moves=options, statistics=statistics) if options else None

        nominations = [self._pool.submit(nominate, index, world) for index, world in enumerate(worlds)]
        for future in nominations:
            result = future.result()
            if result:
                votes[result.move] += 1
        remaining_candidates = set(safe) - set(finalists)
        if remaining_candidates:
            finalists.append(min(remaining_candidates, key=lambda move: (-wins[move], -votes[move], move.uci())))
        slot_ms = int((deadline - time.monotonic()) * 1000 / (2 * len(finalists))) - 20
        if len(finalists) > 1 and slot_ms >= 35:
            futures = [self._pool.submit(self._rank, index, world, finalists, deadline, slot_ms, statistics) for index, world in enumerate(worlds)]
            rows = [future.result() for future in futures]
            if all(row is not None for row in rows):
                chosen = max(finalists, key=lambda move: sum(row[move] for row in rows))
        return statistics.result(chosen)

    def _rank(self, index, world, finalists, deadline, slot_ms, statistics):
        scores = {}
        root_color = world.turn
        order = finalists[index % len(finalists):] + finalists[:index % len(finalists)]
        for move in order:
            after = world.copy(stack=False)
            after.push(move)
            score = _outcome_score(after, root_color)
            if score is None:
                reply = self._probe(index, after.projection(show=(after.turn,)), deadline, observe=True, limit_ms=slot_ms)
                if reply is None or not after.is_legal(reply.move):
                    return None
                after.push(reply.move)
                score = _outcome_score(after, root_color)
            if score is None:
                estimate = self._probe(index, after.projection(show=(chess.WHITE, chess.BLACK)), deadline, limit_ms=slot_ms, statistics=statistics)
                if estimate is None or estimate.score is None:
                    return None
                score = estimate.score
            scores[move] = score
        return {move: sum(candidate < score for candidate in scores.values()) for move, score in scores.items()}
