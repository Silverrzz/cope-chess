from __future__ import annotations

import heapq
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from cope.core.models import (
    GauntletFormatOptions,
    IncrementTimeControl,
    KnockoutFormatOptions,
    RoundRobinFormatOptions,
    SwissFormatOptions,
    TournamentConfig,
)
from cope.db import GameRecord, TournamentRecord, list_games


MAX_GAME_DURATION_SECONDS = 7 * 24 * 60 * 60
MIN_LATE_GAME_PLIES = 6.0
RECENT_DURATION_WINDOW = 32


@dataclass(frozen=True, slots=True)
class _GameTelemetry:
    plies: int
    book_plies: int
    thinking_seconds: float
    white_clock_ms: int | None
    black_clock_ms: int | None


@dataclass(frozen=True, slots=True)
class TournamentEstimate:
    estimated_finish_at: str | None
    estimated_remaining_seconds: int | None
    median_game_seconds: int | None
    sample_size: int
    remaining_games: int
    projected_total_games: int
    concurrency: int
    confidence: str
    basis: str
    state: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TournamentEstimator:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self._historical: dict[str, list[float]] | None = None

    def estimate(
        self,
        tournament: TournamentRecord,
        games: tuple[GameRecord, ...] | None = None,
        *,
        now: datetime | None = None,
    ) -> TournamentEstimate:
        current_time = now or datetime.now(UTC)
        tournament_games = games if games is not None else list_games(self.connection, tournament.id)
        durations = _game_durations(tournament_games)
        basis = "tournament"
        if durations:
            duration_seconds = _representative_duration(durations)
            median_duration_seconds = float(median(durations))
        else:
            historical = self._historical_durations().get(_config_fingerprint(tournament.config), [])
            if historical:
                duration_seconds = float(median(historical))
                median_duration_seconds = duration_seconds
                durations = historical
                basis = "historical"
            else:
                duration_seconds = 0.0
                median_duration_seconds = 0.0
                basis = "unavailable"

        projected_total = max(len(tournament_games), _projected_game_total(tournament.config))
        terminal_games = sum(game.status in {"finished", "abandoned"} for game in tournament_games)
        future_games = max(0, projected_total - len(tournament_games))
        remaining_games = max(0, len(tournament_games) - terminal_games + future_games)
        concurrency = max(1, tournament.config.concurrency)

        if tournament.status in {"finished", "aborted"} or remaining_games == 0:
            return TournamentEstimate(
                estimated_finish_at=None,
                estimated_remaining_seconds=0,
                median_game_seconds=(
                    round(median_duration_seconds) if median_duration_seconds else None
                ),
                sample_size=len(durations),
                remaining_games=0,
                projected_total_games=projected_total,
                concurrency=concurrency,
                confidence=_confidence(basis, len(durations)),
                basis=basis,
                state="complete",
            )

        if duration_seconds <= 0:
            return TournamentEstimate(
                estimated_finish_at=None,
                estimated_remaining_seconds=None,
                median_game_seconds=None,
                sample_size=0,
                remaining_games=remaining_games,
                projected_total_games=projected_total,
                concurrency=concurrency,
                confidence="unavailable",
                basis="unavailable",
                state="unavailable",
            )

        telemetry = self._game_telemetry(tournament.id) if any(
            game.status == "live" for game in tournament_games
        ) else {}
        completed_plies = [
            item.plies
            for game in tournament_games
            if game.status == "finished"
            if (item := telemetry.get(game.id)) is not None and item.plies > 0
        ]
        remaining_seconds = _remaining_runtime_seconds(
            tournament_games,
            future_games=future_games,
            duration_seconds=duration_seconds,
            concurrency=concurrency,
            now=current_time,
            telemetry=telemetry,
            completed_plies=completed_plies,
            max_game_plies=(tournament.config.adjudication.max_moves or 0) * 2,
            tail_fraction=_tail_fraction(remaining_games, concurrency),
            initial_clock_ms=(
                tournament.config.time_control.initial_ms
                if isinstance(tournament.config.time_control, IncrementTimeControl)
                else 0
            ),
        )
        estimated_finish_at: str | None = None
        state = "estimated"
        if tournament.status == "paused":
            state = "paused"
        elif tournament.status == "scheduled":
            anchor = _parse_datetime(tournament.scheduled_start_at) or current_time
            estimated_finish_at = _iso(max(anchor, current_time) + timedelta(seconds=remaining_seconds))
        elif tournament.status == "running":
            estimated_finish_at = _iso(current_time + timedelta(seconds=remaining_seconds))
        else:
            state = "unavailable"

        return TournamentEstimate(
            estimated_finish_at=estimated_finish_at,
            estimated_remaining_seconds=round(remaining_seconds),
            median_game_seconds=round(median_duration_seconds),
            sample_size=len(durations),
            remaining_games=remaining_games,
            projected_total_games=projected_total,
            concurrency=concurrency,
            confidence=_confidence(basis, len(durations)),
            basis=basis,
            state=state,
        )

    def _game_telemetry(self, tournament_id: int) -> dict[int, _GameTelemetry]:
        rows = self.connection.execute(
            """
            SELECT
              game.id AS game_id,
              (
                SELECT MAX(last_move.ply)
                FROM moves last_move
                WHERE last_move.game_id = game.id
              ) AS plies,
              CASE WHEN game.status = 'live' THEN (
                SELECT SUM(CASE WHEN live_move.is_book = 1 THEN 1 ELSE 0 END)
                FROM moves live_move
                WHERE live_move.game_id = game.id
              ) ELSE 0 END AS book_plies,
              CASE WHEN game.status = 'live' THEN (
                SELECT SUM(live_move.time_ms)
                FROM moves live_move
                WHERE live_move.game_id = game.id
              ) ELSE 0 END AS thinking_ms,
              CASE WHEN game.status = 'live' THEN (
                SELECT latest.clock_after_ms
                FROM moves latest
                WHERE latest.game_id = game.id AND latest.ply %% 2 = 1
                ORDER BY latest.ply DESC
                LIMIT 1
              ) END AS white_clock_ms,
              CASE WHEN game.status = 'live' THEN (
                SELECT latest.clock_after_ms
                FROM moves latest
                WHERE latest.game_id = game.id AND latest.ply %% 2 = 0
                ORDER BY latest.ply DESC
                LIMIT 1
            ) END AS black_clock_ms
            FROM games game
            WHERE game.tournament_id = ?
              AND game.status IN ('finished', 'live')
            """,
            (tournament_id,),
        )
        return {
            int(row["game_id"]): _GameTelemetry(
                plies=int(row["plies"] or 0),
                book_plies=int(row["book_plies"] or 0),
                thinking_seconds=max(0.0, float(row["thinking_ms"] or 0) / 1000.0),
                white_clock_ms=(
                    int(row["white_clock_ms"]) if row["white_clock_ms"] is not None else None
                ),
                black_clock_ms=(
                    int(row["black_clock_ms"]) if row["black_clock_ms"] is not None else None
                ),
            )
            for row in rows
        }

    def _historical_durations(self) -> dict[str, list[float]]:
        if self._historical is not None:
            return self._historical
        grouped: dict[str, list[float]] = {}
        rows = self.connection.execute(
            """
            SELECT tournament.config, game.started_at, game.finished_at
            FROM games game
            JOIN tournaments tournament ON tournament.id = game.tournament_id
            WHERE game.status = 'finished'
              AND game.started_at IS NOT NULL
              AND game.finished_at IS NOT NULL
            ORDER BY game.id DESC
            LIMIT 2000
            """
        )
        for row in rows:
            try:
                config = TournamentConfig.model_validate(json.loads(row["config"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            duration = _duration_seconds(row["started_at"], row["finished_at"])
            if duration is None:
                continue
            grouped.setdefault(_config_fingerprint(config), []).append(duration)
        self._historical = grouped
        return grouped


def completed_tournament_estimate(
    tournament: TournamentRecord,
    *,
    total_games: int,
    median_game_seconds: float | None,
    sample_size: int,
) -> TournamentEstimate:
    basis = "tournament" if sample_size else "unavailable"
    return TournamentEstimate(
        estimated_finish_at=None,
        estimated_remaining_seconds=0,
        median_game_seconds=(
            round(median_game_seconds) if median_game_seconds is not None else None
        ),
        sample_size=sample_size,
        remaining_games=0,
        projected_total_games=max(total_games, _projected_game_total(tournament.config)),
        concurrency=max(1, tournament.config.concurrency),
        confidence=_confidence(basis, sample_size),
        basis=basis,
        state="complete",
    )


def _remaining_runtime_seconds(
    games: tuple[GameRecord, ...],
    *,
    future_games: int,
    duration_seconds: float,
    concurrency: int,
    now: datetime,
    telemetry: dict[int, _GameTelemetry] | None = None,
    completed_plies: list[int] | None = None,
    max_game_plies: int = 0,
    tail_fraction: float = 0.0,
    initial_clock_ms: int = 0,
) -> float:
    game_telemetry = telemetry or {}
    finished_plies = completed_plies or []
    active: list[float] = []
    queued = future_games
    for game in games:
        if game.status == "live":
            started_at = _parse_datetime(game.started_at)
            elapsed = max(0.0, (now - started_at).total_seconds()) if started_at else 0.0
            active.append(
                _live_game_remaining_seconds(
                    elapsed=elapsed,
                    duration_seconds=duration_seconds,
                    telemetry=game_telemetry.get(game.id),
                    completed_plies=finished_plies,
                    max_game_plies=max_game_plies,
                    tail_fraction=tail_fraction,
                    initial_clock_ms=initial_clock_ms,
                )
            )
        elif game.status == "assigned":
            active.append(duration_seconds)
        elif game.status == "pending":
            queued += 1
    if not active:
        active = [0.0] * concurrency
    elif len(active) < concurrency:
        active.extend([0.0] * (concurrency - len(active)))
    heapq.heapify(active)
    while len(active) > concurrency:
        heapq.heappop(active)
    for _ in range(queued):
        available_at = heapq.heappop(active)
        heapq.heappush(active, available_at + duration_seconds)
    return max(active, default=0.0)


def _live_game_remaining_seconds(
    *,
    elapsed: float,
    duration_seconds: float,
    telemetry: _GameTelemetry | None,
    completed_plies: list[int],
    max_game_plies: int,
    tail_fraction: float,
    initial_clock_ms: int,
) -> float:
    duration_remaining = max(0.0, duration_seconds - elapsed)
    if telemetry is None or telemetry.plies < 8 or not completed_plies:
        return duration_remaining

    remaining_plies = _remaining_plies(telemetry.plies, completed_plies)
    if max_game_plies > 0:
        remaining_plies = min(remaining_plies, max(0.0, max_game_plies - telemetry.plies))

    played_plies = max(1, telemetry.plies - telemetry.book_plies)
    observed_pace = elapsed / played_plies if elapsed > 0 else 0.0
    thinking_pace = telemetry.thinking_seconds / played_plies
    typical_pace = duration_seconds / max(1.0, float(median(completed_plies)))
    pace = max(observed_pace, thinking_pace)
    if pace <= 0:
        pace = typical_pace
    pace = min(max(pace, typical_pace * 0.35), typical_pace * 3.0)
    progress_remaining = remaining_plies * pace
    if (
        initial_clock_ms > 0
        and telemetry.white_clock_ms is not None
        and telemetry.black_clock_ms is not None
    ):
        clock_fraction = min(
            1.0,
            max(0.0, telemetry.white_clock_ms + telemetry.black_clock_ms)
            / (initial_clock_ms * 2),
        )
        progress_remaining *= 0.7 + 0.3 * clock_fraction**0.5

    progress_weight = min(0.95, max(0.0, (telemetry.plies - 16) / 96))
    progress_weight = min(1.0, progress_weight + 0.2 * tail_fraction)
    return duration_remaining * (1.0 - progress_weight) + progress_remaining * progress_weight


def _remaining_plies(current_plies: int, completed_plies: list[int]) -> float:
    survivors = [plies - current_plies for plies in completed_plies if plies > current_plies]
    if len(survivors) >= 5:
        return max(MIN_LATE_GAME_PLIES, float(median(survivors)))

    ordered = sorted(completed_plies)
    upper_index = min(len(ordered) - 1, round((len(ordered) - 1) * 0.9))
    upper_tail = max(0.0, float(ordered[upper_index] - current_plies))
    typical_tail = max(MIN_LATE_GAME_PLIES, float(median(ordered)) * 0.08)
    return max(typical_tail, upper_tail)


def _representative_duration(durations: list[float]) -> float:
    overall = float(median(durations))
    if len(durations) < 8:
        return overall
    recent = float(median(durations[-RECENT_DURATION_WINDOW:]))
    return overall * 0.35 + recent * 0.65


def _tail_fraction(remaining_games: int, concurrency: int) -> float:
    if concurrency <= 1:
        return 1.0
    return min(1.0, max(0.0, (concurrency - remaining_games) / (concurrency - 1)))


def _game_durations(games: tuple[GameRecord, ...]) -> list[float]:
    durations: list[float] = []
    for game in games:
        if game.status != "finished":
            continue
        duration = _duration_seconds(game.started_at, game.finished_at)
        if duration is not None:
            durations.append(duration)
    return durations


def _duration_seconds(started_at: str | None, finished_at: str | None) -> float | None:
    started = _parse_datetime(started_at)
    finished = _parse_datetime(finished_at)
    if started is None or finished is None:
        return None
    duration = (finished - started).total_seconds()
    if duration <= 0 or duration > MAX_GAME_DURATION_SECONDS:
        return None
    return duration


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _config_fingerprint(config: TournamentConfig) -> str:
    value = {
        "time_control": config.time_control.model_dump(mode="json"),
        "adjudication": config.adjudication.model_dump(mode="json"),
        "engine_threads": config.engine_threads,
        "engine_hash_mb": config.engine_hash_mb,
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _projected_game_total(config: TournamentConfig) -> int:
    participants = len(config.participants)
    options = config.format_options
    if isinstance(options, RoundRobinFormatOptions):
        return participants * (participants - 1) * options.cycles
    if isinstance(options, SwissFormatOptions):
        return (participants // 2) * options.rounds * 2
    if isinstance(options, KnockoutFormatOptions):
        return max(0, participants - 1) * 2
    if isinstance(options, GauntletFormatOptions):
        return max(0, participants - 1) * options.cycles * 2
    return 0


def _confidence(basis: str, sample_size: int) -> str:
    if basis == "unavailable":
        return "unavailable"
    if basis == "historical" or sample_size < 3:
        return "low"
    if sample_size < 10:
        return "medium"
    return "high"
