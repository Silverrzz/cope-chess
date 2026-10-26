from __future__ import annotations

import hmac
import hashlib
import ipaddress
import sqlite3
from datetime import UTC, datetime

from fastapi import Request
import psycopg

from cope.application.errors import ApplicationError
from cope.application.identity import bootstrap_authority_lock,bootstrap_token_digest
from cope.db import connect_database
from cope.network import LOCAL_EVENT_PUBLISHERS


ADMIN_SESSION_MAX_AGE_SECONDS = 43_200


def admin_token(request: Request) -> str | None:
    load_access_tokens(request)
    return request.scope['cope.bootstrap.credentials']['admin']


def manager_token(request: Request) -> str | None:
    load_access_tokens(request)
    return request.scope['cope.bootstrap.credentials']['manager']


def load_access_tokens(request: Request) -> None:
    if 'cope.bootstrap.credentials' in request.scope:
        return
    connection = connect_database(request.app.state.db_path)
    credentials = {'admin':None,'manager':None,'session_role':None}
    try:
        for exclusive in (False,True):
            raw = connection.native_connection()
            bootstrap_authority_lock(raw,exclusive=exclusive)
            tokens = {row['role']:row['token'] or None for row in raw.execute('SELECT role,token FROM admin_access_tokens FOR SHARE').fetchall()}
            credentials = {'admin':tokens.get('admin',request.app.state.configured_admin_token),'manager':tokens.get('manager'),'session_role':None}
            roots = {row['role']:row for row in raw.execute('SELECT role,generation,token_digest FROM application_bootstrap_roles FOR SHARE').fetchall()}
            changed = [role for role in ('admin','manager') if roots[role]['token_digest']!=(bootstrap_token_digest(credentials[role]) if credentials[role] else None)]
            if changed and not exclusive:
                connection.rollback()
                continue
            for role in changed:
                raw.execute('UPDATE application_bootstrap_roles SET generation=generation+1,token_digest=%s WHERE role=%s',
                            (bootstrap_token_digest(credentials[role]) if credentials[role] else None,role))
                roots[role]=raw.execute('SELECT generation,token_digest FROM application_bootstrap_roles WHERE role=%s',(role,)).fetchone()
            value = request.cookies.get('cope_admin_session','')
            if value and len(value)<=1024:
                for role in ('admin','manager'):
                    secret = credentials[role]
                    if secret and signed_value_nonce(secret,value) is not None:
                        session = raw.execute('SELECT role,role_generation,revoked_at FROM application_browser_sessions WHERE session_hash=%s FOR SHARE',
                                              (hashlib.sha256(value.encode()).hexdigest(),)).fetchone()
                        if session is None or session['revoked_at'] is None and session['role']==role and session['role_generation']==roots[role]['generation']:
                            credentials['session_role']=role
                        break
            connection.commit()
            break
    except (sqlite3.Error,psycopg.Error,ApplicationError):
        connection.rollback()
        credentials = {'admin':None,'manager':None,'session_role':None}
    finally:
        connection.close()
    request.scope['cope.bootstrap.credentials']=credentials


def admin_session_role(request: Request) -> str | None:
    internal = request.scope.get("cope.control.internal")
    if internal is not None:
        return "admin" if "*" in internal["principal"].scopes else "manager"
    load_access_tokens(request)
    return request.scope['cope.bootstrap.credentials']['session_role']


def admin_session_valid(request: Request, token: str) -> bool:
    value = request.cookies.get("cope_admin_session", "")
    if not value:
        return False
    if admin_session_role(request) is None:
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
