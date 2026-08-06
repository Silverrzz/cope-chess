from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from cope.db.events import EventRecord, get_event


EventProvisioner = Callable[[Any], int]
EventPayloadBuilder = Callable[[Any, EventRecord], dict[str, Any]]
EventApiRegistrar = Callable[[Any], None]


@dataclass(frozen=True, slots=True)
class EventModule:
    key: str
    label: str
    version: int
    provision: EventProvisioner
    public_payload: EventPayloadBuilder | None = None
    admin_payload: EventPayloadBuilder | None = None
    register_api: EventApiRegistrar | None = None


_modules: dict[str, EventModule] = {}


def register_event_module(module: EventModule) -> EventModule:
    key = module.key.strip()
    if not key or not key.replace("-", "").replace("_", "").isalnum():
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


def provision_event_module(connection: Any, key: str) -> EventRecord:
    module = get_event_module(key)
    if module is None:
        raise ValueError(f"event module {key!r} is not registered")
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
) -> dict[str, Any]:
    module = get_event_module(event.handler_key)
    if module is None or module.version != event.handler_version:
        return {}
    builder = module.admin_payload if admin else module.public_payload
    return {} if builder is None else builder(connection, event)


def register_event_api_routes(app: Any) -> None:
    for module in _modules.values():
        if module.register_api is not None:
            module.register_api(app)
