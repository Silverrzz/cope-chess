from __future__ import annotations

from cope.application.errors import ApplicationError


def authenticate_bearer(connection, token: str):
    if token.startswith("cope_form_"):
        from cope.domains.forms.storage import authenticate_claim

        return authenticate_claim(connection.native_connection(), token)
    from cope.control.operations import authenticate

    try:
        return authenticate(connection, token)
    except PermissionError as exc:
        raise ApplicationError("invalid_credential", "The credential is invalid, expired or revoked.", 401) from exc
