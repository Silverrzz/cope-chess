import chess

from match_runner.game_state import GameState
from match_runner.match import Match

from .tournament import Tournament


class TournamentRunner():
    def __init__(self, tournament: Tournament):
        self._tournament = tournament
        self._create_matches()

    def get_tournament(self) -> Tournament:
        return self._tournament

    def set_tournament(self, tournament: Tournament):
        self._tournament = tournament
        self._create_matches()

    def _create_matches(self):
        self._tournament.clear_matches()

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
