from match_runner.engine_instance import EngineInstance
from match_runner.time_control import TimeControl, TimeControlCategory
from tournament_runner.tournament import Tournament
from tournament_runner.tournament_runner import TournamentRunner

if __name__ == "__main__":

    engines = [
        EngineInstance("sable", "1.2.3.4"),
        EngineInstance("lacrima", "5.6.7.8"),
    ]

    time_control = TimeControl(
        category=TimeControlCategory.INCREMENT,
        initial_time=500,
        increment=50
    )

    tournament = Tournament(
        id=1,
        name="Prototype Blitz",
        engines=engines,
        time_control=time_control
    )

    runner = TournamentRunner(tournament)
    active_tournament = runner.get_tournament()

    print(f"Tournament: {active_tournament.get_name()}")
    print(f"Engines: {len(active_tournament.get_engines())}")
    print(f"Matches: {len(active_tournament.get_matches())}")

    for match in active_tournament.get_matches():
        print(
            f"Match {match.get_id()}: "
            f"{match.get_white().get_name()} vs {match.get_black().get_name()}"
        )
