from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict,Field,model_validator

from cope.application.contracts import Input
from cope.application.errors import ApplicationError
from cope.application.identity import BrowserCredential,Principal,current_principal


class CallerAuthority(Input):
    model_config = ConfigDict(frozen=True)
    actor: str = Field(min_length=1,max_length=200)
    credential_kind: Literal['control','browser_bootstrap']
    token_id: str | None = Field(default=None,min_length=1,max_length=200)
    browser: BrowserCredential | None = None
    scopes: tuple[str,...] = Field(min_length=1,max_length=64)
    completion_policy: Literal['finish_admitted_pure_work','invalidate_on_revocation']
    mode: Literal['on_behalf_of_caller'] = 'on_behalf_of_caller'

    @model_validator(mode='after')
    def credential_matches(self):
        if len(set(self.scopes))!=len(self.scopes) or any(not scope or scope=='*' or len(scope)>128 for scope in self.scopes):
            raise ValueError('Authority scopes must be distinct bounded permission names')
        if self.credential_kind=='control':
            if not self.token_id or self.browser is not None or self.actor!=f'token:{self.token_id}':
                raise ValueError('Control authority requires its original credential actor')
        elif self.browser is None or self.browser.credential_id is None or self.token_id is not None or self.actor!=f'browser:{self.browser.role}':
            raise ValueError('Browser authority requires a checked session binding')
        return self

    def principal(self) -> Principal:
        return Principal(self.actor,self.scopes,self.token_id,self.credential_kind,browser=self.browser)


def capture_caller_authority(connection, principal: Principal, scope: str, *, required_scopes: tuple[str,...]=(),completion_policy: Literal['finish_admitted_pure_work','invalidate_on_revocation']) -> CallerAuthority:
    principal = current_principal(connection,principal)
    admitted = tuple(sorted({scope,*required_scopes}))
    if '*' in admitted:
        raise ApplicationError('unbounded_execution_authority','Executable work requires explicit bounded permission names.',422)
    for permission in admitted:
        principal.require(permission)
    if principal.resource_scope is not None or principal.credential_kind=='control' and principal.token_id is None:
        raise ApplicationError('execution_credential_required','Canonical work requires a bound current credential or a separately admitted domain mandate.',403)
    if principal.credential_kind not in {'control','browser_bootstrap'}:
        raise ApplicationError('execution_credential_required','This credential cannot delegate canonical execution.',403)
    return CallerAuthority(actor=principal.actor,credential_kind=principal.credential_kind,token_id=principal.token_id,
                           browser=principal.browser,scopes=admitted,completion_policy=completion_policy)


def current_caller_authority(connection, authority: CallerAuthority, scope: str) -> Principal:
    principal = current_principal(connection,authority.principal())
    for permission in authority.scopes:
        principal.require(permission)
    principal.require(scope)
    return principal
