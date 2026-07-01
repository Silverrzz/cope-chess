from match_runner.engine_instance import EngineInstance
from match_runner.match import Match
from match_runner.time_control import TimeControl


class Tournament():
    def __init__(
        self,
        id: int,
        name: str,
        engines: list[EngineInstance],
        time_control: TimeControl,
    ):
        self._id = id
        self._name = name
        self._engines = engines
        self._time_control = time_control
        self._matches: list[Match] = []

    def get_id(self) -> int:
        return self._id

    def set_id(self, id: int):
        self._id = id

    def get_name(self) -> str:
        return self._name

    def set_name(self, name: str):
        self._name = name

    def get_engines(self) -> list[EngineInstance]:
        return self._engines

    def set_engines(self, engines: list[EngineInstance]):
        self._engines = engines

    def get_time_control(self) -> TimeControl:
        return self._time_control

    def set_time_control(self, time_control: TimeControl):
        self._time_control = time_control

    def get_matches(self) -> tuple[Match, ...]:
        return tuple(self._matches)

    def add_match(self, match: Match):
        self._matches.append(match)

    def clear_matches(self):
        self._matches.clear()
