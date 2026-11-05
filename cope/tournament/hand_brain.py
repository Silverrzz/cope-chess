import chess

from .game_runner import GameRunner


class _PhaseRunner(GameRunner):
    def __init__(
        self, game, *, allowed=None, apply_move=True, searchmoves_first=False, **kwargs
    ):
        super().__init__(game, **kwargs)
        self.allowed = allowed
        self.apply_move = apply_move
        self.searchmoves_first = searchmoves_first

    def _build_go_command(self, clock):
        command = super()._build_go_command(clock)
        if self.allowed is not None:
            moves = " searchmoves " + " ".join(move.uci() for move in self.allowed)
            command = (
                "go" + moves + command[2:]
                if self.searchmoves_first
                else command + moves
            )
        return command

    def push_legal_move(self, move, side_to_move):
        if self.allowed is not None and move not in self.allowed:
            self._get_game_state().record_illegal_move(side_to_move, move)
            return False
        if not self.apply_move:
            if move not in self._get_board().legal_moves:
                self._get_game_state().record_illegal_move(side_to_move, move)
                return False
            return True
        return super().push_legal_move(move, side_to_move)


class HandBrainRunner:
    def __init__(
        self,
        game,
        relay,
        engines,
        time_control,
        *,
        on_phase,
        lag_compensation_ms=0,
        searchmoves_first_ids=(),
    ):
        self.game = game
        self.relay = relay
        self.engines = engines
        self.clocks = {key: time_control.create_manager() for key in engines}
        self.on_phase = on_phase
        self.lag_compensation_ms = lag_compensation_ms
        self.searchmoves_first_ids = frozenset(searchmoves_first_ids)
        self.phase = None
        self.active_engine_id = None
        self.brain_move = None
        self.last_engine = None
        self.last_results = []

    def prepare_game(self, engines=None):
        for engine in self.engines.values() if engines is None else engines:
            engine.start_new_game()

    def snapshot(self):
        return {str(key): clock.snapshot() for key, clock in self.clocks.items()}

    def restore(self, state):
        if set(state) != {str(key) for key in self.clocks}:
            raise RuntimeError(
                "hand and brain checkpoint must preserve all four clocks"
            )
        for key, clock in self.clocks.items():
            clock.restore(state[str(key)])

    def run_next_move(self):
        if self.game.state.is_finished():
            return None
        board = self.game.state.get_board()
        side = "white" if board.turn == chess.WHITE else "black"
        members = {
            color.name.lower(): tuple(
                team.members[::-1] if self.relay.role_swap else team.members
            )
            for color, team in self.relay.teams.items()
        }
        self.last_results = []
        self.brain_move = None
        allowed = None
        for index, role in enumerate(("brain", "hand")):
            white_id = members["white"][index].engine_id
            black_id = members["black"][index].engine_id
            self.game.white = self.engines[white_id]
            self.game.black = self.engines[black_id]
            self.game.white_tm = self.clocks[white_id]
            self.game.black_tm = self.clocks[black_id]
            engine_id = white_id if board.turn == chess.WHITE else black_id
            self.phase = role
            self.active_engine_id = engine_id
            engine = self.engines[engine_id]
            root_fen = board.fen()

            def sync(color, running, remaining):
                self.on_phase(self, side, role, engine_id, running, remaining)

            runner = _PhaseRunner(
                self.game,
                allowed=allowed,
                apply_move=role == "hand",
                searchmoves_first=engine_id in self.searchmoves_first_ids,
                on_clock_sync=sync,
                lag_compensation_ms=self.lag_compensation_ms,
            )
            runner.prepare_game([])
            move = runner.run_next_move()
            if move is None:
                return None
            result = engine.get_last_search_result()
            self.last_results.append((engine_id, side, role, root_fen, result))
            if role == "brain":
                self.brain_move = move
                piece_type = board.piece_type_at(move.from_square)
                allowed = tuple(
                    candidate
                    for candidate in board.legal_moves
                    if board.piece_type_at(candidate.from_square) == piece_type
                )
                if not allowed:
                    self.game.state.record_engine_error(
                        board.turn, RuntimeError("brain selected no legal piece")
                    )
                    return None
            else:
                self.last_engine = engine
                return move
        return None
