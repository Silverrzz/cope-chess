from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
from typing import Annotated,Any,Callable,Literal

from cryptography.fernet import Fernet,InvalidToken
from pydantic import Field,ValidationError

from .contracts import Input
from .errors import ApplicationError
from .identity import current_principal


class ViewRequest(Input):
    view: str = Field(min_length=1,max_length=100,pattern=r"^[a-z][a-z0-9_.]*\.v[1-9][0-9]*$")
    target: str | None = Field(default=None,min_length=1,max_length=100)
    input: dict[str,Any] = Field(default_factory=dict)
    schema_versions: list[Annotated[int,Field(strict=True,ge=1)]] = Field(default_factory=lambda:[1],min_length=1,max_length=8)


class ViewBatchRequest(ViewRequest):
    checkpoint: str = Field(min_length=1,max_length=4096)


class ViewSnapshot(Input):
    view: str
    context: str
    schema_version: Literal[1]
    refresh_mode: Literal["invalidation"]
    data: dict[str,Any]
    checkpoint: str
    expires_at: str


class ViewBatch(Input):
    view: str
    context: str
    schema_version: Literal[1]
    previous_checkpoint: str
    checkpoint: str
    invalidated: bool
    has_more: bool
    expires_at: str


@dataclass(frozen=True)
class CoherentView:
    key: str
    scope: str
    input_model: type[Input]
    output_model: type[Input]
    read: Callable
    affected: Callable
    requires_target: bool = False


def view_catalog() -> dict[str,CoherentView]:
    from cope.domains.activity.views import views as activity_views

    providers = (*activity_views(),)
    if len({provider.key for provider in providers})!=len(providers):
        raise RuntimeError("Duplicate coherent view registration")
    return {provider.key:provider for provider in providers}


VIEWS = view_catalog()


def list_views(connection, principal) -> list[dict]:
    principal = current_principal(connection,principal)
    return [{"key":view.key,"scope":view.scope,"requires_target":view.requires_target,"schema_versions":[1],
             "refresh_mode":"invalidation","input_schema":view.input_model.model_json_schema(),
             "output_schema":view.output_model.model_json_schema()}
            for view in VIEWS.values() if "*" in principal.scopes or view.scope in principal.scopes]


def _prepare(connection, principal, request: ViewRequest):
    isolation = connection.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
    if isolation not in {"repeatable read","serializable"}:
        raise RuntimeError("Coherent views require a fresh repeatable-read application transaction")
    principal = current_principal(connection,principal)
    view = VIEWS.get(request.view)
    if view is None:
        raise ApplicationError("view_unavailable","This coherent view is not installed.",404)
    principal.require(view.scope)
    if not any(type(version) is int and version==1 for version in request.schema_versions):
        raise ApplicationError("view_schema_unavailable","This client requires a compatible view schema before it can synchronize.",426,supported_versions=[1])
    if view.requires_target != (request.target is not None):
        raise ApplicationError("invalid_view_target","Check this view's target contract.",422)
    try:
        payload = view.input_model.model_validate(request.input)
    except ValidationError as error:
        fields = [{"path":list(item["loc"]),"code":item["type"],"message":item["msg"]}
                  for item in error.errors(include_input=False,include_context=False,include_url=False)]
        raise ApplicationError("invalid_view_input","Check the view fields.",422,fields=fields) from error
    row = connection.execute(
        """SELECT state.incarnation,state.retained_after,
                  COALESCE((SELECT position FROM application_event_log ORDER BY position DESC LIMIT 1),0) AS tail,
                  clock_timestamp() AS observed_at,clock_timestamp()+interval '15 minutes' AS expires_at,
                  secret FROM application_delivery_state state CROSS JOIN application_private_keys
           WHERE state.singleton AND purpose='delivery_cursor.v1'""",
    ).fetchone()
    if row is None or row["retained_after"]>row["tail"]:
        raise ApplicationError("delivery_unavailable","Durable replay requires an operator recovery decision.",503)
    key = hashlib.sha256(row["secret"].encode()).digest()
    basis = [view.key,1,principal.actor,sorted(principal.scopes),principal.credential_kind,principal.resource_scope,
             request.target,payload.model_dump(mode="json")]
    context = hmac.new(key,json.dumps(basis,sort_keys=True,separators=(",",":"),allow_nan=False).encode(),hashlib.sha256).hexdigest()
    return view,principal,payload,row,Fernet(base64.urlsafe_b64encode(key)),context


def _checkpoint(cipher, context: str, delivery, position: int) -> str:
    data = {"schema":1,"context":context,"incarnation":str(delivery["incarnation"]),"position":position,
            "expires":int(delivery["expires_at"].timestamp())}
    return cipher.encrypt(json.dumps(data,separators=(",",":")).encode()).decode()


def _position(cipher, context: str, delivery, checkpoint: str) -> int:
    try:
        data = json.loads(cipher.decrypt(checkpoint.encode()))
        if not isinstance(data,dict) or set(data)!={"schema","context","incarnation","position","expires"}:
            raise ValueError()
        if type(data["schema"]) is not int or data["schema"]!=1 or data["context"]!=context or data["incarnation"]!=str(delivery["incarnation"]):
            raise ValueError()
        if type(data["position"]) is not int or type(data["expires"]) is not int:
            raise ValueError()
        if not delivery["retained_after"]<=data["position"]<=delivery["tail"] or data["expires"]<=delivery["observed_at"].timestamp():
            raise ValueError()
        return data["position"]
    except (InvalidToken,ValueError,TypeError,UnicodeError,AttributeError) as error:
        raise ApplicationError("view_rebase_required","Reacquire this view's snapshot with the current credential and context.",409) from error


def execute_snapshot(connection, principal, request: ViewRequest) -> dict:
    view,principal,payload,delivery,cipher,context = _prepare(connection,principal,request)
    data = view.output_model.model_validate(view.read(connection,principal,request.target,payload)).model_dump(mode="json")
    return ViewSnapshot(view=view.key,context=context,schema_version=1,refresh_mode="invalidation",data=data,
                        checkpoint=_checkpoint(cipher,context,delivery,delivery["tail"]),expires_at=delivery["expires_at"].isoformat()).model_dump(mode="json")


def execute_batch(connection, principal, request: ViewBatchRequest) -> dict:
    view,principal,payload,delivery,cipher,context = _prepare(connection,principal,request)
    position = _position(cipher,context,delivery,request.checkpoint)
    events = connection.execute(
        """SELECT log.position,log.schema_version,source.object_id,source.kind FROM application_event_log log
           JOIN application_outbox source ON source.id=log.event_id
           WHERE log.position>%s AND log.position<=%s ORDER BY log.position LIMIT 501""",(position,delivery["tail"]),
    ).fetchall()
    scanned = events[:500]
    if any(event["schema_version"]!=1 for event in scanned):
        raise ApplicationError("view_schema_unavailable","This delivery schema requires a compatible application version.",426,supported_versions=[1])
    invalidated = bool(view.affected(connection,principal,request.target,payload,scanned))
    next_position = scanned[-1]["position"] if scanned else position
    return ViewBatch(view=view.key,context=context,schema_version=1,previous_checkpoint=request.checkpoint,
                     checkpoint=_checkpoint(cipher,context,delivery,next_position),invalidated=invalidated,
                     has_more=len(events)>500,expires_at=delivery["expires_at"].isoformat()).model_dump(mode="json")
