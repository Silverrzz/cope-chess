import chess

from match_runner.game_state import GameState
from match_runner.match import Match
from match_runner.match_runner import MatchRunner

from .tournament import Tournament


class TournamentRunner():
    def __init__(self, tournament: Tournament, clock_probe_interval: float = 0.001):
        self._tournament = tournament
        self._clock_probe_interval = clock_probe_interval
        self._current_match_index = 0
        self._create_matches()

    def get_tournament(self) -> Tournament:
        return self._tournament

    def set_tournament(self, tournament: Tournament):
        self._tournament = tournament
        self._current_match_index = 0
        self._create_matches()

    def get_current_match_index(self) -> int:
        return self._current_match_index

    def get_current_match(self) -> Match | None:
        if self.is_finished():
            return None

        return self._tournament.get_matches()[self._current_match_index]

    def get_completed_matches(self) -> tuple[Match, ...]:
        return tuple(
            match
            for match in self._tournament.get_matches()
            if match.get_game_state().is_finished()
        )

    def get_remaining_matches(self) -> tuple[Match, ...]:
        return tuple(
            match
            for match in self._tournament.get_matches()
            if not match.get_game_state().is_finished()
        )

    def is_finished(self) -> bool:
        return self._current_match_index >= len(self._tournament.get_matches())

    def run(self):
        while not self.is_finished():
            self.run_next_match()

    def run_next_match(self) -> Match | None:
        match = self.get_current_match()

        if match is None:
            return None

        match_runner = MatchRunner(match, clock_probe_interval=self._clock_probe_interval)
        match_runner.run()
        self._current_match_index += 1
        return match

    def _create_matches(self):
        self._tournament.clear_matches()
        self._current_match_index = 0

        match_id = 1
        engines = self._tournament.get_engines()

        for white_index in range(len(engines)):
            for black_index in range(white_index + 1, len(engines)):
                white = engines[white_index]
                black = engines[black_index]

                self._tournament.add_match(self._create_match(match_id, white, black))
                match_id += 1

                self._tournament.add_match(self._create_match(match_id, black, white))
                match_id += 1

    def _create_match(self, id, white, black) -> Match:
        time_control = self._tournament.get_time_control()

        return Match(
            id=id,
            white=white,
            black=black,
            game_state=GameState(board=chess.Board()),
            white_tm=time_control.create_manager(),
            black_tm=time_control.create_manager(),
        )
