from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime


def source_commit_date(host, repository: str, ref: str) -> str | None:
    if host is None or not host.enabled or not repository or not ref:
        return None
    parts = repository.strip("/").split("/")
    if len(parts) < 2 or any(part in {"", ".", ".."} for part in parts):
        return None
    repository = "/".join(urllib.parse.quote(part, safe="._-") for part in parts)
    revision = urllib.parse.quote(ref, safe="")
    headers = {"Accept": "application/json", "User-Agent": "Cope-engine-dates"}
    if host.provider == "github":
        path = f"/repos/{repository}/commits/{revision}"
        if host.access_token:
            headers["Authorization"] = f"Bearer {host.access_token}"
    elif host.provider == "gitlab":
        path = f"/projects/{urllib.parse.quote(repository, safe='')}/repository/commits/{revision}"
        if host.access_token:
            headers["PRIVATE-TOKEN"] = host.access_token
    else:
        return None
    try:
        request = urllib.request.Request(host.api_url.rstrip("/") + path, headers=headers)
        with urllib.request.urlopen(request, timeout=6) as response:
            payload = json.load(response)
        date = payload.get("commit", {}).get("committer", {}).get("date") if host.provider == "github" else payload.get("committed_date")
        return datetime.fromisoformat(date.replace("Z", "+00:00")).astimezone(UTC).isoformat() if date else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def backfill_source_dates(connection) -> tuple[int, int]:
    from cope.db import get_git_host
    rows = connection.execute("SELECT id, git_host_id, repository_full_name, source_ref FROM engine_versions WHERE source_date IS NULL AND distribution = 'managed'").fetchall()
    connection.commit()
    hosts = {}
    keys = set()
    for row in rows:
        host_id = row["git_host_id"]
        if host_id not in hosts:
            hosts[host_id] = get_git_host(connection, host_id) if host_id is not None else None
            connection.commit()
        keys.add((host_id, row["repository_full_name"], row["source_ref"]))
    def resolve(key):
        return key, source_commit_date(hosts[key[0]], key[1], key[2])
    with ThreadPoolExecutor(max_workers=4) as executor:
        dates = dict(executor.map(resolve, keys))
    updated = 0
    for row in rows:
        key = (row["git_host_id"], row["repository_full_name"], row["source_ref"])
        if dates[key]:
            connection.execute("UPDATE engine_versions SET source_date = ? WHERE id = ? AND source_date IS NULL", (dates[key], row["id"]))
            updated += 1
    connection.commit()
    return updated, len(rows) - updated
