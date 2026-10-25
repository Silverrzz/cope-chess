from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from .identity import Principal


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CommandRequest(Input):
    command: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.]*\.v[1-9][0-9]*$")
    target: str | None = Field(default=None, min_length=1, max_length=100)
    expected_version: int | None = Field(default=None, strict=True, gt=0)
    idempotency_key: str = Field(min_length=1, max_length=160)
    input: dict[str, Any]


class CommandReceipt(Input):
    receipt_id: str
    status: Literal["completed"]
    command: str
    object_id: str
    legacy_id: int | None = None
    version_channel: str | None = None
    basis_version: int | None = None
    version: int | None = None
    output: dict[str, Any]


class QueryRequest(Input):
    query: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.]*\.v[1-9][0-9]*$")
    target: str | None = Field(default=None, min_length=1, max_length=100)
    input: dict[str, Any] = Field(default_factory=dict)


class QueryResult(Input):
    query: str
    data: dict[str, Any]


@dataclass(frozen=True)
class Target:
    object_id: str
    version: int | None
    legacy_id: int | None = None
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Outcome:
    object_id: str
    version: int | None
    output: dict
    legacy_id: int | None = None


@dataclass(frozen=True)
class CommandContext:
    connection: Any
    principal: Principal
    target: Target | None
    request: CommandRequest


@dataclass(frozen=True)
class Command:
    name: str
    type_key: str
    scope: str
    version_channel: str | None
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    resolve: Callable
    handle: Callable
    describe: Callable
    project: Callable
    requires_target: bool = True
    requires_version: bool = True


@dataclass(frozen=True)
class Query:
    name: str
    scope: str | None
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handle: Callable
    requires_target: bool = False
