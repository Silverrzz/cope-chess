from __future__ import annotations

import uuid
from typing import Literal

from pydantic import Field

from cope.application.contracts import Input,Query
from cope.application.errors import ApplicationError
from cope.application.pagination import page_basis,page_cursor

from .sources import sources


State = Literal["planned","blocked","queued","leased","exported","running","waiting","cancelling","succeeded","failed","cancelled","unknown"]


class ActivityInput(Input):
    kind: Literal["operation","job"] | None = None
    source: str | None = Field(default=None,min_length=1,max_length=100,pattern=r"^[a-z][a-z0-9_.]*$")
    state: State | None = None
    limit: int = Field(default=30,strict=True,ge=1,le=100)
    cursor: str | None = Field(default=None,max_length=4096)


class Empty(Input):
    pass


class Count(Input):
    count: int
    more: bool
    limit: int = 1000


class ActivityItem(Input):
    id: str
    kind: Literal["operation","job"]
    source: str
    source_id: str
    title: str
    state: State
    source_state: str
    owner_actor: str | None
    execution_owner: str
    execution_route: Literal['legacy','held']
    ownership_generation: int
    inspect_url: str
    ownership_mode: Literal["legacy_observed"]
    mapping_quality: Literal["status_only"]
    cleanup_state: Literal["not_recorded"]
    observed_at: str
    source_created_at: str | None
    content_revision: int
    state_version: int


class ActivityBasis(Input):
    observed_before: str
    membership: Literal["current_at_request"]
    order: Literal["observed_at_desc_id_desc"]


class ActivityPage(Input):
    items: list[ActivityItem]
    count: Count
    next_cursor: str | None
    basis: ActivityBasis


class SourceItem(Input):
    key: str
    label: str
    kind: str
    execution_owner: str
    inspect_url: str
    ownership_mode: Literal["legacy_observed"]


class SourcesOutput(Input):
    items: list[SourceItem]


def source_catalogue(connection, principal, target, payload: Empty) -> dict:
    return {"items":[{"key":source.key,"label":source.label,"kind":source.kind,"execution_owner":source.owner,"inspect_url":source.inspect_url,"ownership_mode":"legacy_observed"}
                     for source in sources() if source.available(principal)]}


def source_statements(principal, cut, selected: str | None = None) -> tuple[str,list]:
    available = [source for source in sources() if source.available(principal)]
    if selected is not None:
        available = [source for source in available if source.key==selected]
        if not available:
            raise ApplicationError("activity_source_unavailable","This activity source is not available to the current credential.",404)
    statements,parameters = [],[]
    for source in available:
        statement,values = source.statement(principal,cut)
        statements.append(statement)
        parameters.extend(values)
    if not statements:
        raise ApplicationError("activity_source_unavailable","No activity source is available to the current credential.",403)
    return " UNION ALL ".join(statements),parameters


def list_activity(connection, principal, target, payload: ActivityInput) -> dict:
    cut,after,scope = page_basis(connection,payload.cursor,["activity.v1",principal.actor,sorted(principal.scopes),payload.kind,payload.source,payload.state])
    statement,parameters = source_statements(principal,cut,payload.source)
    filters = []
    if payload.kind:
        filters.append("kind=%s")
        parameters.append(payload.kind)
    if payload.state:
        filters.append("state=%s")
        parameters.append(payload.state)
    where = " WHERE "+" AND ".join(filters) if filters else ""
    after_sql = " WHERE (observed_at,id)<(%s,%s::uuid)" if after else ""
    if after:
        parameters.extend(after)
    parameters.append(payload.limit+1)
    row = connection.execute(
        f"""WITH source AS NOT MATERIALIZED ({statement}),visible AS NOT MATERIALIZED (SELECT * FROM source {where}),
            page AS (SELECT * FROM visible {after_sql} ORDER BY observed_at DESC,id DESC LIMIT %s),
            counted AS (SELECT count(*) AS count FROM (SELECT id FROM visible LIMIT 1001) sample)
            SELECT COALESCE((SELECT jsonb_agg(to_jsonb(page) ORDER BY observed_at DESC,id DESC) FROM page),'[]'::jsonb) AS items,
                   counted.count FROM counted""",parameters,
    ).fetchone()
    items = row["items"][:payload.limit]
    cursor = page_cursor(scope,cut,items[-1]["observed_at"],items[-1]["id"]) if len(row["items"])>payload.limit else None
    return {"items":items,"count":{"count":min(row["count"],1000),"more":row["count"]>1000,"limit":1000},"next_cursor":cursor,
            "basis":{"observed_before":cut.isoformat(),"membership":"current_at_request","order":"observed_at_desc_id_desc"}}


def activity_detail(connection, principal, target, payload: Empty) -> dict:
    try:
        resource_id = str(uuid.UUID(target))
    except (ValueError,TypeError,AttributeError) as error:
        raise ApplicationError("invalid_target","Supply an activity resource UUID.",422) from error
    cut = connection.execute("SELECT clock_timestamp() AS cut").fetchone()["cut"]
    statement,parameters = source_statements(principal,cut)
    row = connection.execute(f"SELECT to_jsonb(activity) AS item FROM ({statement}) activity WHERE id=%s::uuid",(*parameters,resource_id)).fetchone()
    if row is None:
        raise ApplicationError("activity_unavailable","This activity is not available to the current credential.",404)
    return row["item"]


def queries() -> tuple[Query,...]:
    return (
        Query("activity.sources.v1",None,Empty,SourcesOutput,source_catalogue),
        Query("activity.list.v1","activity:read",ActivityInput,ActivityPage,list_activity),
        Query("activity.detail.v1","activity:read",Empty,ActivityItem,activity_detail,True),
        Query("activity.legacy.inspect.v1","activity:inspect_legacy",ActivityInput,ActivityPage,list_activity),
        Query("activity.legacy.detail.v1","activity:inspect_legacy",Empty,ActivityItem,activity_detail,True),
    )
