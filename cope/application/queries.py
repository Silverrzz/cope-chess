from __future__ import annotations

from pydantic import ValidationError

from .contracts import QueryRequest, QueryResult
from .errors import ApplicationError
from .identity import Principal, current_principal


def query_catalog() -> dict:
    from cope.domains.forms.queries import queries as form_queries
    from .delivery_queries import queries as delivery_queries
    from cope.domains.activity.queries import queries as activity_queries

    result = {}
    for provider in (form_queries(),delivery_queries(),activity_queries()):
        for query in provider:
            if query.name in result:
                raise RuntimeError(f"Duplicate application query: {query.name}")
            result[query.name] = query
    return result


QUERIES = query_catalog()


def list_queries(principal: Principal) -> list[dict]:
    return [{"name": item.name, "scope": item.scope, "requires_target": item.requires_target,
             "input_schema": item.input_model.model_json_schema(), "output_schema": item.output_model.model_json_schema()}
            for item in QUERIES.values() if item.scope is None or "*" in principal.scopes or item.scope in principal.scopes]


def execute_query(connection, principal: Principal, request: QueryRequest) -> dict:
    query = QUERIES.get(request.query)
    if query is None:
        raise ApplicationError("unknown_query", "Unknown application query.", 404)
    principal = current_principal(connection, principal)
    if query.scope is not None:
        principal.require(query.scope)
    if query.requires_target != (request.target is not None):
        raise ApplicationError("invalid_query_target", "Check this query's target contract.", 422)
    try:
        payload = query.input_model.model_validate(request.input)
    except ValidationError as exc:
        fields = [{"path": list(error["loc"]), "code": error["type"], "message": error["msg"]}
                  for error in exc.errors(include_input=False, include_context=False, include_url=False)]
        raise ApplicationError("invalid_query_input", "Check the query fields.", 422, fields=fields) from exc
    output = query.output_model.model_validate(query.handle(connection, principal, request.target, payload))
    return QueryResult(query=query.name, data=output.model_dump(mode="json")).model_dump(mode="json")
