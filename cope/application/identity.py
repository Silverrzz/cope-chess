from __future__ import annotations

from dataclasses import dataclass
import json

from .errors import ApplicationError


@dataclass(frozen=True)
class Principal:
    actor: str
    scopes: tuple[str, ...]
    token_id: str | None = None
    credential_kind: str = "control"
    resource_scope: str | None = None

    def require(self, scope: str) -> None:
        if "*" not in self.scopes and scope not in self.scopes:
            raise ApplicationError("permission_denied", "This action is not permitted.", 403)


def browser_principal(role: str) -> Principal:
    if role == "admin":
        return Principal("browser:admin", ("*",))
    if role == "manager":
        return Principal("browser:manager", ("operations:read", "engines:write", "tournaments:write",
                                              "forms:submit", "forms:amend", "forms:responses:read"))
    raise ApplicationError("authentication_required", "A current authenticated session is required.", 401)


def current_principal(connection, principal: Principal) -> Principal:
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
