

import chess

from engine_instance import EngineInstance
from time_control import TimeControl, TimeControlCategory
from game_state import GameState
from match import Match
import time

if __name__ == "__main__":

    white_engine = EngineInstance("sable", "1.2.3.4")
    black_engine = EngineInstance("lacrima", "5.6.7.8")

    time_control = TimeControl(
        category=TimeControlCategory.INCREMENT,
        initial_time=2_000,
        increment=1_000
    )

    match = Match(
        id=1,
        white=white_engine,
        black=black_engine,
        game_state=GameState(board=chess.Board()),
        white_tm=time_control.get_manager_object(),
        black_tm=time_control.get_manager_object()
    )

    match.white_tm.start_clock()

    test_start_time = time.time()

    try:
        while True:
            time.sleep(0.001)
            print(match.white_tm.probe_clock())
    except Exception as e:
        print(f"Exception: {e}")
        print(f"Total elapsed time: {time.time() - test_start_time} seconds")