import hashlib
import secrets

from cope.core.models import WorkerIdentityHello


IDENTITY_TOKEN_PREFIX = "cope_identity_"


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return IDENTITY_TOKEN_PREFIX + secrets.token_urlsafe(32)


def list_worker_identities(connection):
    return connection.execute(
        """SELECT identity.id, identity.label, identity.donor_name,
                  identity.revoked_at IS NOT NULL AS revoked,
                  (SELECT COUNT(*) FROM workers worker
                   WHERE worker.worker_identity_id = identity.id
                     AND worker.status IN ('connected', 'downloading', 'ready', 'busy')) AS connected
           FROM worker_identities identity
           ORDER BY identity.revoked_at IS NOT NULL, lower(identity.label), identity.id"""
    ).fetchall()


def create_worker_identity(connection, label: str, donor_name: str) -> tuple[int, str]:
    token = _new_token()
    row = connection.execute(
        """INSERT INTO worker_identities (label, donor_name, token_hash)
           VALUES (?, ?, ?) RETURNING id""",
        (label, donor_name, _token_hash(token)),
    ).fetchone()
    return int(row["id"]), token


def update_worker_identity(connection, identity_id: int, label: str, donor_name: str) -> bool:
    return connection.execute(
        "UPDATE worker_identities SET label = ?, donor_name = ? WHERE id = ?",
        (label, donor_name, identity_id),
    ).rowcount == 1


def authenticate_worker_identity(connection, hello: WorkerIdentityHello) -> int | None:
    identity = connection.execute(
        """SELECT id FROM worker_identities
           WHERE token_hash = ? AND revoked_at IS NULL FOR SHARE""",
        (_token_hash(hello.identity_token),),
    ).fetchone()
    return int(identity["id"]) if identity else None


def get_or_create_identity_worker(connection, identity_id: int, hello: WorkerIdentityHello) -> int | None:
    row = connection.execute(
        """INSERT INTO workers (label, machine_id, worker_identity_id, status)
           VALUES (?, ?, ?, 'offline')
           ON CONFLICT (machine_id) WHERE machine_id IS NOT NULL
           DO NOTHING
           RETURNING id""",
        (hello.label_hint or "Worker", hello.machine_id, identity_id),
    ).fetchone()
    if row is None:
        row = connection.execute(
            "SELECT id FROM workers WHERE machine_id = ? AND worker_identity_id = ?",
            (hello.machine_id, identity_id),
        ).fetchone()
    return int(row["id"]) if row else None


def reset_worker_identity(connection, identity_id: int, *, revoke: bool) -> tuple[bool, str | None, list[int]]:
    from cope.db.repo import disconnect_worker
    from cope.db.tools import release_worker_tool_jobs

    token = None if revoke else _new_token()
    updated = connection.execute(
        """UPDATE worker_identities
           SET token_hash = ?, revoked_at = CASE WHEN ? THEN now() ELSE NULL END
           WHERE id = ? AND revoked_at IS NULL""",
        (_token_hash(token) if token else None, revoke, identity_id),
    ).rowcount
    if not updated:
        return False, None, []
    tournament_ids = set()
    workers = connection.execute(
        "SELECT id FROM workers WHERE worker_identity_id = ? AND session_id IS NOT NULL ORDER BY id",
        (identity_id,),
    ).fetchall()
    for worker in workers:
        worker_id = worker["id"]
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
            (f"worker:{worker_id}",),
        )
        tournament_ids.update(disconnect_worker(connection, worker_id, reason="worker identity credential changed"))
        release_worker_tool_jobs(connection, worker_id)
    connection.execute(
        "UPDATE workers SET session_id = NULL WHERE worker_identity_id = ?",
        (identity_id,),
    )
    return True, token, sorted(tournament_ids)
