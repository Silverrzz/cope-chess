from __future__ import annotations

from .contracts import Input, Query
from .errors import ApplicationError


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
    from cope.web.donor_snapshot import read_donor_snapshot

    snapshot = read_donor_snapshot()
    if snapshot is None:
        raise ApplicationError("donors_snapshot_unavailable", "The donor leaderboard is being prepared.", 503)
    return snapshot


def queries() -> tuple[Query, ...]:
    return (Query("donors.leaderboard.v1", "operations:read", Empty, DonorLeaderboard, leaderboard),)
