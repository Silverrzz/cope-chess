import chess
from collections.abc import Callable

from .engine_instance import EngineInstance
from .game_state import GameState
from .time_control import TimeControlCategory, TimeManager, TimeOutError
from .tournament import Game
from .uci import go_command


class GameRunner:
    def __init__(
        self,
        game: Game,
        clock_probe_interval: float = 0.01,
        lag_compensation_ms: int = 0,
        on_tick: Callable[[chess.Color, int | None], None] | None = None,
        on_clock_sync: Callable[[chess.Color, bool, int | None], None] | None = None,
    ):
        self._game = game
        self._clock_probe_interval = clock_probe_interval
        self._lag_compensation_ms = max(0, lag_compensation_ms)
        self._game_started = False
        self._on_tick = on_tick
        self._on_clock_sync = on_clock_sync

    def get_game(self) -> Game:
        return self._game

    def set_game(self, game: Game):
        self._game = game
        self._game_started = False

    def run(self):
        while not self._get_game_state().is_finished():
            self.run_next_move()

    def prepare_game(self):
        self._start_game()

    def run_next_move(self):
        if self._get_game_state().is_finished():
            return None

        self._start_game()

        board = self._get_board()
        side_to_move = board.turn
        engine = self.get_engine_to_move()
        clock = self.get_clock_to_move()
        move = None
        worker_clock_synced = False

        clock.start_clock()
        engine.start_search(board, self._build_go_command(clock))
        if self._on_clock_sync is not None:
            self._on_clock_sync(side_to_move, True, _clock_remaining_ms(clock))

        try:
            while engine.is_searching():
                worker_elapsed_ms = (
                    engine.get_worker_search_elapsed_ms()
                    if engine.uses_worker_search_clock()
                    else None
                )
                if worker_elapsed_ms is not None:
                    worker_elapsed_ms = max(
                        0,
                        worker_elapsed_ms - self._lag_compensation_ms,
                    )
                try:
                    remaining = clock.probe_clock(worker_elapsed_ms)
                except TimeOutError:
                    if not engine.uses_worker_search_clock() or worker_elapsed_ms is not None:
                        raise
                    remaining = 0
                if (
                    worker_elapsed_ms is not None
                    and not worker_clock_synced
                    and self._on_clock_sync is not None
                ):
                    self._on_clock_sync(side_to_move, True, remaining)
                    worker_clock_synced = True
                if self._on_tick is not None:
                    self._on_tick(side_to_move, remaining)
                engine.wait_for_search(self._clock_probe_interval)

            move = engine.get_search_move()
        except TimeOutError:
            engine.stop_search()
            clock.stop_clock_after_timeout()
            self._get_game_state().record_timeout(side_to_move)
            if self._on_clock_sync is not None:
                self._on_clock_sync(side_to_move, False, 0)
            return None
        except Exception as error:
            engine.stop_search()
            clock.stop_clock_after_timeout()
            self._get_game_state().record_engine_error(side_to_move, error)
            if self._on_clock_sync is not None:
                self._on_clock_sync(side_to_move, False, _clock_remaining_ms(clock))
            return None
        finally:
            if not self._get_game_state().is_finished():
                try:
                    search = engine.get_last_search_result()
                    elapsed_ms = (
                        search.command_elapsed_ms
                        if search is not None and engine.uses_worker_search_clock()
                        else None
                    )
                    if elapsed_ms is not None:
                        elapsed_ms = max(0, elapsed_ms - self._lag_compensation_ms)
                    clock.stop_clock(elapsed_ms)
                    if self._on_clock_sync is not None:
                        self._on_clock_sync(side_to_move, False, _clock_remaining_ms(clock))
                except TimeOutError:
                    self._get_game_state().record_timeout(side_to_move)
                    if self._on_clock_sync is not None:
                        self._on_clock_sync(side_to_move, False, 0)

        if self._get_game_state().is_finished():
            return None

        if move is None:
            self._get_game_state().record_engine_error(side_to_move, RuntimeError("Engine returned no move"))
            return None

        if not self.push_legal_move(move, side_to_move):
            return None

        self._get_game_state().update_from_board()
        return move

    def _start_game(self):
        if self._game_started:
            return

        self._game.white.start_new_game()
        self._game.black.start_new_game()
        self._game_started = True

    def _build_go_command(self, clock: TimeManager) -> str:
        white_clock = self._game.white_tm
        black_clock = self._game.black_tm
        args: dict[str, int | None] = {
            "wtime": white_clock.get_remaining_time(),
            "btime": black_clock.get_remaining_time(),
            "winc": None,
            "binc": None,
            "movetime": None,
            "movestogo": clock.get_moves_to_go(),
            "nodes": clock.get_nodes(),
        }

        tc = clock.get_time_control()
        if tc.get_category() is TimeControlCategory.INCREMENT:
            args["winc"] = white_clock.get_time_control().get_increment()
            args["binc"] = black_clock.get_time_control().get_increment()
        elif tc.get_category() is TimeControlCategory.MOVETIME:
            args["movetime"] = clock.get_remaining_move_time()

        return go_command(**args)

    def _get_game_state(self) -> GameState:
        return self._game.state

    def _get_board(self) -> chess.Board:
        return self._get_game_state().get_board()

    def get_engine_to_move(self) -> EngineInstance:
        if self._get_board().turn == chess.WHITE:
            return self._game.white

        return self._game.black

    def get_clock_to_move(self) -> TimeManager:
        if self._get_board().turn == chess.WHITE:
            return self._game.white_tm

        return self._game.black_tm

    def push_legal_move(self, move: chess.Move, side_to_move: chess.Color) -> bool:
        board = self._get_board()

        if move not in board.legal_moves:
            self._get_game_state().record_illegal_move(side_to_move, move)
            return False

        board.push(move)
        return True


def _clock_remaining_ms(clock: TimeManager) -> int | None:
    remaining_time = clock.get_remaining_time()
    if remaining_time is not None:
        return remaining_time
    return clock.get_remaining_move_time()
