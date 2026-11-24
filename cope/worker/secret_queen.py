from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
import json
import re
from statistics import mean, median
import threading
import time

import chess

from cope.variants.secret_queen import POSITION_PREFIX, SEARCH_WORKERS, SecretQueenBoard

from .uci_engine import UciEngineProcess


Score = tuple[int, int]
_COLORS = (chess.WHITE, chess.BLACK)
_CENTRAL_PAWNS = chess.BB_FILE_C | chess.BB_FILE_D | chess.BB_FILE_E | chess.BB_FILE_F


@dataclass(frozen=True, slots=True)
class Assessment:
    score: Score
    wdl: float | None = None
    terminal: bool = False

    def for_other_side(self) -> Assessment:
        kind, value = self.score
        return Assessment((-kind, -value), None if self.wdl is None else -self.wdl, self.terminal)


@dataclass(frozen=True, slots=True)
class Probe:
    move: chess.Move
    evaluation: Assessment | None


@dataclass(frozen=True, slots=True)
class Candidate:
    move: chess.Move
    reveal: bool
    wins: int
    losses: int
    trade: float
    opening: float
    backtracks: int

    @property
    def priority(self) -> float:
        return self.trade + self.opening - 34 * self.backtracks - 44 * self.reveal


_STATISTICS = re.compile(r"\b(depth|seldepth|nodes|hashfull) ([0-9]{1,15})(?= |$)")
_OPTION_NAME = re.compile(r"^option\s+name\s+(.+?)\s+type\s+\S+", re.IGNORECASE)
_PIECE_VALUES = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330, chess.ROOK: 500, chess.QUEEN: 900}


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

    def evaluate(self, scores: list[Score], positions: int) -> None:
        count = len(scores) if positions else 0
        with self._lock:
            self._evaluation = f"{_score_token(min(scores))},{_score_token(max(scores))},{count},{positions}"
            if self._callback is not None:
                self._callback(self._line())

    def _line(self) -> str:
        elapsed = max(1, round((time.monotonic() - self._started) * 1000))
        values = {**self._values, "time": elapsed}
        if "nodes" in values:
            values["nps"] = values["nodes"] * 1000 // elapsed
        line = "info " + " ".join(f"{key} {value}" for key, value in values.items())
        return f"{line} sq_eval {self._evaluation}" if self._evaluation else line

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
            wdl = None
            if "wdl" in words:
                try:
                    index = words.index("wdl") + 1
                    wins, draws, losses = map(int, words[index:index + 3])
                    if min(wins, draws, losses) >= 0 and wins + draws + losses > 0:
                        wdl = (wins - losses) / (wins + draws + losses)
                except ValueError:
                    pass
            return Probe(move, Assessment((0, value), wdl))
        if kind == "mate":
            return Probe(move, Assessment((1, -value) if value > 0 else (-1, -value)))
    return Probe(move, None)


def _outcome_score(board: SecretQueenBoard, color: chess.Color) -> Assessment | None:
    if board.secret_capture() is not None:
        return Assessment((2 if board.turn == color else -2, 0), terminal=True)
    outcome = board.outcome()
    if outcome:
        kind = 0 if outcome.winner is None else 2 if outcome.winner == color else -2
        return Assessment((kind, 0), terminal=True)
    return None


def _score_utility(assessment: Assessment, scale: float) -> float:
    kind, value = assessment.score
    if kind == 0:
        normalized = value / (abs(value) + scale)
        if assessment.wdl is not None:
            normalized = .8 * normalized + .2 * assessment.wdl
        return .80 * normalized
    magnitude = 1.0 if assessment.terminal else max(.82, .96 - abs(value) * .007)
    return magnitude if kind > 0 else -magnitude


def _move_utility(candidate: Candidate, values: list[float], behind: float) -> float:
    ordered = sorted(values)
    tail_size = max(1, (len(values) + 3) // 4)
    utility = .72 * mean(values) + .28 * (
        (1 - behind) * mean(ordered[:tail_size]) + behind * mean(ordered[-tail_size:])
    )
    if candidate.reveal:
        chances = mean(max(0., min(1., (value + .36) / .36)) for value in values)
        utility -= .035 + .16 * behind * (1 - chances)
    return utility + max(-.055, min(.045, candidate.opening / 1600)) - .025 * candidate.backtracks


def _piece_value(board: SecretQueenBoard, square: chess.Square, color: chess.Color) -> int:
    if board.concealed_square(color) == square:
        return _PIECE_VALUES[chess.QUEEN]
    return _PIECE_VALUES.get(board.visible_piece_type_at(square), 0)


def _opening_priority(board: chess.Board, move: chess.Move, reveal: bool) -> float:
    if board.ply() >= 24 or reveal:
        return 0.
    mover = board.piece_type_at(move.from_square)
    if board.is_castling(move):
        return 55.
    if board.is_capture(move):
        return 0.
    from_rank = chess.square_rank(move.from_square) if board.turn else 7 - chess.square_rank(move.from_square)
    to_rank = chess.square_rank(move.to_square) if board.turn else 7 - chess.square_rank(move.to_square)
    to_file = chess.square_file(move.to_square)
    if mover in (chess.BISHOP, chess.KNIGHT) and from_rank == 0 and to_rank > 0:
        return 42. if 2 <= to_file <= 5 else 23.
    if mover == chess.PAWN and from_rank == 1:
        return 25. + 9 * (to_rank - 2) if chess.BB_SQUARES[move.to_square] & _CENTRAL_PAWNS else -22.
    if mover in (chess.ROOK, chess.QUEEN):
        return -35. if from_rank == 0 else -15.
    return 0.


def _candidate(worlds: tuple[SecretQueenBoard, ...], public: chess.Board, move: chess.Move, recent: list[chess.Move]) -> Candidate:
    color = public.turn
    reveal = worlds[0].reveals(move)
    wins = losses = 0
    trades = []
    for world in worlds:
        gain = _piece_value(world, move.to_square, not color)
        if public.is_en_passant(move):
            gain = _PIECE_VALUES[chess.PAWN]
        if move.promotion:
            gain += _PIECE_VALUES[move.promotion] - _PIECE_VALUES[chess.PAWN]
        after = world.copy(stack=False)
        after.push(move)
        outcome = _outcome_score(after, color)
        wins += outcome is not None and outcome.score[0] == 2
        losses += outcome is not None and outcome.score[0] == -2
        recaptures = () if outcome is not None else after.captures_of(move.to_square)
        if recaptures:
            response = min(recaptures, key=lambda reply: _piece_value(after, reply.from_square, not color))
            cost = _piece_value(after, move.to_square, color)
            answered = after.copy(stack=False)
            answered.push(response)
            if answered.captures_of(response.to_square):
                cost -= min(cost, _piece_value(after, response.from_square, not color))
            gain -= cost
        trades.append(gain)
    reverse = chess.Move(move.to_square, move.from_square)
    backtracks = sum(previous == move or previous == reverse for previous in recent)
    return Candidate(move, reveal, wins, losses, .75 * mean(trades) + .25 * min(trades),
                     _opening_priority(public, move, reveal), backtracks)


def _choose(incumbent: Candidate, finalists: list[Candidate], rows: list[dict[chess.Move, Assessment]], scale: float) -> Candidate:
    baseline = [_score_utility(row[incumbent.move], scale) for row in rows if incumbent.move in row]
    if not baseline:
        return incumbent
    behind = max(0., min(1., (-mean(baseline) - .12) / .40))
    chosen = incumbent
    best = 0.
    for candidate in finalists:
        if candidate == incumbent:
            continue
        paired = [(_score_utility(row[incumbent.move], scale), _score_utility(row[candidate.move], scale))
                  for row in rows if incumbent.move in row and candidate.move in row]
        if len(paired) <= len(rows) // 2:
            continue
        reference, alternative = map(list, zip(*paired, strict=True))
        difference = _move_utility(candidate, alternative, behind) - _move_utility(incumbent, reference, behind)
        difference = difference * len(paired) / len(rows) - .11 * (1 - len(paired) / len(rows))
        if difference > best:
            chosen, best = candidate, difference
    return chosen


def _shared_followup(worlds, move, response, answered, continuation) -> bool:
    public = answered.fen(en_passant="fen")
    for world in worlds:
        possible = world.copy(stack=False)
        possible.push(move)
        if not possible.is_legal(response):
            continue
        possible.push(response)
        if possible.fen(en_passant="fen") != public or _outcome_score(possible, world.turn) is not None:
            continue
        if not possible.is_legal(continuation):
            return False
        possible.push(continuation)
        if possible.secret_capture() is not None:
            return False
    return True


class SecretQueenEngine(UciEngineProcess):
    def __init__(self, spec, *, server_url: str, credential: str, progress_callback=None):
        super().__init__(spec, server_url=server_url, credential=credential, progress_callback=progress_callback)
        self._position: dict | None = None
        self._stop = threading.Event()
        self._options: dict[str, str] = {}
        self._advertised: set[str] = set()
        self._queen_swings: list[int] = []
        self._score_scale: float | None = None
        self._belief_cache: tuple[chess.Color, int, tuple[tuple[str, str], ...], tuple[SecretQueenBoard, ...]] | None = None
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
        if command == "uci":
            lines = super().handle_command(command, line_callback)
            self._advertised = self._advertised_options(lines)
            if "uci_showwdl" in self._advertised:
                self._send("setoption name UCI_ShowWDL value true")
            return lines
        if command.startswith("setoption name "):
            name = command[len("setoption name "):].split(" value", 1)[0].casefold()
            if name not in self._advertised:
                return []
            self._options[name] = command
        if command == "ucinewgame":
            self._position = None
            self._queen_swings.clear()
            self._score_scale = None
            self._belief_cache = None
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

    @staticmethod
    def _advertised_options(lines: list[str]) -> set[str]:
        return {match[1].casefold() for line in lines if (match := _OPTION_NAME.match(line))}

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
                declarations = engine._read_until(lambda line: line == "uciok", timeout_s=timeout)
                advertised = self._advertised_options(declarations)
                for name, option in self._options.items():
                    if name in advertised:
                        engine._send(option)
                if "uci_showwdl" in advertised and "uci_showwdl" not in self._options:
                    engine._send("setoption name UCI_ShowWDL value true")
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

    def _probe(self, lane, board, deadline, *, observe=False, limit_ms=None, statistics=None, position=None):
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
            engine._send(position if position is not None else "position fen " + board.fen())
            engine._send(f"go movetime {window}")
            try:
                lines = engine._read_until(lambda line: line.startswith("bestmove"), timeout_s=(window + 12) / 1000)
            except RuntimeError:
                UciEngineProcess.stop_search(engine)
                lines = engine._read_until(lambda line: line.startswith("bestmove"), timeout_s=.025)
            if statistics is not None:
                statistics.record(lines)
            result = _probe_result(lines)
            return result if result and board.is_legal(result.move) else None
        except (OSError, RuntimeError):
            if engine is not None:
                self._discard(engine)
            return None
        finally:
            with self._active_lock:
                self._active.discard(engine)

    def _worlds(self, color: chess.Color, file: int, history: tuple[tuple[str, str], ...]) -> tuple[SecretQueenBoard, ...]:
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

    def _turn(self, command, line_callback):
        started = time.monotonic()
        color = self._position["color"]
        history = tuple((move, fen) for move, fen in self._position["moves"])
        worlds = self._worlds(color, self._position["file"], history)
        if not worlds:
            raise RuntimeError("No legal Secret Queen position matches the public history")
        first = worlds[0]
        opposing_color = not color
        worlds = tuple({world.concealed_square(opposing_color): world for world in worlds}.values())
        position_count = len(worlds)
        moves = tuple(sorted(first.legal_moves, key=chess.Move.uci))
        if not moves:
            return ["bestmove 0000"]
        statistics = SearchStatistics(started, line_callback)
        capture = first.secret_capture()
        if capture is not None:
            statistics.evaluate([(2, 0)] * position_count, position_count)
            return statistics.result(capture)
        if not first.concealed(color) and not any(world.concealed(opposing_color) for world in worlds):
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

        public = first.projection(show=())
        if public is None:
            public = chess.Board(first.fen())
        recent = first.move_stack[-2::-2][:6]
        candidates = [_candidate(worlds, public, move, recent) for move in moves]
        forced_win = next((candidate for candidate in candidates if candidate.wins == position_count), None)
        if forced_win:
            statistics.evaluate([(2, 0)] * position_count, position_count)
            return statistics.result(forced_win.move)
        safe = [candidate for candidate in candidates if candidate.losses == 0]
        eligible = safe or candidates
        eligible.sort(key=lambda candidate: (candidate.losses, -candidate.wins, -candidate.priority))
        fallback = eligible[0]
        if len(eligible) == 1 or deadline - time.monotonic() < .09 or self._stop.is_set():
            return statistics.result(fallback.move)

        limit_ms = max(25, budget_ms * 17 // 100)
        anchor = self._probe(0, public, min(deadline, started + budget_ms * .27 / 1000), limit_ms=limit_ms,
                             statistics=statistics, position=first.native_position())
        chosen = next((candidate for candidate in eligible if anchor and candidate.move == anchor.move
                       and candidate.losses <= fallback.losses and candidate.trade >= min(-85., fallback.trade - 160.)),
                      fallback)
        if deadline - time.monotonic() < .18:
            return statistics.result(chosen.move)

        nomination_deadline = min(deadline, started + budget_ms * .49 / 1000)
        nomination_limit = min(limit_ms, int((nomination_deadline - time.monotonic()) * 1000) - 25)
        nominations = []
        if nomination_limit >= 25:
            for index, world in enumerate(worlds):
                show = (color,) if index == 0 else _COLORS
                nominations.append(self._pool.submit(self._probe, index, world.projection(show=show),
                                                     nomination_deadline, limit_ms=nomination_limit, statistics=statistics))
        nominations = [future.result() for future in nominations]
        if (first.concealed(color) and first.ply() <= 16 and anchor and anchor.evaluation and nominations
                and nominations[0] and nominations[0].evaluation):
            base = anchor.evaluation.score
            extra = nominations[0].evaluation.score
            if nomination_limit >= limit_ms * .65 and base[0] == extra[0] == 0 and extra[1] > base[1]:
                self._queen_swings.append(extra[1] - base[1])

        votes = Counter(probe.move for probe in nominations[1:] if probe is not None)
        own_view = nominations[0].move if nominations and nominations[0] else None
        nominated = sorted(eligible, key=lambda candidate: (
            candidate.losses, -candidate.wins, -votes[candidate.move], candidate.move != own_view, -candidate.priority,
        ))
        finalists = list(dict.fromkeys([
            chosen,
            fallback,
            nominated[0],
            next((candidate for candidate in nominated if candidate.reveal != chosen.reveal), chosen),
        ]))
        maximum = 5 if deadline - time.monotonic() >= 1.35 else 4
        finalists.extend(candidate for candidate in nominated[:maximum] if candidate not in finalists
                         and (votes[candidate.move] or candidate.move == own_view) and len(finalists) < maximum)
        if len(finalists) < 2 or deadline - time.monotonic() < .11 or self._stop.is_set():
            return statistics.result(chosen.move)

        futures = [
            self._pool.submit(self._rank, index, worlds, finalists, deadline, statistics)
            for index in range(position_count)
        ]
        rows = [future.result() for future in futures]
        scale = self._calibrate(rows)
        if not any(chosen.move in row for row in rows):
            covered = max(finalists, key=lambda candidate: (
                sum(candidate.move in row for row in rows), candidate == chosen, candidate.priority,
            ))
            if sum(covered.move in row for row in rows) > position_count // 2:
                chosen = covered
        chosen = _choose(chosen, finalists, rows, scale)
        scores = [row[chosen.move].score for row in rows if chosen.move in row]
        if scores:
            statistics.evaluate(scores, position_count)
        return statistics.result(chosen.move)

    def _calibrate(self, rows: list[dict[chess.Move, Assessment]]) -> float:
        if self._queen_swings:
            return max(1., median(self._queen_swings) * .40)
        samples = []
        for row in rows:
            scores = [estimate.score[1] for estimate in row.values() if not estimate.terminal and estimate.score[0] == 0]
            if len(scores) > 1 and max(scores) != min(scores):
                samples.append(max(scores) - min(scores))
        if samples:
            spread = median(samples)
            if self._score_scale is None:
                self._score_scale = max(1., spread)
            else:
                spread = max(self._score_scale * .5, min(spread, self._score_scale * 2))
                self._score_scale = max(1., .8 * self._score_scale + .2 * spread)
        return self._score_scale or 100.

    def _rank(self, index, worlds, finalists, deadline, statistics):
        scores = {}
        world = worlds[index]
        color = world.turn
        alternatives = finalists[1:]
        offset = index % len(alternatives)
        order = [finalists[0], *alternatives[offset:], *alternatives[:offset]]
        for number, candidate in enumerate(order):
            if self._stop.is_set() or deadline - time.monotonic() < .045:
                break
            move_deadline = time.monotonic() + (deadline - time.monotonic()) / (len(order) - number)
            after = world.copy(stack=False)
            after.push(candidate.move)
            score = _outcome_score(after, color)
            if score is None:
                score = self._reply_score(index, worlds, candidate.move, after, move_deadline, statistics)
            if score is not None:
                scores[candidate.move] = score
        return scores

    def _reply_score(self, index, worlds, move, after, deadline, statistics):
        opponent = after.turn
        replies = after.captures_of(move.to_square)
        slot = int((deadline - time.monotonic()) * 1000)
        observer_ms = max(15, slot // (4 if replies else 3))
        prediction = self._probe(index, after.projection(show=(opponent,)), deadline, observe=True,
                                 limit_ms=observer_ms)
        responses = []
        if prediction and after.is_legal(prediction.move):
            responses.append(prediction.move)
        if replies:
            recapture = min(replies, key=lambda reply: _piece_value(after, reply.from_square, opponent))
            if recapture not in responses:
                responses.append(recapture)

        scores = []
        for number, response in enumerate(responses):
            answered = after.copy(stack=False)
            answered.push(response)
            score = _outcome_score(answered, not opponent)
            if score is None:
                window = max(15, int((deadline - time.monotonic()) * 1000 / (len(responses) - number)) - 25)
                estimate = self._probe(index, answered.projection(show=_COLORS), deadline, limit_ms=window,
                                       statistics=statistics)
                if estimate and answered.is_legal(estimate.move):
                    score = estimate.evaluation
                    if score and score.score > (0, 0) and not _shared_followup(worlds, move, response, answered, estimate.move):
                        score = Assessment((0, 0))
            if score is not None:
                if score.terminal and score.score[0] == -2:
                    return score
                scores.append(score)
        if scores:
            return min(scores, key=lambda score: score.score)
        estimate = self._probe(index, after.projection(show=_COLORS), deadline, statistics=statistics)
        if estimate and estimate.evaluation and after.is_legal(estimate.move):
            return estimate.evaluation.for_other_side()
        return None
