from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from importlib import import_module

from pydantic import BaseModel, ConfigDict


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Waiting(Exception):
    def __init__(self, detail: str, *, retry_after: int = 30):
        super().__init__(detail)
        self.retry_after = retry_after


@dataclass(frozen=True)
class Action:
    name: str
    title: str
    scope: str
    input_model: type[BaseModel]
    handler: Callable
    external: bool = False
    read_only: bool = False


_actions: dict[str, Action] = {}


def action(name: str, title: str, scope: str, model: type[BaseModel], *, external=False, read_only=False):
    def register(handler):
        if name in _actions:
            raise ValueError(f"duplicate control action: {name}")
        _actions[name] = Action(name, title, scope, model, handler, external, read_only)
        return handler
    return register


def catalog() -> dict[str, Action]:
    import_module("cope.control.intake")

    return dict(_actions)


def describe_actions() -> list[dict[str, Any]]:
    return [
        {"name": item.name, "title": item.title, "scope": item.scope,
         "input_schema": item.input_model.model_json_schema(), "external": item.external, "read_only": item.read_only}
        for item in catalog().values()
    ]
