from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from cope.application.contracts import Input, Query
from cope.application.errors import ApplicationError

from .application import describe_copy
from .bindings import BINDINGS, compile_binding
from .definition import FormDefinition
from .storage import form_permission, get_form, identity, public_form_view, resolve_copy, response_view


class Empty(Input):
    pass


class PublicInput(Input):
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class PageInput(Input):
    limit: int = Field(default=30, strict=True, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=4096)
    form_id: str | None = Field(default=None, min_length=1, max_length=100)


class DataOutput(Input):
    model_config = {"extra": "allow"}


class PageOutput(Input):
    items: list[dict[str, Any]]
    total: int
    next_cursor: str | None
    basis: dict


class DateTimeInput(Input):
    local_date: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    local_time: str = Field(pattern=r"^[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?$")
    timezone: str = Field(min_length=1, max_length=100)


class DateTimeChoice(Input):
    value: str
    label: str
    utc: str


class DateTimeOutput(Input):
    timezone: str
    options: list[DateTimeChoice]


class BindingArgumentOutput(Input):
    name: str
    label: str
    kinds: list[str]
    required: bool


class BindingOutput(Input):
    key: str
    label: str
    policy_revision: str
    permits_anonymous: bool
    arguments: list[BindingArgumentOutput]


class BindingsOutput(Input):
    items: list[BindingOutput]


class CheckDefinitionInput(Input):
    document: dict


class CheckDefinitionOutput(Input):
    definition: dict


def bindings_catalogue(connection, principal, target, payload: Empty) -> dict:
    return {"items": [{"key": binding.key, "label": binding.key.rsplit(".", 1)[0].replace("_", " ").title(),
                       "policy_revision": binding.policy_revision, "permits_anonymous": binding.permits_anonymous,
                       "arguments": [{"name": argument.name, "label": argument.name.replace("_", " ").title(),
                                      "kinds": list(argument.kinds), "required": argument.required} for argument in binding.arguments]}
                      for binding in BINDINGS.values()]}


def check_definition(connection, principal, target, payload: CheckDefinitionInput) -> dict:
    from pydantic import ValidationError

    try:
        definition = FormDefinition.model_validate(payload.document)
    except ValidationError as exc:
        raise ApplicationError("invalid_form_definition", "Resolve the highlighted form settings.", 422,
                               fields=[{"path": list(error["loc"]), "code": error["type"], "message": error["msg"]} for error in exc.errors()]) from exc
    compile_binding(definition)
    return {"definition": definition.model_dump(mode="json")}


def page_basis(connection, principal, payload: PageInput, namespace: str) -> tuple[datetime, tuple | None, str]:
    scope = hashlib.sha256(json.dumps([namespace, principal.actor, sorted(principal.scopes), payload.form_id], separators=(",", ":")).encode()).hexdigest()
    if payload.cursor is None:
        return connection.execute("SELECT clock_timestamp() AS cut").fetchone()["cut"], None, scope
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.cursor.encode()))
        if set(data) != {"scope", "cut", "created", "id"} or data["scope"] != scope:
            raise ValueError()
        cut, created = datetime.fromisoformat(data["cut"]), datetime.fromisoformat(data["created"])
        if cut.tzinfo is None or created.tzinfo is None or created > cut:
            raise ValueError()
        return cut, (created, identity(data["id"])), scope
    except (ValueError, TypeError, KeyError, UnicodeError, ApplicationError) as exc:
        raise ApplicationError("invalid_cursor", "Refresh this list; its continuation does not match the current query and credential.", 422) from exc


def finish_page(items: list[dict], total: int, cut: datetime, scope: str, limit: int) -> dict:
    more = len(items) > limit
    items = items[:limit]
    cursor = None
    if more:
        last = items[-1]
        cursor = base64.urlsafe_b64encode(json.dumps({"scope": scope, "cut": cut.isoformat(), "created": last["created_at"], "id": last["id"]}, separators=(",", ":")).encode()).decode()
    return {"items": items, "total": total, "next_cursor": cursor,
            "basis": {"created_before": cut.isoformat(), "membership": "current_at_request", "order": "created_at_desc_id_desc"}}


def list_forms(connection, principal, target, payload: PageInput) -> dict:
    if payload.form_id is not None:
        raise ApplicationError("invalid_query_input", "The form catalogue does not accept a parent form filter.", 422)
    cut, after, scope = page_basis(connection, principal, payload, "forms")
    where = "" if after is None else "WHERE (created_at, id) < (%s, %s::uuid)"
    parameters = [cut, "*" in principal.scopes, principal.actor, principal.actor]
    if after:
        parameters.extend(after)
    parameters.append(payload.limit + 1)
    row = connection.execute(
        f"""WITH visible AS MATERIALIZED (
          SELECT f.id, f.slug, f.label, f.closed, f.published_release, f.created_at,
                 o.content_revision, o.state_version FROM form_definitions f
          JOIN application_objects o ON o.id = f.id
          WHERE f.created_at <= %s AND (%s OR f.owner_actor = %s OR EXISTS (
            SELECT 1 FROM form_permissions p WHERE p.form_id = f.id AND p.actor = %s AND p.permission IN ('edit', 'review')))
        ), page AS (SELECT * FROM visible {where} ORDER BY created_at DESC, id DESC LIMIT %s)
        SELECT COALESCE((SELECT jsonb_agg(to_jsonb(page) ORDER BY created_at DESC, id DESC) FROM page), '[]'::jsonb) AS items,
               (SELECT count(*) FROM visible) AS total""", parameters,
    ).fetchone()
    return finish_page(row["items"], row["total"], cut, scope, payload.limit)


def list_responses(connection, principal, target, payload: PageInput) -> dict:
    cut, after, scope = page_basis(connection, principal, payload, "responses")
    parent = "" if payload.form_id is None else "AND r.form_id = %s"
    where = "" if after is None else "WHERE (created_at, id) < (%s, %s::uuid)"
    parameters = [cut, principal.actor, "*" in principal.scopes, principal.actor, principal.actor]
    if payload.form_id is not None:
        parameters.append(identity(payload.form_id))
    if after:
        parameters.extend(after)
    parameters.append(payload.limit + 1)
    row = connection.execute(
        f"""WITH visible AS MATERIALIZED (
          SELECT r.id, r.form_id, f.slug, release.definition->>'title' AS form_title,
                 r.current_revision AS revision, r.created_at,
                 CASE WHEN r.withdrawn_at IS NOT NULL THEN 'withdrawn' ELSE COALESCE(review.decision, 'awaiting_review') END AS state
          FROM form_responses r JOIN form_definitions f ON f.id = r.form_id
          JOIN form_releases release ON release.id = r.release_id
          JOIN form_response_revisions v ON v.response_id = r.id AND v.revision = r.current_revision
          LEFT JOIN LATERAL (SELECT decision FROM form_reviews WHERE response_id = r.id AND response_revision = r.current_revision
            AND subject_digest = v.subject_digest ORDER BY created_at DESC, id DESC LIMIT 1) review ON TRUE
          WHERE r.created_at <= %s AND (r.owner_actor = %s OR %s OR f.owner_actor = %s OR EXISTS (
            SELECT 1 FROM form_permissions p WHERE p.form_id = f.id AND p.actor = %s AND p.permission IN ('responses','review'))) {parent}
        ), page AS (SELECT * FROM visible {where} ORDER BY created_at DESC, id DESC LIMIT %s)
        SELECT COALESCE((SELECT jsonb_agg(to_jsonb(page) ORDER BY created_at DESC, id DESC) FROM page), '[]'::jsonb) AS items,
               (SELECT count(*) FROM visible) AS total""", parameters,
    ).fetchone()
    return finish_page(row["items"], row["total"], cut, scope, payload.limit)


def form_detail(connection, principal, target, payload: Empty) -> dict:
    form = get_form(connection, target)
    form_permission(connection, principal, form, "edit")
    versions = connection.execute("SELECT content_revision, state_version FROM application_objects WHERE id = %s FOR SHARE", (form["id"],)).fetchone()
    releases = connection.execute(
        """SELECT r.id, r.number, r.definition, r.created_at, a.accepting, a.valid_until FROM form_releases r
           JOIN form_release_acceptance a ON a.release_id = r.id WHERE r.form_id = %s ORDER BY r.number DESC LIMIT 100""", (form["id"],),
    ).fetchall()
    copies = connection.execute(
        """SELECT c.id, c.current_revision, c.archived, c.updated_at, o.state_version FROM form_working_copies c
           JOIN application_objects o ON o.id = c.id WHERE c.form_id = %s AND c.owner_actor = %s
           ORDER BY c.updated_at DESC LIMIT 200""", (form["id"], principal.actor),
    ).fetchall()
    return {"id": str(form["id"]), "slug": form["slug"], "label": form["label"], "closed": form["closed"],
            "published_release": str(form["published_release"]) if form["published_release"] else None, **dict(versions),
            "releases": [{**dict(row), "id": str(row["id"]), "created_at": row["created_at"].isoformat(), "valid_until": row["valid_until"].isoformat() if row["valid_until"] else None} for row in releases],
            "copies": [{**dict(row), "id": str(row["id"]), "updated_at": row["updated_at"].isoformat()} for row in copies]}


def draft_detail(connection, principal, target, payload: Empty) -> dict:
    resolved = resolve_copy(connection, principal, target, lock=False)
    return {**describe_copy(connection, principal, resolved), "archived": resolved.data["archived"]}


def public_detail(connection, principal, target, payload: PublicInput) -> dict:
    return public_form_view(connection, payload.slug, None if principal.actor == "public" else principal)


def response_detail(connection, principal, target, payload: Empty) -> dict:
    return response_view(connection, principal, target)


def datetime_options(connection, principal, target, payload: DateTimeInput) -> dict:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        local = datetime.fromisoformat(payload.local_date + "T" + payload.local_time)
        zone = ZoneInfo(payload.timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ApplicationError("invalid_local_time", "Choose a valid local date, time and timezone.", 422) from exc
    options = {}
    for fold in (0, 1):
        aware = local.replace(tzinfo=zone, fold=fold)
        utc = aware.astimezone(UTC)
        if utc.astimezone(zone).replace(tzinfo=None) != local:
            continue
        seconds = int(aware.utcoffset().total_seconds())
        hours, rest = divmod(abs(seconds), 3600)
        minutes, seconds_part = divmod(rest, 60)
        offset = ("+" if seconds >= 0 else "-") + f"{hours:02d}:{minutes:02d}" + (f":{seconds_part:02d}" if seconds_part else "")
        options[aware.isoformat()] = {"value": aware.isoformat(), "utc": utc.isoformat(), "label": f"{payload.local_time} (UTC{offset})"}
    return {"timezone": payload.timezone, "options": sorted(options.values(), key=lambda option: option["utc"])}


def queries() -> tuple[Query, ...]:
    from .permissions import queries as permission_queries

    return (
        Query("forms.bindings.v1", "forms:write", Empty, BindingsOutput, bindings_catalogue),
        Query("forms.definition.check.v1", "forms:write", CheckDefinitionInput, CheckDefinitionOutput, check_definition),
        Query("forms.list.v1", "forms:read", PageInput, PageOutput, list_forms),
        Query("forms.responses.list.v1", "forms:responses:read", PageInput, PageOutput, list_responses),
        Query("forms.detail.v1", "forms:write", Empty, DataOutput, form_detail, True),
        Query("forms.drafts.detail.v1", "forms:write", Empty, DataOutput, draft_detail, True),
        Query("forms.responses.detail.v1", "forms:responses:read", Empty, DataOutput, response_detail, True),
        Query("forms.public.v1", None, PublicInput, DataOutput, public_detail),
        Query("forms.datetime_options.v1", None, DateTimeInput, DateTimeOutput, datetime_options),
    ) + permission_queries()
