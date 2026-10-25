from __future__ import annotations

import hmac
import ipaddress
import sqlite3
import time
from datetime import UTC, datetime

from fastapi import Request

from cope.db import connect_database
from cope.network import LOCAL_EVENT_PUBLISHERS


ADMIN_SESSION_MAX_AGE_SECONDS = 43_200


def admin_token(request: Request) -> str | None:
    load_access_tokens(request)
    return getattr(request.app.state, "admin_token", None) or None


def manager_token(request: Request) -> str | None:
    load_access_tokens(request)
    return getattr(request.app.state, "manager_token", None) or None


def load_access_tokens(request: Request) -> None:
    now = time.monotonic()
    if getattr(request.app.state, "access_tokens_loaded", False) and now - getattr(request.app.state, "access_tokens_loaded_at", 0.0) < 1.0:
        return
    connection = connect_database(request.app.state.db_path)
    try:
        rows = connection.execute(
            "SELECT role, token FROM admin_access_tokens"
        ).fetchall()
    except sqlite3.Error:
        return
    finally:
        connection.close()
    for row in rows:
        if row["role"] == "admin" and row["token"]:
            request.app.state.admin_token = str(row["token"])
        elif row["role"] == "manager" and row["token"]:
            request.app.state.manager_token = str(row["token"])
    request.app.state.access_tokens_loaded = True
    request.app.state.access_tokens_loaded_at = now


def admin_session_role(request: Request) -> str | None:
    internal = request.scope.get("cope.control.internal")
    if internal is not None:
        return "admin" if "*" in internal["principal"].scopes else "manager"
    admin_secret = admin_token(request)
    if admin_secret and admin_session_valid(request, admin_secret):
        return "admin"
    manager_secret = manager_token(request)
    if manager_secret and admin_session_valid(request, manager_secret):
        return "manager"
    return None


def admin_session_valid(request: Request, token: str) -> bool:
    value = request.cookies.get("cope_admin_session", "")
    if not value:
        return False
    nonce = signed_value_nonce(token, value)
    return nonce is not None


def csrf_token(request: Request, token: str | None) -> str:
    if token is None:
        return ""
    nonce = signed_value_nonce(token, request.cookies.get("cope_admin_session", ""))
    if nonce is None:
        return ""
    return csrf_for_nonce(token, nonce)


def csrf_token_valid(request: Request, token: str, supplied: str) -> bool:
    expected = csrf_token(request, token)
    return bool(expected and supplied and hmac.compare_digest(supplied, expected))


def signed_value(token: str, nonce: str, *, issued_at: int | None = None) -> str:
    timestamp = issued_at if issued_at is not None else int(datetime.now(UTC).timestamp())
    payload = f"{timestamp}.{nonce}"
    signature = hmac.digest(
        token.encode("utf-8"),
        payload.encode("utf-8"),
        "sha256",
    ).hex()
    return f"{payload}.{signature}"


def signed_value_nonce(token: str, value: str) -> str | None:
    parts = value.split(".")
    if len(parts) != 3:
        return None
    timestamp_text, nonce, supplied = parts
    if not timestamp_text or not nonce or not supplied:
        return None
    try:
        issued_at = int(timestamp_text)
    except ValueError:
        return None
    payload = f"{timestamp_text}.{nonce}"
    expected = hmac.digest(
        token.encode("utf-8"),
        payload.encode("utf-8"),
        "sha256",
    ).hex()
    if not hmac.compare_digest(supplied, expected):
        return None
    now = int(datetime.now(UTC).timestamp())
    if issued_at > now or now - issued_at >= ADMIN_SESSION_MAX_AGE_SECONDS:
        return None
    return nonce


def csrf_for_nonce(token: str, nonce: str) -> str:
    return hmac.digest(
        token.encode("utf-8"),
        f"csrf:{nonce}".encode("utf-8"),
        "sha256",
    ).hex()


def request_is_secure(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "")
    peer_is_private = False
    if request.client is not None:
        try:
            peer_is_private = ipaddress.ip_address(request.client.host).is_private
        except ValueError:
            peer_is_private = False
    return request.url.scheme == "https" or (
        peer_is_private and forwarded.split(",", 1)[0].strip() == "https"
    )


def request_is_secure_or_local(request: Request) -> bool:
    if request_is_secure(request):
        return True
    if request.client is None:
        return True
    return request.client.host in LOCAL_EVENT_PUBLISHERS
