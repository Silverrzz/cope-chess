from __future__ import annotations

from cope.db.donors import donor_leaderboard

from .contracts import Input, Query


class Empty(Input):
    pass


class DonorRow(Input):
    donor_key: str
    name: str
    cpu_hours: float | None
    games: int
    timed_games: int


class DonorLeaderboard(Input):
    donors: list[DonorRow]
    completed_games: int
    unattributed_games: int


def leaderboard(connection, principal, target, payload: Empty) -> dict:
    return donor_leaderboard(connection)


def queries() -> tuple[Query, ...]:
    return (Query("donors.leaderboard.v1", "operations:read", Empty, DonorLeaderboard, leaderboard),)
