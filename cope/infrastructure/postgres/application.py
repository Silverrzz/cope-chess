from __future__ import annotations

import psycopg

from cope.application.errors import ApplicationError
from cope.application.identity import Principal


def run_application(connection, identity, handler, *args, **kwargs):
    principal = Principal(identity.actor, tuple(identity.scopes), identity.token_id)
    try:
        result = handler(connection.native_connection(), principal, *args, **kwargs)
        connection.commit()
        return result
    except psycopg.errors.UniqueViolation as exc:
        connection.rollback()
        raise ApplicationError("resource_conflict", "A resource with these values already exists.") from exc
    except (psycopg.errors.LockNotAvailable, psycopg.errors.DeadlockDetected, psycopg.errors.SerializationFailure) as exc:
        connection.rollback()
        raise ApplicationError("concurrent_change", "Concurrent work prevented this edit. Retry the same request.") from exc
    except Exception:
        connection.rollback()
        raise
