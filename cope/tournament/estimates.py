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
    KnockoutFormatOptions,
    RoundRobinFormatOptions,
    SwissFormatOptions,
    TournamentConfig,
)
from cope.db import GameRecord, TournamentRecord, list_games


MAX_GAME_DURATION_SECONDS = 7 * 24 * 60 * 60


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
            duration_seconds = float(median(durations))
        else:
            historical = self._historical_durations().get(_config_fingerprint(tournament.config), [])
            if historical:
                duration_seconds = float(median(historical))
                durations = historical
                basis = "historical"
            else:
                duration_seconds = 0.0
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
                median_game_seconds=round(duration_seconds) if duration_seconds else None,
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

        remaining_seconds = _remaining_runtime_seconds(
            tournament_games,
            future_games=future_games,
            duration_seconds=duration_seconds,
            concurrency=concurrency,
            now=current_time,
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
            median_game_seconds=round(duration_seconds),
            sample_size=len(durations),
            remaining_games=remaining_games,
            projected_total_games=projected_total,
            concurrency=concurrency,
            confidence=_confidence(basis, len(durations)),
            basis=basis,
            state=state,
        )

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


def _remaining_runtime_seconds(
    games: tuple[GameRecord, ...],
    *,
    future_games: int,
    duration_seconds: float,
    concurrency: int,
    now: datetime,
) -> float:
    active: list[float] = []
    queued = future_games
    for game in games:
        if game.status == "live":
            started_at = _parse_datetime(game.started_at)
            elapsed = max(0.0, (now - started_at).total_seconds()) if started_at else 0.0
            active.append(max(0.0, duration_seconds - elapsed))
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
        return max(0, participants - 1) * 2
    return 0


def _confidence(basis: str, sample_size: int) -> str:
    if basis == "unavailable":
        return "unavailable"
    if basis == "historical" or sample_size < 3:
        return "low"
    if sample_size < 10:
        return "medium"
    return "high"
