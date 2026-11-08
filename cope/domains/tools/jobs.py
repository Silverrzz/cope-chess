from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from cope.application.errors import ApplicationError
from cope.db import cancel_tool_job, create_tool_job, get_tool_job

from .catalogue import UCI_INSPECTION


class InspectOptions(BaseModel):
    engine_ids: list[int] = Field(min_length=1, max_length=500)
    option_name: str = Field(min_length=1, max_length=200)

    @field_validator("engine_ids")
    @classmethod
    def validate_engine_ids(cls, value: list[int]) -> list[int]:
        if any(engine_id <= 0 for engine_id in value):
            raise ValueError("engine ids must be positive")
        unique = list(dict.fromkeys(value))
        if len(unique) != len(value):
            raise ValueError("engine ids must be unique")
        return unique

    @field_validator("option_name")
    @classmethod
    def validate_option_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned or any(ord(character) < 32 for character in cleaned):
            raise ValueError("enter a valid UCI option name")
        return cleaned


def inspect_options(connection, payload: InspectOptions):
    try:
        return create_tool_job(
            connection,
            tool_name=UCI_INSPECTION.key,
            input_data={"option_name": payload.option_name},
            engine_version_ids=payload.engine_ids,
        )
    except ValueError as exc:
        raise ApplicationError("invalid_tool_input", str(exc), 422) from exc


def cancel_job(connection, job_id: int) -> None:
    try:
        if cancel_tool_job(connection, job_id):
            return
    except ValueError as exc:
        raise ApplicationError("tool_job_route_changed", str(exc)) from exc
    if get_tool_job(connection, job_id) is None:
        raise ApplicationError("tool_job_not_found", "Tool job not found.", 404)
    raise ApplicationError("tool_job_not_cancellable", "Only queued or running tool jobs can be cancelled.")
