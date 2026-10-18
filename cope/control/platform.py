from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import inspect
import json
import os
import re
from dataclasses import dataclass
from types import SimpleNamespace
from urllib.parse import quote

import httpx
from fastapi.routing import APIRoute
from pydantic import Field, create_model
from starlette.routing import compile_path

from .operations import Plan, Principal, Step, audit, create_operation
from .registry import Action, Input, _actions


_applications = {}
_routes = {}


class Upload(Input):
    field: str = Field(min_length=1, max_length=100)
    filename: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(max_length=2_800_000)


@dataclass(frozen=True)
class PlatformRoute:
    action: Action
    method: str
    path: str
    path_fields: tuple[str, ...]
    query_fields: tuple[str, ...]
    multipart: bool


class BorrowedConnection:
    def __init__(self, connection):
        self.connection = connection

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def _scope(path, method):
    if method == "GET":
        return "*" if "/settings/access-tokens/" in path else "operations:read"
    if "/settings/access-tokens/" in path:
        return "*"
    if path.startswith("/api/admin/git-hosts"):
        return "*"
    segments = path.split("/")
    resource = segments[3] if path.startswith("/api/admin/") else segments[2]
    domain = {
        "workers": "workers", "benchmarkers": "workers", "benchmark-jobs": "engines", "benchmark-hardware": "engines",
        "engines": "engines", "engine-versions": "engines", "git-hosts": "settings", "engine-dockerfiles": "engines",
        "events": "events", "event-modules": "events", "ratings": "ratings", "rating-lists": "ratings",
        "badges": "badges", "chat": "chat", "tournaments": "tournaments", "openings": "openings",
        "settings": "settings", "deployments": "deployments", "updates": "deployments", "dockerfile-pulls": "recipes",
        "environment-exports": "environments", "tools": "tools",
    }.get(resource, "platform")
    if path.endswith("/chat"):
        domain = "chat"
    if "environment-clone" in path or "environment-export" in path:
        domain = "environments"
    if "/tools/tournament-creator" in path:
        domain = "tournaments"
    if "/tools/invalidate-engine-games" in path or path.endswith("/commit-results"):
        domain = "ratings"
    return f"{domain}:write"


def register_platform_actions(app):
    _applications[str(app.state.db_path)] = app
    from .registry import catalog

    catalog()
    for route in tuple(app.routes):
        if not isinstance(route, APIRoute):
            continue
        eligible = route.path.startswith("/api/admin/") or re.match(r"^/api/(engines|ratings|tournaments|events)(/|$)", route.path)
        if not eligible or route.path.startswith("/api/admin/control"):
            continue
        for method in sorted(route.methods & {"GET", "POST", "PUT", "PATCH", "DELETE"}):
            name = "platform." + route.name + "." + method.lower()
            if name in _routes and _routes[name].path != route.path:
                name += "." + hashlib.sha256(route.path.encode()).hexdigest()[:8]
            fields = {}
            for field in (*route.dependant.path_params, *route.dependant.query_params):
                fields[field.name] = (field.field_info.annotation, copy.deepcopy(field.field_info))
            multipart = False
            if route.body_field is not None:
                field = route.body_field
                media_type = getattr(field.field_info, "media_type", "application/json")
                multipart = media_type in {"multipart/form-data", "application/x-www-form-urlencoded"}
                if not multipart:
                    fields["body"] = (field.field_info.annotation, Field(default=... if field.field_info.is_required() else None))
            if method != "GET" and (route.body_field is None or multipart):
                fields["form_values"] = (dict[str, str | list[str]], Field(default_factory=dict))
                fields["uploads"] = (list[Upload], Field(default_factory=list, max_length=20))
            model = create_model("Control_" + route.name + "_" + method, __base__=Input, **fields)
            read_only = method == "GET"
            title = route.name.replace("_", " ").removeprefix("admin ").capitalize()
            registered = Action(name, title, _scope(route.path, method), model, None, False, read_only)
            spec = PlatformRoute(registered, method, route.path, tuple(field.name for field in route.dependant.path_params), tuple(field.name for field in route.dependant.query_params), multipart)
            handler = _handler(spec)
            registered = Action(name, title, registered.scope, model, handler, False, read_only)
            _actions[name] = registered
            _routes[name] = PlatformRoute(registered, method, spec.path, spec.path_fields, spec.query_fields, multipart)


def _handler(spec):
    def handle(context, payload):
        return asyncio.run(_invoke(context, spec, payload))
    return handle


async def _invoke(context, spec, payload):
    app = _applications[context.database_url]
    values = payload.model_dump(mode="json", by_alias=False, exclude_none=True)
    path = spec.path
    for name in spec.path_fields:
        path = re.sub(r"\{" + re.escape(name) + r"(?::[^}]+)?\}", quote(str(values[name]), safe=""), path)
    queries = []
    for name in spec.query_fields:
        if name in values:
            value = values[name]
            queries.extend((name, str(item).lower() if isinstance(item, bool) else str(item)) for item in (value if isinstance(value, list) else [value]))
    identity = context.principal if hasattr(context, "principal") else Principal(context.actor, tuple(json.loads(context.operation["scopes"])), context.operation["token_id"])
    identity.require(spec.action.scope)
    connection = BorrowedConnection(context.connection)
    context.connection.execute("SET LOCAL idle_in_transaction_session_timeout = '180s'")

    async def internal_app(scope, receive, send):
        pattern, _, _ = compile_path(spec.path)
        if scope["method"] != spec.method or pattern.fullmatch(scope["path"]) is None:
            raise PermissionError("Tool parameters must remain inside their registered API route")
        scope["cope.control.internal"] = {"principal": identity, "connection": connection, "action": spec.action.name, "route_path": spec.path}
        count = 0
        async def limited_send(message):
            nonlocal count
            if message["type"] == "http.response.body":
                count += len(message.get("body", b""))
                if count > 2 * 1024 * 1024:
                    raise ValueError(f"Result is too large for a tool response; use the Cope interface at {path}")
            await send(message)
        await app(scope, receive, limited_send)

    request_kwargs = {}
    if "body" in values:
        request_kwargs["json"] = values["body"]
    elif values.get("form_values") or values.get("uploads"):
        parts = []
        for key, value in values.get("form_values", {}).items():
            parts.extend((key, (None, str(item))) for item in (value if isinstance(value, list) else [value]))
        for upload in values.get("uploads", []):
            content = base64.b64decode(upload["content_base64"], validate=True)
            if len(content) > 2 * 1024 * 1024:
                raise ValueError("Uploaded files cannot exceed two MiB")
            parts.append((upload["field"], (upload["filename"], content, "application/octet-stream")))
        request_kwargs["files"] = parts
    origin = "https://" + (os.environ.get("COPE_DOMAIN") or "cope-control.internal")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=internal_app), base_url=origin, follow_redirects=False) as client:
        response = await client.request(spec.method, path, params=queries, **request_kwargs)
    if response.status_code >= 300:
        try:
            detail = response.json().get("detail", response.text)
        except (ValueError, AttributeError):
            detail = response.text
        raise ValueError(f"{spec.action.title} returned HTTP {response.status_code}: {str(detail)[:3000]}")
    if response.status_code == 204:
        return {"status": 204}
    if "application/json" in response.headers.get("content-type", ""):
        result = response.json()
    else:
        result = {"content": response.text, "content_type": response.headers.get("content-type")}
    return {"status": response.status_code, "data": result}


def execute_read(connection, identity, database_url, name, payload):
    spec = _routes[name]
    identity.require(spec.action.scope)
    context = SimpleNamespace(connection=connection, principal=identity, actor=identity.actor, database_url=database_url)
    result = spec.action.handler(context, payload)
    audit(connection, identity.actor, "action.read", {"action": name})
    return result


def install_platform_tools(server, invoke, database_url):
    from mcp.server.fastmcp import Context
    from mcp.types import ToolAnnotations

    for name, spec in _routes.items():
        def build_tool(name, spec):
            async def call(payload, ctx, idempotency_key=""):
                if spec.action.read_only:
                    return await asyncio.to_thread(invoke, ctx, lambda connection, identity: execute_read(connection, identity, database_url, name, payload))
                if not idempotency_key or len(idempotency_key) > 160:
                    raise ValueError("Supply an idempotency key of at most 160 characters for this write")
                plan = Plan(title=spec.action.title, steps=[Step(key="apply", action=name, input=payload.model_dump(mode="json"))])
                return await asyncio.to_thread(invoke, ctx, create_operation, plan, idempotency_key)
            parameters = [inspect.Parameter("payload", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=spec.action.input_model), inspect.Parameter("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Context)]
            if not spec.action.read_only:
                parameters.append(inspect.Parameter("idempotency_key", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str))
            call.__signature__ = inspect.Signature(parameters, return_annotation=dict)
            call.__annotations__ = {"payload": spec.action.input_model, "ctx": Context, "return": dict}
            if not spec.action.read_only:
                call.__annotations__["idempotency_key"] = str
            return call
        tool_name = "cope_" + name.removeprefix("platform.").replace(".", "_")
        if len(tool_name) > 64:
            tool_name = tool_name[:53] + "_" + hashlib.sha256(tool_name.encode()).hexdigest()[:10]
        server.add_tool(build_tool(name, spec), name=tool_name, title=spec.action.title,
            description=f"{spec.action.title}. {spec.method} {spec.path}. " + ("Returns current Cope data." if spec.action.read_only else "Creates a durable operation; inspect it with cope_get_operation. Reuse the same idempotency key when retrying."),
            annotations=ToolAnnotations(readOnlyHint=spec.action.read_only, destructiveHint=not spec.action.read_only,
                openWorldHint=any(value in spec.path for value in ("repositories", "git-hosts", "environment-clone", "deployments", "updates", "dockerfile-pulls"))))
