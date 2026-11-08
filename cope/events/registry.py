from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from cope.application.errors import ApplicationError
from cope.db.events import EventRecord, get_event


EventProvisioner = Callable[[Any], int]
EventPayloadBuilder = Callable[[Any, EventRecord], dict[str, Any]]
EventApiRegistrar = Callable[[Any], None]


@dataclass(frozen=True, slots=True)
class EventDeletion:
    delete_tournament_history: bool = False
    opening_suite_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class EventModule:
    key: str
    label: str
    version: int
    provision: EventProvisioner
    public_payload: EventPayloadBuilder | None = None
    admin_payload: EventPayloadBuilder | None = None
    register_api: EventApiRegistrar | None = None
    reset: Callable[[Any, int], object] | None = None
    deletion_resources: Callable[[Any, EventRecord], EventDeletion] | None = None
    public_arena_statuses: frozenset[str] | None = None
    closed_arena_payload: Callable[[dict[str, Any]], dict[str, Any]] | None = None


_modules: dict[str, EventModule] = {}


def register_event_module(module: EventModule) -> EventModule:
    key = module.key.strip()
    if key != module.key or not key or not key.replace("-", "").replace("_", "").isalnum():
        raise ValueError("event module keys may only contain letters, numbers, hyphens, and underscores")
    if not module.label.strip():
        raise ValueError("event module label is required")
    if module.version < 1:
        raise ValueError("event module version must be positive")
    if key in _modules:
        raise ValueError(f"event module {key!r} is already registered")
    _modules[key] = module
    return module


def get_event_module(key: str) -> EventModule | None:
    return _modules.get(key)


def event_modules() -> Mapping[str, EventModule]:
    return MappingProxyType(_modules)


def event_handler(event: EventRecord) -> EventModule | None:
    module = get_event_module(event.handler_key)
    return module if module is not None and module.version == event.handler_version else None


def event_arena_is_public(event: EventRecord) -> bool:
    module = get_event_module(event.handler_key)
    return module is None or module.public_arena_statuses is None or event.status in module.public_arena_statuses


def provision_event_module(connection: Any, key: str) -> EventRecord:
    module = get_event_module(key)
    if module is None:
        raise ApplicationError("event_module_not_found", "Event module not found.", 404)
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))", (f"cope:event-module:{key}",))
    existing = connection.execute("SELECT id FROM events WHERE handler_key = ? LIMIT 1", (key,)).fetchone()
    if existing is not None:
        raise ApplicationError("event_already_provisioned", f"{module.label} has already been created.")
    event_id = module.provision(connection)
    event = get_event(connection, event_id)
    if event is None:
        raise RuntimeError("event provisioner did not create an event")
    if event.handler_key != module.key:
        raise RuntimeError("event provisioner created an event for a different handler")
    if event.handler_version != module.version:
        raise RuntimeError("event provisioner created an event with a different handler version")
    return event


def event_extension_payload(
    connection: Any,
    event: EventRecord,
    *,
    admin: bool,
    arena_available: bool = True,
) -> dict[str, Any]:
    module = event_handler(event)
    if module is None:
        return {}
    builder = module.admin_payload if admin else module.public_payload
    payload = {} if builder is None else builder(connection, event)
    if not admin and not arena_available and module.closed_arena_payload is not None:
        return module.closed_arena_payload(payload)
    return payload


def register_event_api_routes(app: Any) -> None:
    for module in _modules.values():
        if module.register_api is not None:
            module.register_api(app)
