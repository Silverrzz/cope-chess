from __future__ import annotations

from collections import defaultdict
import json

from pydantic import BaseModel, Field

from cope.core.models import (
    AdjudicationConfig, DrawAdjudicationRule, IncrementTimeControl, ResignAdjudicationRule,
    RoundRobinFormatOptions, SwissFormatOptions, TournamentConfig, TournamentFormat,
)
from cope.db import (
    create_game, create_tournament, get_engine_version_record, get_tournament, list_games,
    list_tournament_matches, set_tournament_status,
)
from cope.db.events import (
    create_event_award, create_event_cast_member, create_event_contest, create_event_stage,
    get_event, list_event_cast, list_event_contests, list_event_stages, set_event_stage_status,
    set_event_status, update_event_contest_state, update_event_state, utc_now,
)


STAGES = (("groups", "Group stage"), ("quarterfinal", "Quarterfinal"), ("semifinal", "Semifinal"), ("final", "Final"))
BRACKET = (
    ("q1", "Q1", "quarterfinal", (1, 8)), ("q2", "Q2", "quarterfinal", (4, 5)),
    ("q3", "Q3", "quarterfinal", (2, 7)), ("q4", "Q4", "quarterfinal", (3, 6)),
    ("s1", "S1", "semifinal", ("q1", "q2")), ("s2", "S2", "semifinal", ("q3", "q4")),
    ("final", "Final", "final", ("s1", "s2")),
)
KNOCKOUT_BRACKETS = {
    2: (("final", "Final", "final", (1, 2)),),
    4: (("s1", "S1", "semifinal", (1, 4)), ("s2", "S2", "semifinal", (2, 3)), BRACKET[-1]),
    8: BRACKET,
}
PRE_START = {"draft", "announced", "scheduled", "postponed"}


class SwissGroup(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    engine_ids: list[int] = Field(default_factory=list)
    rounds: int = Field(default=3, ge=1, le=100)
    qualifiers: int = Field(default=2, ge=0, le=8)


class CompetitionSettings(BaseModel):
    groups: list[SwissGroup] = Field(default_factory=list)
    group_clock: IncrementTimeControl = IncrementTimeControl(initial_ms=120000, increment_ms=1000)
    knockout_clock: IncrementTimeControl = IncrementTimeControl(initial_ms=120000, increment_ms=1000)
    hash_mb: int = Field(default=16, ge=1, le=1024)
    group_concurrency: int = Field(default=1, ge=1, le=128)


def settings(event) -> CompetitionSettings:
    return CompetitionSettings.model_validate(event.config.get("competition", {}))


def save_settings(connection, event, configured: CompetitionSettings) -> None:
    if event.status not in PRE_START or list_event_contests(connection, event.id):
        raise ValueError("Competition settings are locked once play begins.")
    engine_ids = [engine_id for group in configured.groups for engine_id in group.engine_ids]
    if len(engine_ids) != len(set(engine_ids)):
        raise ValueError("An engine can enter only one group.")
    names = [group.name.strip().casefold() for group in configured.groups]
    if "" in names or len(set(names)) != len(names):
        raise ValueError("Group names must be non-empty and unique.")
    engines = [get_engine_version_record(connection, engine_id) for engine_id in engine_ids]
    if any(engine is None or not engine.active or not engine.engine_active for engine in engines):
        raise ValueError("Every entrant needs an active engine version.")
    families = [engine.engine_id for engine in engines]
    if len(set(families)) != len(families):
        raise ValueError("Enter one version of each engine family.")
    connection.execute("DELETE FROM event_cast_members WHERE event_id = ?", (event.id,))
    for position, engine in enumerate(engines):
        create_event_cast_member(
            connection, event.id, member_key=f"engine-{engine.id}", kind="engine", role="competitor",
            display_name=f"{engine.name} {engine.version}".strip(), position=position, engine_version_id=engine.id,
        )
    config = {**event.config, "competition": configured.model_dump(mode="json")}
    connection.execute(
        "UPDATE events SET config = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
        (json.dumps(config), utc_now(), event.id),
    )


def _validate_start(configured: CompetitionSettings) -> int:
    if not configured.groups or any(len(group.engine_ids) < 2 for group in configured.groups):
        raise ValueError("Each Swiss group needs at least two engines.")
    if any(group.qualifiers > len(group.engine_ids) for group in configured.groups):
        raise ValueError("A group cannot qualify more engines than it contains.")
    qualifiers = sum(group.qualifiers for group in configured.groups)
    if qualifiers not in KNOCKOUT_BRACKETS:
        raise ValueError("Choose 2, 4, or 8 qualifiers for the knockout bracket.")
    return qualifiers


def _tournament_config(configured, engine_ids, *, rounds=None):
    swiss = rounds is not None
    return TournamentConfig(
        variant="secret-queen", format=TournamentFormat.SWISS if swiss else TournamentFormat.ROUND_ROBIN,
        format_options=SwissFormatOptions(rounds=rounds) if swiss else RoundRobinFormatOptions(),
        participants=list(engine_ids), time_control=configured.group_clock if swiss else configured.knockout_clock,
        engine_hash_mb=configured.hash_mb, concurrency=configured.group_concurrency if swiss else 1,
        adjudication=AdjudicationConfig(
            draw=DrawAdjudicationRule(max_abs_cp=10, consecutive_plies=8, min_fullmove=40),
            resign=ResignAdjudicationRule(min_abs_cp=800, consecutive_plies=6),
        ),
        rated=False, lag_compensation_ms=0, uci_options={"Ponder": False},
    )


def _link(connection, event_id: int, contest_id: int, tournament_id: int) -> None:
    connection.execute(
        "INSERT INTO secret_queen_event_tournaments (event_id, contest_id, tournament_id) VALUES (?, ?, ?)",
        (event_id, contest_id, tournament_id),
    )


def start_competition(connection, event_id: int) -> list[int]:
    from cope.runner.scheduler import generate_swiss_round

    event = get_event(connection, event_id, lock=True)
    if event is None or event.handler_key != "secret-queen" or event.status not in PRE_START:
        raise ValueError("This event is no longer awaiting its start.")
    if not event.published_at:
        raise ValueError("Publish the event before starting the competition.")
    configured = settings(event)
    bracket = KNOCKOUT_BRACKETS[_validate_start(configured)]
    if list_event_contests(connection, event_id):
        raise ValueError("The bracket has already been initialized.")
    stage_keys = {"groups", *(stage for _, _, stage, _ in bracket)}
    stages = {
        key: create_event_stage(connection, event_id, stage_key=key, title=title, position=index)
        for index, (key, title) in enumerate(STAGES)
        if key in stage_keys
    }
    tournaments = []
    for index, group in enumerate(configured.groups):
        contest_id = create_event_contest(
            connection, event_id, contest_key=f"group-{index + 1}", title=group.name.strip(),
            position=index, stage_id=stages["groups"], status="live", summary="Swiss",
            state={"engine_ids": group.engine_ids, "rounds": group.rounds, "qualifiers": group.qualifiers},
        )
        tournament_id = create_tournament(
            connection, f"{event.title} · {group.name.strip()}",
            _tournament_config(configured, group.engine_ids, rounds=group.rounds),
        )
        _link(connection, event_id, contest_id, tournament_id)
        generate_swiss_round(connection, get_tournament(connection, tournament_id), 1)
        update_event_contest_state(connection, contest_id, status="live")
        set_tournament_status(connection, tournament_id, "running")
        tournaments.append(tournament_id)
    for index, (key, title, stage, sources) in enumerate(bracket, start=len(tournaments)):
        create_event_contest(
            connection, event_id, contest_key=key, title=title, position=index, stage_id=stages[stage],
            summary=dict(STAGES)[stage], state={"sources": sources},
        )
    set_event_stage_status(connection, stages["groups"], "active")
    set_event_status(connection, event_id, "live")
    return tournaments


def start_due_competitions(connection, now: str) -> list[int]:
    result = []
    rows = connection.execute(
        """
        SELECT id FROM events
        WHERE handler_key = 'secret-queen' AND status = 'scheduled'
          AND scheduled_start_at <= ? AND published_at IS NOT NULL
        FOR UPDATE SKIP LOCKED
        """,
        (now,),
    ).fetchall()
    for row in rows:
        event = get_event(connection, row["id"])
        try:
            _validate_start(settings(event))
        except ValueError:
            continue
        result.extend(start_competition(connection, event.id))
    return result


def _standings(connection, tournament, games):
    from cope.runner.scheduler import _swiss_buchholz, _swiss_points

    matches = list_tournament_matches(connection, tournament.id)
    points = _swiss_points(tournament, games, matches)
    buchholz = _swiss_buchholz(tournament, games, matches, points)
    entrants = tournament.config.participants
    seed = {engine_id: index for index, engine_id in enumerate(entrants)}
    ordered = sorted(entrants, key=lambda engine_id: (-points[engine_id], -buchholz[engine_id], seed[engine_id]))
    return [{"engine_id": engine, "points": points[engine], "buchholz": buchholz[engine]} for engine in ordered]


def _next_match(connection, event) -> None:
    contests = list_event_contests(connection, event.id)
    groups = [item for item in contests if "qualifiers" in item.state]
    if any(item.status != "completed" for item in groups):
        return
    qualifiers = [item.state["qualified"] for item in groups]
    order = [group[rank] for rank in range(max(map(len, qualifiers))) for group in qualifiers if rank < len(group)]
    sources = {index: engine for index, engine in enumerate(order, 1)}
    sources.update({item.contest_key: item.state.get("winner_engine_id") for item in contests})
    stages = list_event_stages(connection, event.id)
    for stage in stages:
        matches = [item for item in contests if item.stage_id == stage.id]
        if any(item.status == "live" for item in matches):
            return
        pending = next((item for item in matches if item.status != "completed"), None)
        if pending is not None:
            engine_ids = [sources[key] for key in pending.state["sources"]]
            title = f"{event.title} · {pending.summary} {pending.title}" if pending.contest_key != "final" else f"{event.title} · Final"
            tournament_id = create_tournament(
                connection, title, _tournament_config(settings(event), engine_ids),
            )
            _link(connection, event.id, pending.id, tournament_id)
            update_event_contest_state(connection, pending.id, status="live", state={**pending.state, "engine_ids": engine_ids})
            set_event_stage_status(connection, stage.id, "active")
            _create_pair(connection, tournament_id, engine_ids, 1)
            set_tournament_status(connection, tournament_id, "running")
            return
        if stage.status != "completed":
            set_event_stage_status(connection, stage.id, "completed")
    winner = sources["final"]
    member = next(member for member in list_event_cast(connection, event.id) if member.engine_version_id == winner)
    create_event_award(connection, event.id, award_key="champion", title="Champion", position=0, recipient_cast_id=member.id, recipient_label=member.display_name, awarded_at=utc_now())
    update_event_state(connection, event.id, {**event.state, "winner_engine_id": winner})
    set_event_status(connection, event.id, "completed")


def _create_pair(connection, tournament_id: int, engine_ids: list[int], pair_index: int) -> None:
    for number, (white, black) in enumerate((engine_ids, engine_ids[::-1]), 1):
        create_game(
            connection, tournament_id=tournament_id, round=pair_index, pair_index=pair_index,
            white_engine_id=white, black_engine_id=black, game_number=number,
        )


def advance_competition(connection, tournament):
    from cope.runner.scheduler import TournamentAdvance, _advance_swiss

    if tournament.config.variant != "secret-queen":
        return None
    link = connection.execute("SELECT event_id, contest_id FROM secret_queen_event_tournaments WHERE tournament_id = ?", (tournament.id,)).fetchone()
    if link is None:
        return None
    event = get_event(connection, link["event_id"], lock=True)
    if event.status != "live":
        return TournamentAdvance()
    contest = next(item for item in list_event_contests(connection, event.id) if item.id == link["contest_id"])
    if contest.status == "completed":
        return TournamentAdvance(complete=True)
    games = list_games(connection, tournament.id)
    if not games or any(game.status != "finished" for game in games):
        return TournamentAdvance()
    if "qualifiers" in contest.state:
        advancement = _advance_swiss(connection, tournament)
        if not advancement.complete:
            return advancement
        table = _standings(connection, tournament, games)
        state = {**contest.state, "qualified": [row["engine_id"] for row in table[:contest.state["qualifiers"]]]}
        result = ""
    else:
        latest_pair = max(game.pair_index for game in games)
        pair = [game for game in games if game.pair_index == latest_pair]
        if len(pair) != 2 or any(game.result not in {"1-0", "0-1", "1/2-1/2"} for game in pair):
            return TournamentAdvance()
        first, second = contest.state["engine_ids"]
        points = defaultdict(int)
        for game in pair:
            if game.result == "1/2-1/2":
                points[first] += 1
                points[second] += 1
            else:
                points[game.white_engine_id if game.result == "1-0" else game.black_engine_id] += 2
        if points[first] == points[second]:
            _create_pair(connection, tournament.id, [first, second], latest_pair + 1)
            return TournamentAdvance(created_games=2)
        winner = first if points[first] > points[second] else second
        state = {**contest.state, "winner_engine_id": winner, "winning_pair": latest_pair}
        result = f"{points[first] / 2:g}–{points[second] / 2:g}"
    update_event_contest_state(connection, contest.id, status="completed", state=state, result=result)
    set_tournament_status(connection, tournament.id, "finished")
    _next_match(connection, event)
    return TournamentAdvance(complete=True)


def public_competition(connection, event) -> dict:
    links = {row["contest_id"]: row["tournament_id"] for row in connection.execute(
        "SELECT contest_id, tournament_id FROM secret_queen_event_tournaments WHERE event_id = ?", (event.id,),
    )}
    contests = []
    for item in list_event_contests(connection, event.id):
        tournament_id = links.get(item.id)
        games = () if tournament_id is None else list_games(connection, tournament_id)
        group = "qualifiers" in item.state
        tournament = None if tournament_id is None else get_tournament(connection, tournament_id)
        contests.append({
            "id": item.id, "key": item.contest_key, "title": item.title, "stage": item.summary,
            "kind": "group" if group else "bracket", "status": item.status, "result": item.result,
            "tournament_id": tournament_id, "engine_ids": item.state.get("engine_ids", []),
            "winner_engine_id": item.state.get("winner_engine_id"), "winning_pair": item.state.get("winning_pair"),
            "sources": item.state.get("sources", []),
            "qualifier_count": item.state.get("qualifiers", 0),
            "rounds": item.state.get("rounds", 0),
            "qualified": item.state.get("qualified", []),
            "standings": _standings(connection, tournament, games) if group and tournament else [],
            "games": [{"id": game.id, "round": game.round, "pair_index": game.pair_index,
                       "white_engine_id": game.white_engine_id, "black_engine_id": game.black_engine_id,
                       "status": game.status, "result": game.result} for game in games],
            "time_control": None if tournament is None else tournament.config.time_control.model_dump(mode="json"),
        })
    launch_ready = False
    if event.status in PRE_START and event.published_at:
        try:
            _validate_start(settings(event))
        except ValueError:
            pass
        else:
            launch_ready = True
    return {"contests": contests, "winner_engine_id": event.state.get("winner_engine_id"), "launch_ready": launch_ready}
