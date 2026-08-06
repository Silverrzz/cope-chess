import chess
from enum import Enum


class GameTermination(Enum):
    CHECKMATE = "checkmate"
    STALEMATE = "stalemate"
    INSUFFICIENT_MATERIAL = "insufficient material"
    SEVENTYFIVE_MOVES = "seventy-five moves"
    FIVEFOLD_REPETITION = "fivefold repetition"
    FIFTY_MOVES = "fifty moves"
    THREEFOLD_REPETITION = "threefold repetition"
    TIMEOUT = "timeout"
    ILLEGAL_MOVE = "illegal move"
    ENGINE_ERROR = "engine error"
    VARIANT_END = "variant end"
    UNKNOWN = "unknown"


class GameState():
    def __init__(self, board: chess.Board):
        self._board = board
        self._result = "*"
        self._winner = None
        self._termination = None
        self._details = ""
        self._position_counts: dict[tuple[str, ...], int] = {}
        self._recorded_stack_size = 0
        self._reset_position_counts()
        self.update_from_board()

    def get_board(self) -> chess.Board:
        return self._board

    def set_board(self, board: chess.Board):
        self._board = board
        self._reset_position_counts()

    def get_result(self) -> str:
        return self._result

    def get_winner(self) -> chess.Color | None:
        return self._winner

    def get_termination(self) -> GameTermination | None:
        return self._termination

    def get_details(self) -> str:
        return self._details

    def is_finished(self) -> bool:
        return self._result != "*"

    def update_from_board(self):
        self._record_position()
        outcome = self._current_outcome()

        if outcome is None:
            return

        self._result = outcome.result()
        self._winner = outcome.winner
        self._termination = self._get_board_termination(outcome.termination)
        self._details = self._termination.value

    def _current_outcome(self) -> chess.Outcome | None:
        board = self._board
        if board.is_variant_loss():
            return chess.Outcome(chess.Termination.VARIANT_LOSS, not board.turn)
        if board.is_variant_win():
            return chess.Outcome(chess.Termination.VARIANT_WIN, board.turn)
        if board.is_variant_draw():
            return chess.Outcome(chess.Termination.VARIANT_DRAW, None)
        if board.is_checkmate():
            return chess.Outcome(chess.Termination.CHECKMATE, not board.turn)
        if board.is_insufficient_material():
            return chess.Outcome(chess.Termination.INSUFFICIENT_MATERIAL, None)
        if board.is_stalemate():
            return chess.Outcome(chess.Termination.STALEMATE, None)
        if board.is_seventyfive_moves():
            return chess.Outcome(chess.Termination.SEVENTYFIVE_MOVES, None)
        if self._position_counts.get(self._position_key(board), 0) >= 5:
            return chess.Outcome(chess.Termination.FIVEFOLD_REPETITION, None)
        if board.halfmove_clock >= 99 and board.can_claim_fifty_moves():
            return chess.Outcome(chess.Termination.FIFTY_MOVES, None)
        if self._position_counts.get(self._position_key(board), 0) >= 3:
            return chess.Outcome(chess.Termination.THREEFOLD_REPETITION, None)
        return None

    def _reset_position_counts(self) -> None:
        replay = self._board.root()
        self._position_counts = {self._position_key(replay): 1}
        for move in self._board.move_stack:
            replay.push(move)
            key = self._position_key(replay)
            self._position_counts[key] = self._position_counts.get(key, 0) + 1
        self._recorded_stack_size = len(self._board.move_stack)

    def _record_position(self) -> None:
        stack_size = len(self._board.move_stack)
        if stack_size == self._recorded_stack_size:
            return
        if stack_size != self._recorded_stack_size + 1:
            self._reset_position_counts()
            return
        key = self._position_key(self._board)
        self._position_counts[key] = self._position_counts.get(key, 0) + 1
        self._recorded_stack_size = stack_size

    @staticmethod
    def _position_key(board: chess.Board) -> tuple[str, ...]:
        return tuple(board.fen(en_passant="legal").split()[:4])

    def record_timeout(self, loser: chess.Color):
        winner = not loser
        if self._board.has_insufficient_material(winner):
            self._winner = None
            self._termination = GameTermination.INSUFFICIENT_MATERIAL
            self._result = "1/2-1/2"
            self._details = self._termination.value
            return
        self._set_decisive_result(winner, GameTermination.TIMEOUT)

    def record_illegal_move(self, loser: chess.Color, move: chess.Move):
        winner = not loser
        self._set_decisive_result(winner, GameTermination.ILLEGAL_MOVE, str(move))

    def record_engine_error(self, loser: chess.Color, error: Exception):
        winner = not loser
        self._set_decisive_result(winner, GameTermination.ENGINE_ERROR, str(error))

    def get_summary(self) -> str:
        if not self.is_finished():
            return "Game in progress"

        if self._details:
            return f"{self._result} by {self._details}"

        return self._result

    def _set_decisive_result(self, winner: chess.Color, termination: GameTermination, details: str = ""):
        self._winner = winner
        self._termination = termination
        self._result = "1-0" if winner == chess.WHITE else "0-1"
        self._details = termination.value

        if details:
            self._details = f"{self._details}: {details}"

    def _get_board_termination(self, termination: chess.Termination) -> GameTermination:
        termination_map = {
            chess.Termination.CHECKMATE: GameTermination.CHECKMATE,
            chess.Termination.STALEMATE: GameTermination.STALEMATE,
            chess.Termination.INSUFFICIENT_MATERIAL: GameTermination.INSUFFICIENT_MATERIAL,
            chess.Termination.SEVENTYFIVE_MOVES: GameTermination.SEVENTYFIVE_MOVES,
            chess.Termination.FIVEFOLD_REPETITION: GameTermination.FIVEFOLD_REPETITION,
            chess.Termination.FIFTY_MOVES: GameTermination.FIFTY_MOVES,
            chess.Termination.THREEFOLD_REPETITION: GameTermination.THREEFOLD_REPETITION,
            chess.Termination.VARIANT_WIN: GameTermination.VARIANT_END,
            chess.Termination.VARIANT_LOSS: GameTermination.VARIANT_END,
            chess.Termination.VARIANT_DRAW: GameTermination.VARIANT_END,
        }

        return termination_map.get(termination, GameTermination.UNKNOWN)
