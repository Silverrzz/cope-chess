from __future__ import annotations

import hashlib
from importlib import resources


def migration_status(connection) -> dict:
    package = resources.files("cope.infrastructure.postgres.migrations")
    expected = {
        path.name: hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest()
        for path in package.iterdir() if path.name.endswith(".sql")
    }
    present = connection.execute("SELECT to_regclass('application_migrations') AS relation").fetchone()
    installed = {} if present["relation"] is None else {
        row["version"]: row["checksum"] for row in connection.execute("SELECT version, checksum FROM application_migrations")
    }
    missing = sorted(expected.keys() - installed.keys())
    unsupported = sorted(installed.keys() - expected.keys())
    changed = sorted(key for key in installed.keys() & expected.keys() if installed[key] != expected[key])
    return {"ready": not (missing or unsupported or changed), "missing": missing, "unsupported": unsupported, "changed": changed}


def apply_migrations(connection) -> list[str]:
    connection.execute("SELECT pg_advisory_xact_lock(684392719405)")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS application_migrations (
            version TEXT PRIMARY KEY,
            checksum TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        )"""
    )
    installed = {
        row["version"]: row["checksum"]
        for row in connection.execute("SELECT version, checksum FROM application_migrations")
    }
    package = resources.files("cope.infrastructure.postgres.migrations")
    sources = sorted((path for path in package.iterdir() if path.name.endswith(".sql")), key=lambda path: path.name)
    available = {path.name for path in sources}
    if installed.keys() - available:
        raise RuntimeError("Database contains application migrations unsupported by this release")
    applied = []
    for path in sources:
        source = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(source.encode()).hexdigest()
        if path.name in installed:
            if installed[path.name] != checksum:
                raise RuntimeError(f"Applied migration checksum mismatch: {path.name}")
            continue
        connection.execute(source)
        connection.execute(
            "INSERT INTO application_migrations(version, checksum) VALUES (%s, %s)",
            (path.name, checksum),
        )
        applied.append(path.name)
    return applied
