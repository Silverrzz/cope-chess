from __future__ import annotations

from dataclasses import dataclass

from .errors import ApplicationError


@dataclass(frozen=True)
class Principal:
    actor: str
    scopes: tuple[str, ...]
    token_id: str | None = None

    def require(self, scope: str) -> None:
        if "*" not in self.scopes and scope not in self.scopes:
            raise ApplicationError("permission_denied", "This action is not permitted.", 403)
