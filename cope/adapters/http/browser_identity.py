from __future__ import annotations

import hashlib

from cope.application.errors import ApplicationError
from cope.application.identity import BrowserCredential,Principal,browser_principal,bootstrap_token_digest

from .authentication import ADMIN_SESSION_MAX_AGE_SECONDS,admin_session_role,admin_token,manager_token,signed_value_nonce


def request_browser_principal(request) -> Principal:
    internal = request.scope.get('cope.control.internal')
    if internal is not None:
        identity = internal['principal']
        if isinstance(identity,Principal):
            return identity
        return Principal(identity.actor,tuple(identity.scopes),identity.token_id)
    role = admin_session_role(request)
    if role not in {'admin','manager'}:
        raise ApplicationError('authentication_required','A current authenticated browser session is required.',401)
    secret = admin_token(request) if role=='admin' else manager_token(request)
    cookie = request.cookies.get('cope_admin_session','')
    if not secret or not cookie or len(cookie)>1024 or signed_value_nonce(secret,cookie) is None:
        raise ApplicationError('credential_revoked','The browser credential expired or changed.',403)
    return cookie_browser_principal(role,secret,cookie)


def cookie_browser_principal(role: str, secret: str, cookie: str) -> Principal:
    if not cookie or len(cookie)>1024 or signed_value_nonce(secret,cookie) is None:
        raise ApplicationError('credential_revoked','The browser credential expired or changed.',403)
    issued_at = int(cookie.split('.',1)[0])
    identity = browser_principal(role)
    binding = BrowserCredential(role=role,session_id=hashlib.sha256(cookie.encode()).hexdigest(),
                                token_digest=bootstrap_token_digest(secret),expires_at=issued_at+ADMIN_SESSION_MAX_AGE_SECONDS)
    return Principal(identity.actor,identity.scopes,credential_kind='browser_bootstrap',browser=binding)
