from __future__ import annotations

from cope.application.contracts import Input, Query
from cope.db.connection import LegacyTransaction

from .views import event_catalogue


class CatalogueInput(Input):
    pass


class CatalogueOutput(Input):
    events: list[dict]
    statuses: list[str]
    registered_modules: list[dict]


def catalogue(connection, principal, target, payload: CatalogueInput) -> dict:
    return event_catalogue(LegacyTransaction(connection))


def queries() -> tuple[Query, ...]:
    return (Query("events.catalogue.v1", "operations:read", CatalogueInput, CatalogueOutput, catalogue),)
