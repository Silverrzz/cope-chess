from __future__ import annotations

import asyncio
import logging
import time

from cope.db import TournamentRecord, connect_database, list_games_for_tournaments, list_tournaments
from cope.tournament.estimates import TournamentEstimate, TournamentEstimator


LOG = logging.getLogger(__name__)
ESTIMATE_REFRESH_SECONDS = 30
ESTIMATE_MAX_AGE_SECONDS = 90


class TournamentEstimateCache:
    def __init__(self) -> None:
        self._entries: dict[int, tuple[TournamentRecord, TournamentEstimate, float]] = {}

    def get(self, tournament: TournamentRecord) -> TournamentEstimate | None:
        entry = self._entries.get(tournament.id)
        if entry is None:
            return None
        previous, estimate, calculated_at = entry
        if (
            time.monotonic() - calculated_at > ESTIMATE_MAX_AGE_SECONDS
            or previous.status != tournament.status
            or previous.config != tournament.config
            or previous.scheduled_start_at != tournament.scheduled_start_at
            or previous.started_at != tournament.started_at
        ):
            return None
        return estimate

    async def run(self, database_url: str) -> None:
        while True:
            try:
                await asyncio.to_thread(self.refresh, database_url)
            except Exception:
                LOG.exception("tournament estimate refresh failed")
            await asyncio.sleep(ESTIMATE_REFRESH_SECONDS)

    def refresh(self, database_url: str) -> None:
        connection = connect_database(database_url)
        try:
            tournaments = tuple(
                tournament
                for tournament in list_tournaments(connection)
                if tournament.status in {"scheduled", "running", "paused"}
            )
            games_by_tournament = {tournament.id: [] for tournament in tournaments}
            for game in list_games_for_tournaments(connection, games_by_tournament):
                if game.record_eligible:
                    games_by_tournament[game.tournament_id].append(game)
            estimator = TournamentEstimator(connection)
            entries = {
                tournament_id: entry
                for tournament_id, entry in self._entries.items()
                if tournament_id in games_by_tournament
            }
            for tournament in tournaments:
                try:
                    estimate = estimator.estimate(
                        tournament,
                        tuple(games_by_tournament[tournament.id]),
                    )
                except Exception:
                    LOG.exception("tournament %s estimate failed", tournament.id)
                    continue
                entries[tournament.id] = (tournament, estimate, time.monotonic())
            self._entries = entries
        finally:
            connection.close()
