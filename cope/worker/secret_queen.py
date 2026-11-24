from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
import json
import re
import logging
import threading
import time

import chess

from cope.variants.secret_queen import POSITION_PREFIX, SEARCH_WORKERS, SecretQueenBoard

from .uci_engine import UciEngineProcess
from .secret_queen_beliefs import OpponentBeliefs
from .secret_queen_search import MATE, SearchExpired, SearchResult, TacticalSearch, concealment, evaluate, exchange, exposure, fork_reply, mating_reply, move_order, terminal


Score = tuple[int, int]
_COLORS = (chess.WHITE, chess.BLACK)
LOG = logging.getLogger("cope.worker.secret_queen")


@dataclass(frozen=True, slots=True)
class Assessment:
    score: Score


@dataclass(frozen=True, slots=True)
class Probe:
    move: chess.Move
    evaluation: Assessment | None


_STATISTICS = re.compile(r"\b(depth|seldepth|nodes|hashfull) ([0-9]{1,15})(?= |$)")
_OPTION_NAME = re.compile(r"^option\s+name\s+(.+?)\s+type\s+\S+", re.IGNORECASE)


def _score_token(score: Score) -> str:
    kind, value = score
    if kind == 0:
        return f"cp:{value}"
    if abs(kind) == 1 and value != 0:
        return f"mate:{-value}"
    return "win" if kind > 0 else "loss"


class SearchStatistics:
    def __init__(self, started, callback):
        self._started = started
        self._callback = callback
        self._values: dict[str, int] = {}
        self._evaluation: str | None = None
        self._estimate: Score | None = None
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

    def evaluate(self, scores: list[Score], positions: int, estimate: Score | None = None) -> None:
        count = len(scores) if positions else 0
        with self._lock:
            self._evaluation = f"{_score_token(min(scores))},{_score_token(max(scores))},{count},{positions}"
            self._estimate = estimate
            if self._callback is not None:
                self._callback(self._line())

    def _line(self) -> str:
        elapsed = max(1, round((time.monotonic() - self._started) * 1000))
        values = {**self._values, "time": elapsed}
        if "nodes" in values:
            values["nps"] = values["nodes"] * 1000 // elapsed
        line = "info " + " ".join(f"{key} {value}" for key, value in values.items())
        if self._evaluation:
            line += f" sq_eval {self._evaluation}"
        if self._estimate is not None:
            line += f" sq_score {_score_token(self._estimate)}"
        return line

    def result(self, move: chess.Move) -> list[str]:
        lines = [f"bestmove {move.uci()}"]
        with self._lock:
            return [self._line(), *lines] if self._values or self._evaluation else lines


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
            return Probe(move, Assessment((0, value)))
        if kind == "mate":
            return Probe(move, Assessment((1, -value) if value > 0 else (-1, -value)))
    return Probe(move, None)


def _variations(lines: list[str], board: chess.Board) -> tuple[Probe, ...]:
    found = {}
    for line in lines:
        words = line.split()
        if not words or words[0] != "info" or "pv" not in words:
            continue
        try:
            move = words[words.index("pv") + 1]
            slot = int(words[words.index("multipv") + 1]) if "multipv" in words else 1
        except (IndexError, ValueError):
            continue
        probe = _probe_result([line, f"bestmove {move}"])
        if probe and probe.evaluation and board.is_legal(probe.move):
            found[slot] = probe
    best = _probe_result(lines)
    if best and board.is_legal(best.move):
        return (best, *(probe for _, probe in sorted(found.items()) if probe.move != best.move))
    return tuple(probe for _, probe in sorted(found.items()))


class SecretQueenEngine(UciEngineProcess):
    def __init__(self, spec, *, server_url: str, credential: str, progress_callback=None):
        super().__init__(spec, server_url=server_url, credential=credential, progress_callback=progress_callback)
        self._position: dict | None = None
        self._stop = threading.Event()
        self._options: dict[str, str] = {}
        self._advertised: list[set[str]] = [set() for _ in range(SEARCH_WORKERS)]
        self._configured: list[dict[str, str]] = [{} for _ in range(SEARCH_WORKERS)]
        self._belief_cache = None
        self._opponent_beliefs = None
        self._pool = ThreadPoolExecutor(max_workers=max(1, SEARCH_WORKERS - 1))
        self._active: set[UciEngineProcess] = set()
        self._active_lock = threading.Lock()
        self._closed = False
        self._lanes: list[UciEngineProcess | None] = [self] + [None] * (SEARCH_WORKERS - 1)
        self._new_process = lambda: UciEngineProcess(spec, server_url=server_url, credential=credential)

    def handle_command(self, command, line_callback=None):
        if command.startswith(POSITION_PREFIX):
            try:
                self._position = json.loads(command[len(POSITION_PREFIX):])
            except ValueError:
                raise RuntimeError("Invalid Secret Queen seat position") from None
            return []
        if command == "uci":
            lines = super().handle_command(command, line_callback)
            self._advertised[0] = self._advertised_options(lines)
            return lines
        if command.startswith("setoption name "):
            name = command[len("setoption name "):].split(" value", 1)[0].casefold()
            if name not in self._advertised[0]:
                return []
            self._options[name] = command
            self._configured[0][name] = command
        if command == "ucinewgame":
            self._position = None
            self._belief_cache = None
            self._opponent_beliefs = None
            for engine in self._lanes[1:]:
                if engine is not None and engine.process_started:
                    engine.handle_command(command)
        if command == "isready":
            deadline = time.monotonic() + 3
            futures = [self._pool.submit(self._warm, lane, deadline) for lane in range(1, SEARCH_WORKERS)]
            lines = super().handle_command(command, line_callback)
            for future in futures:
                future.result()
            return lines
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
        for engine in self._lanes[1:]:
            if engine is not None:
                engine.close()
        super().close()

    @staticmethod
    def _advertised_options(lines: list[str]) -> set[str]:
        return {match[1].casefold() for line in lines if (match := _OPTION_NAME.match(line))}

    def _warm(self, lane, deadline):
        if self._lanes[lane] is not None and self._lanes[lane].process_started:
            return
        try:
            self._process_for(lane, deadline, startup_timeout=2)
        except (OSError, RuntimeError):
            if self._lanes[lane] is not None:
                self._discard(self._lanes[lane])

    def _process_for(self, lane: int, deadline: float, multipv: int = 1, startup_timeout=.5, threads=1) -> UciEngineProcess:
        engine = self._lanes[lane]
        if engine is None:
            engine = self._new_process()
            self._lanes[lane] = engine
        if not engine.process_started:
            engine._send("uci")
            timeout = max(.001, min(startup_timeout, deadline - time.monotonic() - .035))
            lines = engine._read_until(lambda line: line == "uciok", timeout_s=timeout)
            self._advertised[lane] = self._advertised_options(lines)
            self._configured[lane].clear()
            engine._send("ucinewgame")
        options = {"threads": "setoption name Threads value 1", **self._options,
                   "multipv": f"setoption name MultiPV value {multipv}"}
        if threads is not None:
            options["threads"] = f"setoption name Threads value {threads}"
        for name, option in options.items():
            if name in self._advertised[lane] and self._configured[lane].get(name) != option:
                engine._send(option)
                self._configured[lane][name] = option
        engine._send("isready")
        timeout = max(.001, min(startup_timeout, deadline - time.monotonic() - .035))
        engine._read_until(lambda line: line == "readyok", timeout_s=timeout)
        return engine

    @staticmethod
    def _discard(engine: UciEngineProcess) -> None:
        process = engine._process
        if process is not None and process.poll() is None:
            with suppress(OSError):
                process.kill()
        UciEngineProcess.close(engine)

    def _probe(self, board, deadline, statistics, *, lane, limit_ms=None, multipv=1, position=None, threads=1):
        if board is None or self._stop.is_set() or deadline - time.monotonic() < .055:
            return ()
        engine = None
        try:
            engine = self._process_for(lane, deadline, multipv, threads=threads)
            with self._active_lock:
                if self._stop.is_set():
                    return ()
                self._active.add(engine)
            remaining = int((deadline - time.monotonic()) * 1000) - 35
            if remaining < 15:
                return ()
            window = remaining if limit_ms is None else min(remaining, limit_ms)
            engine._send(position if position is not None else "position fen " + board.fen())
            engine._send(f"go movetime {max(1, window)}")
            lines = []
            try:
                ending = engine._read_until(lambda line: line.startswith("bestmove"), line_callback=lines.append,
                                            timeout_s=(window + 10) / 1000)
            except RuntimeError:
                UciEngineProcess.stop_search(engine)
                ending = engine._read_until(lambda line: line.startswith("bestmove"), line_callback=lines.append,
                                            timeout_s=max(.005, min(.040, deadline - time.monotonic())))
            lines.append(ending[-1])
            statistics.record(lines)
            return _variations(lines, board)
        except (OSError, RuntimeError):
            failed = engine if engine is not None else self._lanes[lane]
            if failed is not None:
                self._discard(failed)
            return ()
        finally:
            with self._active_lock:
                self._active.discard(engine)

    def _worlds(self, color, file, history):
        prior = None
        suffix = history
        if self._belief_cache is not None:
            previous_color, previous_file, previous_history, previous_worlds = self._belief_cache
            if (color, file) == (previous_color, previous_file) and history[:len(previous_history)] == previous_history:
                prior = previous_worlds
                suffix = history[len(previous_history):]
        worlds = SecretQueenBoard.beliefs(color, file, suffix, prior=prior)
        self._belief_cache = color, file, history, worlds
        return worlds

    @staticmethod
    def _budget(command, color):
        words = command.split()
        def value(name, default):
            return int(words[words.index(name) + 1]) if name in words else default
        if "movetime" in words:
            return max(0, value("movetime", 1000) - 50)
        remaining = value("wtime" if color else "btime", 1000)
        increment = value("winc" if color else "binc", 0)
        moves = max(1, min(24, value("movestogo", 24)))
        return max(0, min(remaining - 80, remaining // moves + increment * 3 // 4, 5000))

    def _turn(self, command, line_callback):
        started = time.monotonic()
        color = self._position["color"]
        deadline = started + self._budget(command, color) / 1000
        history = tuple((move, fen) for move, fen in self._position["moves"])
        worlds = self._worlds(color, self._position["file"], history)
        if not worlds:
            raise RuntimeError("No legal Secret Queen position matches the public history")
        first = worlds[0]
        moves = tuple(sorted(first.legal_moves, key=chess.Move.uci))
        if not moves:
            return ["bestmove 0000"]
        statistics = SearchStatistics(started, line_callback)
        capture = first.secret_capture()
        if capture is not None:
            statistics.evaluate([(2, 0)] * len(worlds), len(worlds))
            return statistics.result(capture)
        if not any(world.concealed(side) for world in worlds for side in _COLORS):
            lane = first._files[not color]
            engine = self._process_for(lane, max(deadline, started + .5), threads=None)
            with self._active_lock:
                self._active.add(engine)
            try:
                UciEngineProcess.handle_command(engine, first.native_position())
                lines = UciEngineProcess.handle_command(engine, command, line_callback)
                result = _probe_result(lines)
                return lines if result and result.move in moves else statistics.result(moves[0])
            finally:
                with self._active_lock:
                    self._active.discard(engine)

        quick = deadline - started < .12
        if self._opponent_beliefs is None or self._opponent_beliefs.color != color:
            self._opponent_beliefs = OpponentBeliefs(color)
        weights = self._opponent_beliefs.update(history, worlds, started + (deadline - started) * .10, self._stop)
        if quick:
            moves = tuple(sorted(moves, key=lambda move: move_order(first, move), reverse=True)[:8])
        projections = {}
        for index, world in enumerate(() if quick else worlds):
            board = world.projection(show=_COLORS)
            if board is not None:
                projections.setdefault(board.fen(), (board, []))[1].append(index)
        workers = max(1, SEARCH_WORKERS - 1)
        waves = max(1, (len(projections) + workers - 1) // workers)
        oracle_deadline = started + (deadline - started) * .62
        window = max(15, int((oracle_deadline - time.monotonic()) * 1000 / waves) - 45)
        futures = [(indices, self._pool.submit(self._probe, board, oracle_deadline, statistics,
                                              lane=worlds[indices[0]]._files[not color],
                                              multipv=6, limit_ms=window,
                                              threads=max(1, workers // max(1, min(workers, len(projections))))))
                   for board, indices in projections.values()]
        rows = {}
        bases = {}
        exchanges = {}
        for move in moves:
            values = []
            baseline = []
            for world in worlds:
                after = world.copy(stack=False)
                after.push(move, record=False)
                end = terminal(after, color, 1)
                if end is None:
                    loss = exchange(after, move.to_square, exchanges) if quick else exposure(after, exchanges)
                    values.append(evaluate(after, color) - loss)
                else:
                    values.append(end)
                baseline.append(values[-1] - concealment(after, color))
            rows[move] = SearchResult(tuple(values), -1, 0)
            bases[move] = baseline
        eligible = [move for move in moves if rows[move].losses == 0] or list(moves)
        guidance = {move: [0.] * len(worlds) for move in eligible}
        nominations = set()
        covered = set()
        for indices, future in futures:
            probes = future.result()
            if not probes:
                continue
            covered.update(indices)
            scores = {}
            for probe in probes:
                if probe.move in eligible:
                    nominations.add(probe.move)
                if probe.evaluation is not None:
                    kind, value = probe.evaluation.score
                    scores[probe.move] = value if kind == 0 else 10000 if kind > 0 else -10000
            for move in eligible:
                for index in indices:
                    reference = probes[0].move
                    if move in scores:
                        adjustment = scores[move] - bases[move][index]
                    elif reference in scores and reference in bases:
                        adjustment = scores[reference] - bases[reference][index]
                    else:
                        adjustment = 0
                    guidance[move][index] = max(-150, min(150, adjustment))

        def blended(move):
            return tuple(score + round(guidance[move][index]) if abs(score) < MATE - 128 else score
                         for index, score in enumerate(rows[move].values))

        def value(move):
            row = rows[move]
            scores = blended(move)
            average = sum(weight * score for weight, score in zip(weights, scores))
            downside = sum(weight * max(0, average - score) for weight, score in zip(weights, scores))
            losses = sum(weight for weight, score in zip(weights, row.values) if score <= -MATE + 128)
            return -losses, average - downside, min(scores), move in nominations

        eligible.sort(key=value, reverse=True)
        finalists = eligible[:6]
        refutations = {}
        for move in finalists:
            for index, world in enumerate(worlds):
                after = world.copy(stack=False)
                after.push(move, record=False)
                loss = mating_reply(after, color, started + (deadline - started) * .72)
                if loss is not None:
                    refutations.setdefault(move, {})[index] = loss
        for move in finalists:
            for index, world in enumerate(worlds):
                after = world.copy(stack=False)
                after.push(move, record=False)
                loss = fork_reply(after, color, started + (deadline - started) * .82, self._stop)
                if loss is not None:
                    refutations.setdefault(move, {})[index] = min(loss, refutations.get(move, {}).get(index, loss))
        search = TacticalSearch(color, deadline - .025, self._stop, weights)
        for depth, quiet in ((0, 2), (0, 4), (1, 4), (2, 4), (3, 4), (4, 4)):
            try:
                completed = {move: search.candidate(worlds, move, depth, quiet) for move in finalists}
            except SearchExpired:
                break
            rows.update(completed)
        for move, results in refutations.items():
            row = rows[move]
            rows[move] = SearchResult(tuple(min(score, results.get(index, score))
                                           for index, score in enumerate(row.values)), row.depth, row.nodes)
        if min(rows[move].losses for move in finalists) > min(rows[move].losses for move in eligible):
            finalists = eligible
        chosen = max(finalists, key=value)
        scores = [(2, 0) if score >= MATE - 128 else (-2, 0) if score <= -MATE + 128 else (0, score)
                  for score in blended(chosen)]
        estimate = (0, round(value(chosen)[1])) if all(kind == 0 for kind, _ in scores) else None
        statistics.evaluate(scores, len(worlds), estimate)
        LOG.info("secret queen decision %s", json.dumps({
            "ply": first.ply(), "worlds": len(worlds), "chosen": chosen.uci(),
            "hint_coverage": len(covered), "tactical_nodes": search.nodes,
            "weights": weights, "belief_observations": self._opponent_beliefs.observations,
            "candidates": [{"move": move.uci(), "values": row.values, "depth": row.depth,
                            "guidance": guidance.get(move), "value": value(move)[1] if move in guidance else row.mean}
                           for move, row in rows.items()],
        }, separators=(",", ":")))
        return statistics.result(chosen)
