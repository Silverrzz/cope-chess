from engine_instance import EngineInstance
from game_state import GameState
from time_control import TimeManager

class Match():
    def __init__(self, id: int, white: EngineInstance, black: EngineInstance, game_state: GameState, white_tm: TimeManager, black_tm: TimeManager):
        self.id = id
        self.white = white
        self.black = black
        self.game_state = game_state
        self.white_tm = white_tm
        self.black_tm = black_tm