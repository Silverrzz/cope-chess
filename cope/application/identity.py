from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from typing import Literal
from uuid import UUID,uuid4

from pydantic import BaseModel,ConfigDict,Field

from .errors import ApplicationError


class BrowserCredential(BaseModel):
    model_config = ConfigDict(extra='forbid',frozen=True)
    role: Literal['admin','manager']
    session_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    token_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    expires_at: int = Field(strict=True,gt=0)
    credential_id: UUID | None = None


def bootstrap_authority_lock(connection, *, exclusive=False):
    function = 'pg_advisory_xact_lock' if exclusive else 'pg_advisory_xact_lock_shared'
    connection.execute(f"SELECT {function}(hashtextextended('application-bootstrap-credentials.v1',0))")
    if connection.execute('SELECT generation FROM application_bootstrap_epoch WHERE singleton FOR SHARE').fetchone() is None:
        raise ApplicationError('credential_unavailable','The bootstrap credential registry is unavailable.',503)


def bootstrap_token_digest(token: str) -> str:
    return hashlib.sha256(b'cope-browser-credential.v1\0'+token.encode()).hexdigest()


def bootstrap_secret(connection, role: str, *, configured_admin: str | None=None) -> str | None:
    if role not in {'admin','manager'}:
        raise ApplicationError('invalid_credential','This bootstrap role is unavailable.',403)
    state = connection.execute('SELECT recovery_locked FROM application_bootstrap_roles WHERE role=%s FOR SHARE',(role,)).fetchone()
    if state is None or state['recovery_locked']:
        return None
    row = connection.execute('SELECT token FROM admin_access_tokens WHERE role=%s FOR SHARE',(role,)).fetchone()
    if row is not None:
        return row['token'] or None
    if role=='admin':
        if configured_admin is not None:
            return configured_admin
        from cope.network import default_admin_token

        try:
            return default_admin_token()
        except ValueError as error:
            raise ApplicationError('credential_unavailable','The configured bootstrap credential is unavailable.',503) from error
    return None


def bootstrap_role_state(connection, role: str):
    row = connection.execute('SELECT generation,token_digest,sessions_valid_after,recovery_locked FROM application_bootstrap_roles WHERE role=%s FOR SHARE',(role,)).fetchone()
    if row is None:
        raise ApplicationError('credential_unavailable','The bootstrap credential registry is unavailable.',503)
    return row


def register_browser_credential(connection, binding: BrowserCredential, generation: int) -> BrowserCredential:
    def lookup():
        return connection.execute('SELECT * FROM application_browser_sessions WHERE session_hash=%s FOR SHARE',(binding.session_id,)).fetchone()

    row = lookup()
    if row is None:
        if binding.credential_id is not None:
            raise ApplicationError('credential_revoked','The initiating browser credential is unavailable.',403)
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",('browser-session:'+binding.session_id,))
        row = lookup()
        if row is None:
            credential_id = uuid4()
            connection.execute("INSERT INTO application_objects(id,type_key) VALUES (%s,'credential')",(credential_id,))
            row = connection.execute('''INSERT INTO application_browser_sessions(id,role,role_generation,session_hash,token_digest,issued_at,expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (credential_id,binding.role,generation,binding.session_id,binding.token_digest,binding.expires_at-43200,binding.expires_at)).fetchone()
    if (row['revoked_at'] is not None or row['role']!=binding.role or row['role_generation']!=generation
        or row['expires_at']!=binding.expires_at or not hmac.compare_digest(row['token_digest'],binding.token_digest)
        or binding.credential_id is not None and row['id']!=binding.credential_id):
        raise ApplicationError('credential_revoked','The initiating browser credential expired or was revoked.',403)
    return binding.model_copy(update={'credential_id':row['id']})


def revoke_browser_credential(connection, cookie: str, principal: Principal | None) -> None:
    if not cookie or len(cookie)>1024:
        return
    session_id = hashlib.sha256(cookie.encode()).hexdigest()
    bootstrap_authority_lock(connection)
    connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('browser-session:'+session_id,))
    row = connection.execute('SELECT id FROM application_browser_sessions WHERE session_hash=%s FOR UPDATE',(session_id,)).fetchone()
    if row is None:
        if principal is None:
            return
        identity = current_principal(connection,principal)
        if identity.browser is None or identity.browser.session_id!=session_id:
            raise ApplicationError('credential_mismatch','The browser credential does not match this session.',403)
        row = {'id':identity.browser.credential_id}
    connection.execute('UPDATE application_browser_sessions SET revoked_at=clock_timestamp() WHERE id=%s AND revoked_at IS NULL',(row['id'],))


@dataclass(frozen=True)
class Principal:
    actor: str
    scopes: tuple[str, ...]
    token_id: str | None = None
    credential_kind: str = "control"
    resource_scope: str | None = None
    browser: BrowserCredential | None = None

    def require(self, scope: str) -> None:
        if "*" not in self.scopes and scope not in self.scopes:
            raise ApplicationError("permission_denied", "This action is not permitted.", 403)


def browser_principal(role: str) -> Principal:
    if role == "admin":
        return Principal("browser:admin", ("*",))
    if role == "manager":
        return Principal("browser:manager", ("operations:read", "activity:read", "engines:write", "tournaments:write",
                                              "forms:submit", "forms:amend", "forms:responses:read"))
    raise ApplicationError("authentication_required", "A current authenticated session is required.", 401)


def current_principal(connection, principal: Principal) -> Principal:
    if principal.credential_kind=='browser_bootstrap':
        binding = principal.browser
        if binding is None or principal.actor!=f'browser:{binding.role}' or principal.token_id is not None or principal.resource_scope is not None:
            raise ApplicationError('credential_mismatch','The browser credential does not match this legacy actor.',403)
        bootstrap_authority_lock(connection)
        now = connection.execute('SELECT extract(epoch FROM clock_timestamp()) AS now').fetchone()['now']
        root = bootstrap_role_state(connection,binding.role)
        secret = connection.execute('SELECT token FROM admin_access_tokens WHERE role=%s FOR SHARE',(binding.role,)).fetchone()
        if (root['recovery_locked'] or binding.expires_at<=now or binding.expires_at-43200>now or binding.expires_at-43200<root['sessions_valid_after']
            or secret is not None and (not secret['token'] or not hmac.compare_digest(binding.token_digest,bootstrap_token_digest(secret['token'])))
            or secret is None and binding.role!='admin'
            or root['token_digest']!=binding.token_digest):
            raise ApplicationError('credential_revoked','The initiating browser credential expired or changed.',403)
        binding = register_browser_credential(connection,binding,root['generation'])
        allowed = set(browser_principal(binding.role).scopes)
        effective = principal.scopes if '*' in allowed else tuple(sorted(allowed if '*' in principal.scopes else allowed.intersection(principal.scopes)))
        return Principal(principal.actor,effective,credential_kind='browser_bootstrap',browser=binding)
    if principal.token_id is None:
        return principal
    if principal.credential_kind == "form_claim":
        if principal.actor != f"form-claim:{principal.token_id}":
            raise ApplicationError("credential_mismatch", "The credential does not match the acting identity.", 403)
        row = connection.execute(
            """SELECT form_id FROM form_claims WHERE id = %s AND revoked_at IS NULL
               AND expires_at > clock_timestamp() FOR SHARE""", (principal.token_id,),
        ).fetchone()
        if row is None or str(row["form_id"]) != principal.resource_scope:
            raise ApplicationError("credential_revoked", "This form claim expired or was revoked.", 403)
        scopes = tuple(scope for scope in principal.scopes if scope in {"forms:submit", "forms:amend", "forms:responses:read"})
        return Principal(principal.actor, scopes, principal.token_id, "form_claim", str(row["form_id"]))
    if principal.credential_kind != "control" or principal.actor != f"token:{principal.token_id}":
        raise ApplicationError("credential_mismatch", "The credential does not match the acting identity.", 403)
    row = connection.execute(
        """SELECT scopes FROM control_tokens
           WHERE id = %s AND revoked_at IS NULL AND expires_at > clock_timestamp() FOR SHARE""",
        (principal.token_id,),
    ).fetchone()
    if row is None:
        raise ApplicationError("credential_revoked", "The initiating credential is no longer valid.", 403)
    allowed = set(json.loads(row["scopes"]))
    effective = principal.scopes if "*" in allowed else tuple(allowed if "*" in principal.scopes else allowed.intersection(principal.scopes))
    return Principal(principal.actor, effective, principal.token_id)
