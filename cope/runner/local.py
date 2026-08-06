from __future__ import annotations

import logging
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import chess

from cope.chat import (
    announce_game_finished,
    announce_tournament_finished,
)
from cope.core.san import pv_to_san
from cope.core.models import (
    AdjudicationConfig,
    ColorSlot,
    EngineSpec,
    EngineRelayMember,
    EngineRelayTeam,
    GameAssignment,
    GameBenchmarkReference,
    TimeControl,
    WorkerGameAssignment,
    IncrementTimeControl,
    MoveNodesTimeControl,
    MoveTimeControl,
    MovesToGoTimeControl,
    TournamentConfig,
    WorkerResources,
    ENGINE_PROCESS_MEMORY_OVERHEAD_MB,
)
from cope.db import (
    GameAssignmentRecord,
    GameBenchmarkReferenceRecord,
    GameRecord,
    MoveRecord,
    OpeningPositionRecord,
    TournamentRecord,
    WorkerRecord,
    assign_game_to_worker,
    connect_database,
    finish_game,
    get_engine,
    get_game,
    get_game_assignment,
    get_common_benchmark_reference,
    get_opening_position,
    get_tournament,
    list_games,
    list_moves,
    list_tournaments,
    list_worker_tournament_ids,
    lock_tournament,
    mark_game_assignment_live,
    mark_game_live,
    record_move,
    set_tournament_current_round_at_least,
    set_tournament_status,
    touch_service_heartbeat,
)

from .scheduler import TournamentPreparation, advance_tournament, prepare_scheduled_tournaments
from .commands import process_pending_runner_commands
from .events import (
    publish_clock_sync,
    publish_engine_info,
    publish_game_move,
    publish_tournament_event,
    set_runner_wake_handler,
    start_event_publisher,
)
from cope.tournament.engine_instance import (
    EngineInstance,
    EngineCommandTransport,
    EngineSearchInfo,
    EngineSearchResult,
)
from cope.tournament.game_runner import GameRunner
from cope.version import app_version
from cope.tournament.game_state import GameState
from cope.tournament.time_control import RuntimeTimeControl, TimeControlCategory
from cope.pgn import PgnGame, render_annotated_pgn
from cope.tournament.tournament import Game


TERMINAL_GAME_STATUSES = {"finished", "abandoned"}
MAX_LEGAL_GAME_PLIES = 17_697
ENGINE_INFO_PUBLISH_INTERVAL_S = 0.5
DEFAULT_MAX_MOVES_DECISIVE_CP = 800
LOG = logging.getLogger("cope.runner")


@dataclass(frozen=True, slots=True)
class RunnerReport:
    prepared: tuple[TournamentPreparation, ...]
    tournaments_finished: int
    commands_applied: int = 0
    commands_failed: int = 0
    rating_commits_applied: int = 0
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunnerServiceConfig:
    db_path: str | Path
    poll_interval_s: float = 2.0


def run_tournament_service(config: RunnerServiceConfig) -> None:
    LOG.info("service started db=postgresql wake_mode=stream")
    wake = threading.Event()
    set_runner_wake_handler(lambda _event: wake.set())
    start_event_publisher()

    while True:
        wake.clear()
        connection: sqlite3.Connection | None = None
        try:
            connection = connect_database(config.db_path)
            touch_service_heartbeat(
                connection,
                "scheduler",
                app_version(),
            )
            report = run_tournament_matches(connection)
            print_runner_report(report)
        except Exception:
            LOG.exception("cycle failed")
        finally:
            if connection is not None:
                connection.close()

        wake.wait(timeout=max(config.poll_interval_s, 0.1))


def run_tournament_matches(
    connection: sqlite3.Connection,
) -> RunnerReport:
    prepared = prepare_scheduled_tournaments(connection)
    tournaments_finished = finish_completed_tournaments(connection)
    command_report = process_pending_runner_commands(connection)
    connection.commit()
    for result in prepared:
        if result.skipped_reason is None:
            publish_tournament_event(result.tournament_id)
    if tournaments_finished:
        for tournament in list_tournaments(connection):
            if tournament.status == "finished":
                publish_tournament_event(tournament.id)
    for result in command_report.rating_commits:
        publish_tournament_event(result.tournament_id)

    return RunnerReport(
        prepared=prepared,
        tournaments_finished=tournaments_finished,
        commands_applied=command_report.applied,
        commands_failed=command_report.failed,
        rating_commits_applied=len(command_report.rating_commits),
        errors=command_report.errors,
    )


def finish_completed_tournaments(connection: sqlite3.Connection) -> int:
    tournaments_finished = 0
    for tournament in list_tournaments(connection):
        if tournament.status != "running":
            continue

        if _finish_tournament_if_complete(connection, tournament):
            tournaments_finished += 1

    return tournaments_finished


def print_runner_report(report: RunnerReport) -> None:
    for result in report.prepared:
        if result.skipped_reason is None:
            LOG.info(
                "prepared tournament id=%s name=%s games=%s",
                result.tournament_id,
                result.tournament_name,
                result.created_games,
            )
        else:
            LOG.warning(
                "skipped tournament id=%s name=%s reason=%s",
                result.tournament_id,
                result.tournament_name,
                result.skipped_reason,
            )

    for error in report.errors:
        LOG.error("runner error: %s", error)

    if report.tournaments_finished:
        LOG.info("finished tournaments count=%s", report.tournaments_finished)
    if report.commands_applied:
        LOG.info(
            "applied runner commands count=%s rating_commits=%s",
            report.commands_applied,
            report.rating_commits_applied,
        )
    if report.commands_failed:
        LOG.warning("failed runner commands count=%s", report.commands_failed)


def next_worker_assignment(
    connection: sqlite3.Connection,
    worker: WorkerRecord,
    *,
    used_resources: tuple[int, int] | None = None,
    excluded_engine_ids: frozenset[int] = frozenset(),
    excluded_game_ids: frozenset[int] = frozenset(),
) -> WorkerGameAssignment | None:
    available_resources = _worker_available_resources(
        connection,
        worker,
        used_resources=used_resources,
    )
    if available_resources is None:
        return None
    allowed_tournament_ids = (
        None
        if worker.tournament_scope == "all"
        else frozenset(list_worker_tournament_ids(connection, worker.id))
    )
    for tournament in list_tournaments(connection):
        if tournament.status != "running":
            continue
        if (
            allowed_tournament_ids is not None
            and tournament.id not in allowed_tournament_ids
        ):
            continue

        active_games = _active_game_count(connection, tournament.id)
        if active_games >= tournament.config.concurrency:
            continue

        if worker.hw is None:
            continue
        game = _next_playable_game_for_worker(
            connection,
            tournament.id,
            worker,
            excluded_engine_ids=excluded_engine_ids,
            excluded_game_ids=excluded_game_ids,
        )
        if game is None:
            continue
        engines = _assignment_engines(connection, game)
        if excluded_engine_ids.intersection(engines):
            continue
        required_resources = _tournament_required_resources(
            tournament,
            engine_count=len(engines),
        )
        available_threads, available_hash_mb = available_resources
        if (
            available_threads < required_resources.threads
            or available_hash_mb < required_resources.hash_mb
        ):
            continue
        benchmark_reference = get_common_benchmark_reference(
            connection,
            tuple(engines.values()),
        )
        if benchmark_reference is None:
            continue

        set_tournament_current_round_at_least(connection, tournament.id, game.round)
        assignment_record = assign_game_to_worker(
            connection,
            game_id=game.id,
            assignment_key=secrets.token_urlsafe(24),
            worker_id=worker.id,
        )
        if assignment_record is None:
            continue
        opening = get_opening_position(connection, game.opening_id)
        LOG.info(
            "claimed game worker_id=%s assignment_id=%s game_id=%s tournament=%s round=%s",
            worker.id,
            assignment_record.id,
            game.id,
            tournament.name,
            game.round,
        )
        return _worker_assignment_payload(
            connection,
            tournament,
            game,
            assignment_record,
            opening,
            engines,
            benchmark_reference,
        )

    return None


def mark_worker_assignment_live(
    connection: sqlite3.Connection,
    assignment_id: int,
) -> None:
    assignment = get_game_assignment(connection, assignment_id)
    if assignment is None:
        raise RuntimeError(f"unknown assignment {assignment_id}")
    if assignment.status not in {"assigned", "acked", "live"}:
        raise RuntimeError(
            f"assignment {assignment_id} is no longer active ({assignment.status})"
        )
    mark_game_assignment_live(connection, assignment.id)
    mark_game_live(connection, assignment.game_id)


def run_worker_assignment_game(
    connection: sqlite3.Connection,
    assignment: WorkerGameAssignment,
    transport: EngineCommandTransport,
    *,
    progress_handler: Callable[
        [str, str, str, str, int | None, int | None, dict | None],
        None,
    ] | None = None,
) -> None:
    def progress(
        stage: str,
        substage: str,
        status: str,
        detail: str,
        current: int | None = None,
        total: int | None = None,
        metadata: dict | None = None,
    ) -> None:
        if progress_handler is not None:
            progress_handler(
                stage,
                substage,
                status,
                detail,
                current,
                total,
                metadata,
            )

    _validated_assignment_record(connection, assignment)
    game_record = _validated_game(connection, assignment.assignment.game_id)
    tournament = _validated_tournament(connection, game_record.tournament_id)
    opening = get_opening_position(connection, game_record.opening_id)
    board = _starting_board(opening)
    recorded_moves = list_moves(connection, game_record.id)
    opening_moves = () if opening is None else opening.moves
    if assignment.initial_fen != board.fen():
        raise RuntimeError("assignment opening start position does not match the game")
    if assignment.opening_moves != opening_moves:
        raise RuntimeError("assignment opening moves do not match the game")
    _validate_new_game_attempt(recorded_moves)
    connection.commit()
    LOG.info(
        "starting game assignment_id=%s game_id=%s tournament=%s round=%s opening=%s",
        assignment.assignment.assignment_id,
        game_record.id,
        tournament.name,
        game_record.round,
        None if opening is None else opening.name,
    )
    progress(
        "startup",
        "runtime_create",
        "running",
        "Creating the synchronized engine runtime",
    )

    runtime_time_control = _runtime_time_control(tournament.config.time_control)
    engine_instances = {
        engine_id: EngineInstance(
            engine_id,
            transport,
            options=_engine_options(assignment, engine_id),
        )
        for engine_id in assignment.engines
    }
    relay = assignment.assignment.engine_relay
    white_member = (
        _relay_member_for_moves(relay.teams[ColorSlot.WHITE], 0)
        if relay is not None
        else None
    )
    black_member = (
        _relay_member_for_moves(relay.teams[ColorSlot.BLACK], 0)
        if relay is not None
        else None
    )
    white = engine_instances[
        white_member.engine_id if white_member is not None else game_record.white_engine_id
    ]
    black = engine_instances[
        black_member.engine_id if black_member is not None else game_record.black_engine_id
    ]
    game = Game(
        id=game_record.id,
        white=white,
        black=black,
        state=GameState(board=board),
        white_tm=runtime_time_control.create_manager(),
        black_tm=runtime_time_control.create_manager(),
    )
    relay_engine_data = _relay_engine_data(relay)
    live_reporter = _LiveGameReporter(
        tournament.id,
        game_record.id,
        game,
        white,
        black,
        relay_engine_data=relay_engine_data,
    )
    if relay is None:
        white.set_info_listener(live_reporter.publish_white_engine_info)
        black.set_info_listener(live_reporter.publish_black_engine_info)
    else:
        for color, team in relay.teams.items():
            side = color.name.lower()
            for member in team.members:
                engine = engine_instances[member.engine_id]
                engine.set_info_listener(
                    lambda line, info, side=side, engine=engine: live_reporter.publish_engine_info(
                        side,
                        engine,
                        line,
                        info,
                    )
                )
    runner = GameRunner(
        game,
        on_clock_sync=live_reporter.publish_clock_sync,
        lag_compensation_ms=tournament.config.lag_compensation_ms,
    )
    progress(
        "startup",
        "runtime_create",
        "completed",
        "Created both engine runtime controllers",
    )
    progress(
        "startup",
        "engine_initialize",
        "running",
        "Starting both engines and synchronizing their UCI configuration",
        current=0,
        total=len(engine_instances),
    )
    runner.prepare_game(engine_instances.values())
    progress(
        "startup",
        "engine_initialize",
        "completed",
        "Every assigned engine passed UCI initialization and readiness checks",
        current=len(engine_instances),
        total=len(engine_instances),
    )
    opening_label = "Start position" if opening is None else opening.name or "Configured opening"
    progress(
        "opening",
        "opening_select",
        "running",
        f"Selecting {opening_label}",
        current=0,
        total=max(len(opening_moves), 1),
        metadata={
            "start_fen": assignment.initial_fen,
            "opening": opening_label,
            "book_plies": len(opening_moves),
        },
    )
    for engine in engine_instances.values():
        engine.prepare_position(board)
    progress(
        "opening",
        "opening_start_position",
        "completed",
        f"Both engines initialized at the start position for {opening_label}",
        current=0,
        total=max(len(opening_moves), 1),
        metadata={"fen": board.fen()},
    )
    moves = list(recorded_moves)
    for book_index, uci in enumerate(opening_moves, start=1):
        progress(
            "opening",
            "opening_move",
            "running",
            f"Playing opening book move {book_index}/{len(opening_moves)}: {uci}",
            current=book_index - 1,
            total=len(opening_moves),
            metadata={"move": uci, "book_ply": book_index},
        )
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise RuntimeError(
                f"opening move {uci} is illegal at book ply {book_index}"
            )
        board_before_move = board.copy(stack=False)
        side_to_move = board.turn
        board.push(move)
        game.state.update_from_board()
        for engine in engine_instances.values():
            engine.prepare_position(board)
        clock = game.white_tm if side_to_move == chess.WHITE else game.black_tm
        clock_after_ms = _clock_time_ms(clock)
        move_record = record_move(
            connection,
            game_id=game_record.id,
            assignment_id=assignment.assignment.assignment_id,
            assignment_key=assignment.assignment.assignment_key,
            ply=board.ply(),
            uci=uci,
            san=board_before_move.san(move),
            is_book=True,
            time_ms=0,
            clock_after_ms=clock_after_ms if clock_after_ms is not None else 0,
        )
        moves.append(move_record)
        connection.commit()
        publish_game_move(
            tournament.id,
            game_record.id,
            board.ply(),
            move=asdict(move_record),
            clocks_ms=_live_clock_payload(
                game,
                "white" if side_to_move == chess.WHITE else "black",
                clock_after_ms,
            ),
        )
        progress(
            "opening",
            "opening_move",
            "completed",
            f"Applied opening book move {book_index}/{len(opening_moves)}: {uci}",
            current=book_index,
            total=len(opening_moves),
            metadata={
                "move": uci,
                "book_ply": book_index,
                "game_ply": board.ply(),
                "fen": board.fen(),
                "clocks_started": False,
            },
        )
    progress(
        "opening",
        "opening_moves",
        "completed",
        f"Completed {len(opening_moves)} book plies for {opening_label}; clocks remain stopped",
        current=len(opening_moves),
        total=max(len(opening_moves), 1),
        metadata={
            "fen": board.fen(),
            "book_plies": len(opening_moves),
            "clocks_started": False,
        },
    )

    mark_worker_assignment_live(connection, assignment.assignment.assignment_id)
    _validated_assignment_record(connection, assignment)
    connection.commit()
    publish_tournament_event(tournament.id)
    progress(
        "play",
        "game_live",
        "running",
        "Engines are synchronized and move play is starting",
        current=board.ply(),
        total=max(assignment.max_plies, board.ply(), 1),
    )

    adjudicated = (
        None
        if game.state.is_finished()
        else _adjudication_result(tournament.config.adjudication, moves)
    )
    while (
        not game.state.is_finished()
        and adjudicated is None
        and board.ply() < assignment.max_plies
    ):
        side_to_move = board.turn
        side_label = "White" if side_to_move == chess.WHITE else "Black"
        if relay is not None:
            color = ColorSlot.WHITE if side_to_move == chess.WHITE else ColorSlot.BLACK
            team = relay.teams[color]
            member = _relay_member_for_moves(
                team,
                _relay_moves_played(moves, color),
            )
            engine = engine_instances[member.engine_id]
            manager = RuntimeTimeControl(
                TimeControlCategory.MOVENODES,
                nodes=member.nodes,
            ).create_manager()
            if side_to_move == chess.WHITE:
                game.white = engine
                game.white_tm = manager
            else:
                game.black = engine
                game.black_tm = manager
        else:
            engine = white if side_to_move == chess.WHITE else black
        board_before_move = board.copy(stack=False)
        move = runner.run_next_move()
        if move is None:
            break
        search = engine.get_last_search_result()
        if search is not None and search.info_line is not None:
            live_reporter.publish_final_engine_info(
                side_label.lower(),
                engine,
                search,
                board_before_move.fen(),
            )
        clock = game.white_tm if side_to_move == chess.WHITE else game.black_tm
        clock_after_ms = _clock_time_ms(clock)
        move_record = record_move(
            connection,
            game_id=game_record.id,
            assignment_id=assignment.assignment.assignment_id,
            assignment_key=assignment.assignment.assignment_key,
            ply=board.ply(),
            uci=move.uci(),
            san=board_before_move.san(move),
            eval_cp=None if search is None else search.eval_cp,
            eval_mate=None if search is None else search.eval_mate,
            score_bound=None if search is None else search.score_bound,
            depth=None if search is None else search.depth,
            seldepth=None if search is None else search.seldepth,
            nodes=None if search is None else search.nodes,
            nps=None if search is None else search.nps,
            hashfull=None if search is None else search.hashfull,
            pv=None if search is None else search.pv,
            info_line=None if search is None else search.info_line,
            time_ms=0 if search is None else search.time_ms,
            clock_after_ms=clock_after_ms if clock_after_ms is not None else 0,
            engine_version_id=int(engine.get_name()),
        )
        moves.append(move_record)
        connection.commit()
        if not game.state.is_finished():
            adjudicated = _adjudication_result(tournament.config.adjudication, moves)
        publish_game_move(
            tournament.id,
            game_record.id,
            board.ply(),
            move=asdict(move_record),
            clocks_ms=_live_clock_payload(
                game,
                side_label.lower(),
                clock_after_ms,
            ),
        )
        if board.ply() <= 10 or board.ply() % 10 == 0:
            LOG.debug(
                "recorded move game_id=%s ply=%s move=%s",
                game_record.id,
                board.ply(),
                move.uci(),
            )

    _validated_assignment_record(connection, assignment)
    progress(
        "play",
        "game_complete",
        "completed",
        f"Move play stopped after {board.ply()} plies",
        current=board.ply(),
        total=max(assignment.max_plies, board.ply(), 1),
    )
    progress(
        "conclude",
        "result",
        "running",
        "Determining the final game result and termination",
        current=board.ply(),
        total=max(assignment.max_plies, board.ply(), 1),
    )
    if adjudicated is not None:
        result, termination = adjudicated
    elif not game.state.is_finished():
        result, termination = _max_moves_result(tournament, moves)
    else:
        result = game.state.get_result()
        termination = game.state.get_details() or "unknown"

    progress(
        "conclude",
        "pgn",
        "running",
        f"Building PGN for {len(moves)} recorded plies",
        current=len(moves),
        total=max(len(moves), 1),
    )
    pgn = _build_pgn(connection, tournament, game_record, opening, moves, result, termination)
    progress(
        "conclude",
        "persist",
        "running",
        f"Persisting result {result} ({termination})",
    )
    finish_game(
        connection,
        game_record.id,
        result=result,
        termination=termination,
        pgn=pgn,
    )
    announce_game_finished(
        connection,
        tournament,
        game_record,
        result=result,
        termination=termination,
    )
    _finish_tournament_if_complete(connection, tournament)
    connection.commit()
    progress(
        "conclude",
        "persist",
        "completed",
        f"Game concluded with result {result} after {len(moves)} plies",
        current=len(moves),
        total=max(len(moves), 1),
        metadata={"result": result, "termination": termination, "plies": len(moves)},
    )
    LOG.info(
        "finished game assignment_id=%s game_id=%s result=%s termination=%s plies=%s",
        assignment.assignment.assignment_id,
        game_record.id,
        result,
        termination,
        len(moves),
    )
    publish_tournament_event(tournament.id)


def _relay_member_for_moves(
    team: EngineRelayTeam,
    moves_played: int,
) -> EngineRelayMember:
    cycle = sum(member.relay_moves for member in team.members)
    offset = moves_played % cycle
    for member in team.members:
        if offset < member.relay_moves:
            return member
        offset -= member.relay_moves
    return team.members[0]


def _relay_moves_played(
    moves: Sequence[MoveRecord],
    color: ColorSlot,
) -> int:
    white = color == ColorSlot.WHITE
    return sum(
        1
        for move in moves
        if not move.is_book and ((move.ply % 2 == 1) == white)
    )


def _relay_engine_data(relay) -> dict[int, dict[str, Any]]:
    if relay is None:
        return {}
    return {
        member.engine_id: {
            "relay_team_id": team.team_id,
            "relay_team_name": team.name,
            "relay_position": index,
            "relay_moves": member.relay_moves,
            "node_limit": member.nodes,
        }
        for team in relay.teams.values()
        for index, member in enumerate(team.members)
    }


class _LiveGameReporter:
    def __init__(
        self,
        tournament_id: int,
        game_id: int,
        game: Game,
        white: EngineInstance,
        black: EngineInstance,
        relay_engine_data: dict[int, dict[str, Any]] | None = None,
    ):
        self._tournament_id = tournament_id
        self._game_id = game_id
        self._game = game
        self._white = white
        self._black = black
        self._relay_engine_data = relay_engine_data or {}
        self._last_engine_info_at = {"white": 0.0, "black": 0.0}

    def publish_white_engine_info(self, line: str, info: EngineSearchInfo) -> None:
        self._publish_engine_info("white", self._white, line, info)

    def publish_black_engine_info(self, line: str, info: EngineSearchInfo) -> None:
        self._publish_engine_info("black", self._black, line, info)

    def publish_engine_info(
        self,
        side: str,
        engine: EngineInstance,
        line: str,
        info: EngineSearchInfo,
    ) -> None:
        self._publish_engine_info(side, engine, line, info)

    def publish_final_engine_info(
        self,
        side: str,
        engine: EngineInstance,
        result: EngineSearchResult,
        root_fen: str,
    ) -> None:
        self._publish_engine_info(
            side,
            engine,
            result.info_line or "",
            EngineSearchInfo(
                eval_cp=result.eval_cp,
                eval_mate=result.eval_mate,
                score_bound=result.score_bound,
                depth=result.depth,
                seldepth=result.seldepth,
                nodes=result.nodes,
                nps=result.nps,
                hashfull=result.hashfull,
                time_ms=result.time_ms,
                pv=result.pv,
            ),
            force=True,
            root_fen=root_fen,
        )

    def publish_clock_sync(
        self,
        side_to_move: chess.Color,
        running: bool,
        active_remaining_ms: int | None,
    ) -> None:
        side = "white" if side_to_move == chess.WHITE else "black"
        publish_clock_sync(
            self._tournament_id,
            {
                "tournament_id": self._tournament_id,
                "game_id": self._game_id,
                "active_side": side,
                "running": running,
                "clocks_ms": _live_clock_payload(self._game, side, active_remaining_ms),
            },
        )

    def _publish_engine_info(
        self,
        side: str,
        engine: EngineInstance,
        line: str,
        info: EngineSearchInfo,
        *,
        force: bool = False,
        root_fen: str | None = None,
    ) -> None:
        now = time.monotonic()
        if not force and now - self._last_engine_info_at[side] < ENGINE_INFO_PUBLISH_INTERVAL_S:
            return
        self._last_engine_info_at[side] = now
        engine_data = _live_engine_data(info)
        engine_id = int(engine.get_name())
        engine_data["engine_id"] = engine_id
        engine_data.update(self._relay_engine_data.get(engine_id, {}))
        root_fen = root_fen or self._game.state.get_board().fen()
        engine_data["root_fen"] = root_fen
        engine_data["pv_san"] = pv_to_san(info.pv, root_fen) or "not recorded"
        engine_data["info"] = line
        publish_engine_info(
            self._tournament_id,
            {
                "tournament_id": self._tournament_id,
                "game_id": self._game_id,
                "engine_id": engine_id,
                "side": side,
                "raw": line,
                "root_fen": root_fen,
                "engine_data": engine_data,
            },
        )


def _live_clock_payload(
    game: Game,
    active_side: str,
    active_remaining_ms: int | None,
) -> dict[str, int | None]:
    white_ms = _clock_time_ms(game.white_tm)
    black_ms = _clock_time_ms(game.black_tm)
    if active_side == "white":
        white_ms = active_remaining_ms if white_ms is not None else None
    else:
        black_ms = active_remaining_ms if black_ms is not None else None
    return {
        "white": white_ms,
        "black": black_ms,
    }


def _clock_time_ms(clock) -> int | None:
    remaining_time = clock.get_remaining_time()
    if remaining_time is not None:
        return remaining_time
    return clock.get_remaining_move_time()


def _live_engine_data(info: EngineSearchInfo | None) -> dict[str, Any]:
    if info is None:
        return {
            "depth": "-",
            "seldepth": "-",
            "nps": "-",
            "nodes": "-",
            "hashfull": "-",
            "eval": "-",
            "pv": "not recorded",
        }

    nps = info.nps
    if nps is None and info.nodes is not None and info.time_ms > 0:
        nps = int(info.nodes / (info.time_ms / 1000))

    return {
        "depth": str(info.depth) if info.depth is not None else "-",
        "seldepth": str(info.seldepth) if info.seldepth is not None else "-",
        "nps": f"{nps:,}" if nps is not None else "-",
        "nodes": f"{info.nodes:,}" if info.nodes is not None else "-",
        "hashfull": str(info.hashfull) if info.hashfull is not None else "-",
        "eval": _live_eval_label(info),
        "pv": info.pv or "not recorded",
    }


def _live_eval_label(info: EngineSearchInfo) -> str:
    prefix = {"lowerbound": "≥", "upperbound": "≤"}.get(info.score_bound, "")
    if info.eval_mate is not None:
        return f"{prefix}#{info.eval_mate}"
    if info.eval_cp is not None:
        return f"{prefix}{info.eval_cp / 100:+.2f}"
    return "-"


def _worker_assignment_payload(
    connection: sqlite3.Connection,
    tournament: TournamentRecord,
    game: GameRecord,
    assignment: GameAssignmentRecord,
    opening: OpeningPositionRecord | None,
    engines: dict[int, EngineSpec],
    benchmark_reference: GameBenchmarkReferenceRecord,
) -> WorkerGameAssignment:
    from cope.events.engine_relay import relay_assignment_for_game

    relay = relay_assignment_for_game(connection, game)
    return WorkerGameAssignment(
        assignment=GameAssignment(
            assignment_id=assignment.id,
            assignment_key=assignment.assignment_key,
            game_id=game.id,
            slots={
                ColorSlot.WHITE: game.white_engine_id,
                ColorSlot.BLACK: game.black_engine_id,
            },
            time_control=tournament.config.time_control,
            uci_options_overrides={
                engine_id: _tournament_engine_options(tournament, engine_id)
                for engine_id in engines
            },
            engine_relay=relay,
        ),
        tournament_name=tournament.name,
        round=game.round,
        initial_fen=_starting_board(opening).fen(),
        opening_name=None if opening is None else opening.name,
        opening_moves=() if opening is None else opening.moves,
        max_plies=_max_plies(tournament),
        engines=engines,
        required_resources=_tournament_required_resources(
            tournament,
            engine_count=len(engines),
        ),
        benchmark_reference=GameBenchmarkReference(
            hardware_key=benchmark_reference.hardware_key,
            engine_nps=benchmark_reference.engine_nps,
        ),
    )


def _assignment_engines(
    connection: sqlite3.Connection,
    game: GameRecord,
) -> dict[int, EngineSpec]:
    from cope.events.engine_relay import relay_engine_ids_for_game

    engines: dict[int, EngineSpec] = {}
    engine_ids = {
        game.white_engine_id,
        game.black_engine_id,
        *relay_engine_ids_for_game(connection, game),
    }
    for engine_id in engine_ids:
        engine = get_engine(connection, engine_id)
        if engine is None:
            raise RuntimeError(f"unknown engine {engine_id}")
        engines[engine_id] = engine
    return engines


def _engine_options(
    assignment: WorkerGameAssignment,
    engine_id: int,
) -> dict[str, str | int | bool]:
    spec = assignment.engines.get(engine_id)
    if spec is None:
        raise RuntimeError(f"assignment missing engine {engine_id}")

    return _merge_uci_options(
        spec.uci_options,
        assignment.assignment.uci_options_overrides.get(engine_id, {}),
    )


def _tournament_required_resources(
    tournament: TournamentRecord,
    *,
    engine_count: int = 2,
) -> WorkerResources:
    return WorkerResources(
        threads=tournament.config.engine_threads,
        hash_mb=(
            tournament.config.engine_hash_mb + ENGINE_PROCESS_MEMORY_OVERHEAD_MB
        )
        * max(2, engine_count),
    )


def _worker_available_resources(
    connection: sqlite3.Connection,
    worker: WorkerRecord,
    *,
    used_resources: tuple[int, int] | None = None,
) -> tuple[int, int] | None:
    capacity = worker.capacity
    if capacity is None:
        return None
    if used_resources is None:
        used_threads = 0
        used_hash_mb = 0
        rows = connection.execute(
            """
            SELECT tournaments.id, tournaments.config
            FROM game_assignments
            JOIN games ON games.id = game_assignments.game_id
            JOIN tournaments ON tournaments.id = games.tournament_id
            WHERE game_assignments.worker_id = ?
              AND game_assignments.status IN ('assigned', 'acked', 'live')
              AND games.status IN ('assigned', 'live')
            """,
            (worker.id,),
        )
        for row in rows:
            config = TournamentConfig.model_validate_json(row["config"])
            used_threads += config.engine_threads
            from cope.events.engine_relay import relay_engine_count_for_tournament

            engine_count = relay_engine_count_for_tournament(
                connection,
                int(row["id"]),
            )
            used_hash_mb += (
                config.engine_hash_mb + ENGINE_PROCESS_MEMORY_OVERHEAD_MB
            ) * max(2, engine_count)
    else:
        used_threads, used_hash_mb = used_resources
    return (
        max(0, capacity.threads - used_threads),
        max(0, capacity.hash_mb - used_hash_mb),
    )


def _tournament_engine_options(
    tournament: TournamentRecord,
    engine_id: int,
) -> dict[str, str | int | bool]:
    del engine_id
    options = dict(tournament.config.uci_options)
    options["Threads"] = tournament.config.engine_threads
    options["Hash"] = tournament.config.engine_hash_mb
    return options


def _merge_uci_options(
    base: dict[str, str | int | bool],
    overrides: dict[str, str | int | bool],
) -> dict[str, str | int | bool]:
    overridden_names = {name.strip().lower() for name in overrides}
    merged = {
        name: value
        for name, value in base.items()
        if name.strip().lower() not in overridden_names
    }
    merged.update(overrides)
    return merged


def _runtime_time_control(time_control: TimeControl) -> RuntimeTimeControl:
    if isinstance(time_control, IncrementTimeControl):
        return RuntimeTimeControl(
            TimeControlCategory.INCREMENT,
            initial_time=time_control.initial_ms,
            increment=time_control.increment_ms,
        )
    if isinstance(time_control, MoveTimeControl):
        return RuntimeTimeControl(
            TimeControlCategory.MOVETIME,
            move_time=time_control.move_time_ms,
        )
    if isinstance(time_control, MovesToGoTimeControl):
        return RuntimeTimeControl(
            TimeControlCategory.MOVESTOGO,
            initial_time=time_control.initial_ms,
            moves_to_go=time_control.moves_to_go,
        )
    if isinstance(time_control, MoveNodesTimeControl):
        return RuntimeTimeControl(
            TimeControlCategory.MOVENODES,
            nodes=time_control.nodes,
        )
    raise RuntimeError(f"unsupported time control: {time_control}")


def _validated_game(connection: sqlite3.Connection, game_id: int) -> GameRecord:
    game = get_game(connection, game_id)
    if game is None:
        raise RuntimeError(f"unknown game {game_id}")
    if game.status in TERMINAL_GAME_STATUSES:
        raise RuntimeError(f"game {game_id} is already {game.status}")
    return game


def _validated_assignment_record(
    connection: sqlite3.Connection,
    assignment: WorkerGameAssignment,
) -> GameAssignmentRecord:
    payload = assignment.assignment
    assignment_record = get_game_assignment(connection, payload.assignment_id)
    if assignment_record is None:
        raise RuntimeError(f"unknown assignment {payload.assignment_id}")
    if assignment_record.assignment_key != payload.assignment_key:
        raise RuntimeError(f"stale assignment {payload.assignment_id}")
    if assignment_record.game_id != payload.game_id:
        raise RuntimeError(f"assignment {payload.assignment_id} game mismatch")
    if assignment_record.status not in {"assigned", "acked", "live"}:
        raise RuntimeError(
            f"assignment {payload.assignment_id} is no longer active "
            f"({assignment_record.status})"
        )
    return assignment_record


def _validated_tournament(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> TournamentRecord:
    tournament = get_tournament(connection, tournament_id)
    if tournament is None:
        raise RuntimeError(f"unknown tournament {tournament_id}")
    return tournament


def _next_playable_game_for_worker(
    connection: sqlite3.Connection,
    tournament_id: int,
    worker: WorkerRecord,
    *,
    excluded_engine_ids: frozenset[int] = frozenset(),
    excluded_game_ids: frozenset[int] = frozenset(),
) -> GameRecord | None:
    del worker
    conditions = "tournament_id = ? AND status = 'pending'"
    parameters: list[int] = [tournament_id]
    if excluded_engine_ids:
        blocked = tuple(sorted(excluded_engine_ids))
        placeholders = ", ".join("?" for _ in blocked)
        conditions += (
            f" AND white_engine_id NOT IN ({placeholders})"
            f" AND black_engine_id NOT IN ({placeholders})"
        )
        parameters.extend(blocked)
        parameters.extend(blocked)
    if excluded_game_ids:
        blocked_games = tuple(sorted(excluded_game_ids))
        placeholders = ", ".join("?" for _ in blocked_games)
        conditions += f" AND id NOT IN ({placeholders})"
        parameters.extend(blocked_games)
    row = connection.execute(
        f"""
        SELECT id
        FROM games
        WHERE {conditions}
        ORDER BY ((game_number - 1) / 2), round, pair_index, game_number, id
        LIMIT 1
        """,
        parameters,
    ).fetchone()
    return None if row is None else get_game(connection, int(row["id"]))


def _active_game_count(connection: sqlite3.Connection, tournament_id: int) -> int:
    row = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM games
        WHERE tournament_id = ? AND status IN ('assigned', 'live')
        """,
        (tournament_id,),
    ).fetchone()
    return 0 if row is None else int(row["count"])


def _paired_game_queue(games: tuple[GameRecord, ...]) -> tuple[GameRecord, ...]:
    groups: dict[
        tuple[int, int, int | None, int | None, int, str | None],
        list[GameRecord],
    ] = {}
    for game in games:
        engine1_id, engine2_id = sorted(
            (game.white_engine_id, game.black_engine_id)
        )
        key = (
            engine1_id,
            engine2_id,
            game.opening_id,
            game.match_id,
            (game.game_number - 1) // 2,
            game.tiebreak_kind,
        )
        groups.setdefault(key, []).append(game)
    paired_groups = sorted(
        groups.values(),
        key=lambda paired_games: min(
            (
                (game.game_number - 1) // 2,
                game.round,
                game.pair_index,
                game.id,
            )
            for game in paired_games
        ),
    )
    return tuple(
        game
        for paired_games in paired_groups
        for game in sorted(paired_games, key=lambda item: (item.game_number, item.id))
    )


def _finish_tournament_if_complete(
    connection: sqlite3.Connection,
    tournament: TournamentRecord,
) -> bool:
    if tournament.config.format in {"round_robin", "gauntlet"}:
        counts = connection.execute(
            """
            SELECT
              COUNT(*) AS total,
              COUNT(*) FILTER (
                WHERE status NOT IN ('finished', 'abandoned')
              ) AS unfinished
            FROM games
            WHERE tournament_id = ?
            """,
            (tournament.id,),
        ).fetchone()
        if (
            counts is None
            or int(counts["total"]) == 0
            or int(counts["unfinished"]) > 0
        ):
            return False

    current = lock_tournament(connection, tournament.id)
    if current is None or current.status != "running":
        return False

    games = list_games(connection, current.id)
    if not games:
        return False
    all_terminal = all(game.status in TERMINAL_GAME_STATUSES for game in games)
    if all_terminal and any(game.status == "abandoned" for game in games):
        set_tournament_status(connection, current.id, "aborted")
        return False

    if current.config.format in {"round_robin", "gauntlet"}:
        if not all_terminal:
            return False
        set_tournament_status(connection, current.id, "finished")
        finished = get_tournament(connection, current.id) or current
        announce_tournament_finished(connection, finished)
        return True

    advance = advance_tournament(connection, current)
    games = list_games(connection, current.id)
    if not advance.complete or any(game.status not in TERMINAL_GAME_STATUSES for game in games):
        return False

    set_tournament_status(connection, current.id, "finished")
    finished = get_tournament(connection, current.id) or current
    announce_tournament_finished(connection, finished)
    return True


def _starting_board(opening: OpeningPositionRecord | None) -> chess.Board:
    if opening is None or opening.start_fen == "startpos":
        return chess.Board()
    return chess.Board(opening.start_fen)


def _validate_new_game_attempt(recorded_moves: tuple[MoveRecord, ...]) -> None:
    if recorded_moves:
        raise RuntimeError(
            "game assignment contains moves from an earlier attempt; "
            "interrupted games must be reset before reassignment"
        )


def _max_plies(tournament: TournamentRecord) -> int:
    max_moves = tournament.config.adjudication.max_moves
    if max_moves is not None:
        return max_moves * 2
    return MAX_LEGAL_GAME_PLIES


def _adjudication_result(
    config: AdjudicationConfig,
    moves: Sequence[MoveRecord],
) -> tuple[str, str] | None:
    draw = config.draw
    if draw is not None:
        window = _adjudication_window(moves, draw.consecutive_plies)
        if window and all(
            (move.ply + 1) // 2 >= draw.min_fullmove
            and move.eval_mate is None
            and move.eval_cp is not None
            and move.score_bound is None
            and abs(move.eval_cp) <= draw.max_abs_cp
            for move in window
        ):
            return (
                "1/2-1/2",
                "draw adjudication: both engines agreed within "
                f"+/-{draw.max_abs_cp}cp for {draw.consecutive_plies} consecutive plies",
            )

    resign = config.resign
    if resign is None:
        return None
    window = _adjudication_window(moves, resign.consecutive_plies)
    if not window:
        return None

    winners: list[str] = []
    for move in window:
        score = _white_relative_move_score(move)
        if score is None:
            return None
        mate, cp, bound = score
        if mate is not None:
            if mate == 0 or bound is not None:
                return None
            winners.append("white" if mate > 0 else "black")
        elif cp is not None:
            winner = _decisive_score_winner(cp, bound, resign.min_abs_cp)
            if winner is None:
                return None
            winners.append(winner)
        else:
            return None

    winner = winners[0]
    if any(candidate != winner for candidate in winners[1:]):
        return None
    return (
        "1-0" if winner == "white" else "0-1",
        f"win adjudication: both engines agreed {winner} was winning for "
        f"{resign.consecutive_plies} consecutive plies",
    )


def _adjudication_window(
    moves: Sequence[MoveRecord],
    consecutive_plies: int,
) -> Sequence[MoveRecord]:
    if len(moves) < consecutive_plies:
        return ()
    window = moves[-consecutive_plies:]
    if any(current.ply != previous.ply + 1 for previous, current in zip(window, window[1:])):
        return ()
    if len({move.ply % 2 for move in window}) != 2:
        return ()
    return window


def _white_relative_move_score(
    move: MoveRecord,
) -> tuple[int | None, int | None, str | None] | None:
    mover_sign = 1 if move.ply % 2 == 1 else -1
    bound = move.score_bound
    if mover_sign < 0:
        bound = {"lowerbound": "upperbound", "upperbound": "lowerbound"}.get(bound)
    if move.eval_mate is not None:
        return mover_sign * move.eval_mate, None, bound
    if move.eval_cp is not None:
        return None, mover_sign * move.eval_cp, bound
    return None


def _decisive_score_winner(cp: int, bound: str | None, threshold: int) -> str | None:
    if cp >= threshold and bound != "upperbound":
        return "white"
    if cp <= -threshold and bound != "lowerbound":
        return "black"
    return None


def _max_moves_result(
    tournament: TournamentRecord,
    moves: Sequence[MoveRecord],
) -> tuple[str, str]:
    score = _latest_white_relative_score(moves)
    if score is None:
        return "1/2-1/2", "max moves"

    mate, cp, bound = score
    if mate is not None:
        if bound is not None:
            return "1/2-1/2", "max moves"
        if mate > 0:
            return "1-0", "max moves: white has forced mate"
        if mate < 0:
            return "0-1", "max moves: black has forced mate"
        return "1/2-1/2", "max moves"

    if cp is None:
        return "1/2-1/2", "max moves"

    threshold = _max_moves_decisive_cp(tournament)
    winner = _decisive_score_winner(cp, bound, threshold)
    if winner == "white":
        return "1-0", f"max moves: white winning by evaluation ({cp / 100:+.2f})"
    if winner == "black":
        return "0-1", f"max moves: black winning by evaluation ({cp / 100:+.2f})"
    if bound is not None:
        return "1/2-1/2", "max moves"
    return "1/2-1/2", f"max moves: evaluation within decisive threshold ({cp / 100:+.2f})"


def _latest_white_relative_score(
    moves: Sequence[MoveRecord],
) -> tuple[int | None, int | None, str | None] | None:
    for move in reversed(moves):
        score = _white_relative_move_score(move)
        if score is not None:
            return score
    return None


def _max_moves_decisive_cp(tournament: TournamentRecord) -> int:
    resign_rule = tournament.config.adjudication.resign
    if resign_rule is not None:
        return resign_rule.min_abs_cp
    return DEFAULT_MAX_MOVES_DECISIVE_CP


def _build_pgn(
    connection: sqlite3.Connection,
    tournament: TournamentRecord,
    game: GameRecord,
    opening: OpeningPositionRecord | None,
    moves: Sequence[MoveRecord],
    result: str,
    termination: str,
) -> str:
    white = get_engine(connection, game.white_engine_id)
    black = get_engine(connection, game.black_engine_id)
    extra_headers: dict[str, Any] = {}
    white_name = (
        " ".join((white.name, white.version))
        if white is not None
        else f"Engine {game.white_engine_id}"
    )
    black_name = (
        " ".join((black.name, black.version))
        if black is not None
        else f"Engine {game.black_engine_id}"
    )
    from cope.events.engine_relay import relay_assignment_for_game

    relay = relay_assignment_for_game(connection, game)
    if relay is not None:
        white_team = relay.teams[ColorSlot.WHITE]
        black_team = relay.teams[ColorSlot.BLACK]
        white_name = white_team.name
        black_name = black_team.name
        extra_headers = {
            "EventType": "Engine Relay",
            "WhiteRelay": ", ".join(str(member.engine_id) for member in white_team.members),
            "BlackRelay": ", ".join(str(member.engine_id) for member in black_team.members),
        }
    return render_annotated_pgn(
        PgnGame(
            id=game.id,
            tournament_id=tournament.id,
            event=tournament.name,
            round=game.round,
            game_number=game.game_number,
            white=white_name,
            black=black_name,
            result=result,
            termination=termination,
            opening=opening.name if opening is not None else None,
            start_fen=opening.start_fen if opening is not None else None,
            started_at=game.started_at,
            time_control=tournament.config.time_control.model_dump(mode="json"),
            moves=moves,
            extra_headers=extra_headers,
        )
    )
