from __future__ import annotations

import psycopg

from cope.application.errors import ApplicationError
from cope.application.identity import Principal,current_principal


def run_coherent_application(connection, identity, handler, *args):
    raw = connection.native_connection()
    if raw.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        raise RuntimeError("Coherent application reads require a fresh database transaction")
    try:
        raw.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        raw.execute("SET LOCAL statement_timeout='5s'")
        return run_application(connection,identity,handler,*args)
    except Exception:
        connection.rollback()
        raise


def run_application(connection, identity, handler, *args, **kwargs):
    principal = identity if isinstance(identity, Principal) else Principal(identity.actor, tuple(identity.scopes), identity.token_id)
    try:
        raw = connection.native_connection()
        principal = current_principal(raw,principal)
        result = handler(raw, principal, *args, **kwargs)
        connection.commit()
        return result
    except psycopg.errors.UniqueViolation as exc:
        connection.rollback()
        raise ApplicationError("resource_conflict", "A resource with these values already exists.") from exc
    except (psycopg.errors.LockNotAvailable, psycopg.errors.DeadlockDetected, psycopg.errors.SerializationFailure) as exc:
        connection.rollback()
        raise ApplicationError("concurrent_change", "Concurrent work prevented this edit. Retry the same request.") from exc
    except psycopg.Error as exc:
        connection.rollback()
        if exc.sqlstate=='P7501':
            raise ApplicationError('execution_ownership_changed','Legacy execution routing changed. Refresh the owning workflow before retrying.',409) from exc
        raise
    except Exception:
        connection.rollback()
        raise
