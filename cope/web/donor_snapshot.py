from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from tempfile import NamedTemporaryFile

from cope.db import connect_database
from cope.db.donors import donor_leaderboard
from cope.infrastructure.artifact_files import storage_root

LOG = logging.getLogger(__name__)


def _snapshot_path() -> Path:
    return storage_root() / "snapshots" / "donors.json"


def _read_snapshot() -> dict | None:
    try:
        snapshot = json.loads(_snapshot_path().read_text(encoding="utf-8"))
        if isinstance(snapshot, dict) and isinstance(snapshot.get("data"), dict):
            return snapshot
    except (OSError, ValueError):
        pass
    return None


def read_donor_snapshot() -> dict | None:
    snapshot = _read_snapshot()
    return snapshot["data"] if snapshot is not None else None


def _write_snapshot(snapshot: dict) -> None:
    destination = _snapshot_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(snapshot, output, separators=(",", ":"))
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _refresh_snapshot(database_url: str) -> None:
    connection = connect_database(database_url)
    try:
        if not connection.execute(
            "SELECT pg_try_advisory_xact_lock(84541773018) AS locked"
        ).fetchone()["locked"]:
            return
        revision = connection.execute(
            "SELECT COALESCE(MAX(id), 0) AS revision FROM engine_elo_history"
        ).fetchone()["revision"]
        snapshot = _read_snapshot()
        if snapshot is not None and snapshot.get("rating_revision") == revision:
            return
        _write_snapshot({"rating_revision": revision, "data": donor_leaderboard(connection)})
    finally:
        connection.close()


async def run_donor_snapshot_updates(database_url: str) -> None:
    while True:
        try:
            await asyncio.to_thread(_refresh_snapshot, database_url)
        except (OSError, sqlite3.Error):
            LOG.exception("Could not refresh the donor snapshot")
            await asyncio.sleep(60)
        else:
            await asyncio.sleep(10)
