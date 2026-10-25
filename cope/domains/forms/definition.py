from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from cope.application.errors import ApplicationError


class DefinitionValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Choice(DefinitionValue):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    label: str = Field(min_length=1, max_length=300)


class Condition(DefinitionValue):
    field: str = Field(min_length=1, max_length=100)
    equals: str | StrictBool


class FormField(DefinitionValue):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    kind: Literal["text", "long_text", "email", "integer", "decimal", "date", "time", "datetime",
                  "choice", "multiple_choice", "checkbox", "group", "attachment", "reference"]
    label: str = Field(min_length=1, max_length=300)
    help: str = Field(default="", max_length=4000)
    required: bool = False
    nullable: bool = False
    min_length: int = Field(default=0, ge=0, le=65536)
    max_length: int = Field(default=4096, ge=1, le=65536)
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    precision: int = Field(default=6, ge=0, le=12)
    unit: str = Field(default="", max_length=80)
    timezone: str | None = Field(default=None, max_length=100)
    choices: tuple[Choice, ...] = Field(default=(), max_length=500)
    when: Condition | None = None
    fields: tuple[FormField, ...] = Field(default=(), max_length=100)
    min_items: int = Field(default=0, ge=0, le=100)
    max_items: int = Field(default=20, ge=1, le=100)
    reference_type: str | None = Field(default=None, max_length=100, pattern=r"^[a-z][a-z0-9_.-]*$")
    accepted_content_types: tuple[str, ...] = Field(default=(), max_length=30)
    max_bytes: int = Field(default=10485760, ge=1, le=1073741824)


class FormDefinition(DefinitionValue):
    schema_version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=12000)
    fields: tuple[FormField, ...] = Field(min_length=1, max_length=100)
    binding: str = Field(default="feedback.v1", max_length=100)
    mapping: dict[str, str] = Field(default_factory=dict, max_length=100)
    audience: Literal["public", "authenticated", "invited"] = "authenticated"
    allow_amendments: bool = True
    max_payload_bytes: int = Field(default=1048576, ge=1024, le=1048576)
    max_rows: int = Field(default=200, ge=1, le=1000)
    submissions_per_hour: int = Field(default=20, ge=1, le=1000)


def compile_definition(definition: FormDefinition) -> FormDefinition:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    errors = []
    count = 0

    def fail(path: tuple[str, ...], code: str, message: str):
        errors.append({"path": list(path), "code": code, "message": message})

    def scope(fields: tuple[FormField, ...], parent: tuple[str, ...], depth: int):
        nonlocal count
        by_id = {}
        for field in fields:
            count += 1
            path = (*parent, field.id)
            if field.id in by_id:
                fail(path, "duplicate_field", "Field identities must be unique within their group.")
            by_id[field.id] = field
            if not field.label.strip():
                fail(path, "missing_label", "Enter a readable field label.")
            if field.min_length > field.max_length or field.min_items > field.max_items:
                fail(path, "invalid_bounds", "The minimum cannot exceed the maximum.")
            if any(value is not None and not value.is_finite() for value in (field.minimum, field.maximum)):
                fail(path, "invalid_number_bound", "Numeric bounds must be finite.")
            elif field.minimum is not None and field.maximum is not None and field.minimum > field.maximum:
                fail(path, "invalid_number_bounds", "The minimum cannot exceed the maximum.")
            if field.required and field.nullable:
                fail(path, "required_nullable", "A required field cannot accept an explicit clear.")
            if field.kind in {"choice", "multiple_choice"}:
                if not field.choices or len({choice.id for choice in field.choices}) != len(field.choices):
                    fail(path, "invalid_choices", "Choices need distinct stable identities.")
            elif field.choices:
                fail(path, "unexpected_choices", "This field does not accept choices.")
            if field.kind == "group":
                if not field.fields or depth >= 3:
                    fail(path, "invalid_group", "Groups need fields and can nest at most three levels.")
                else:
                    scope(field.fields, path, depth + 1)
            elif field.fields:
                fail(path, "unexpected_children", "Only a repeatable group can contain fields.")
            if (field.kind == "reference") != (field.reference_type is not None):
                fail(path, "reference_type", "Object reference fields need an explicit permitted type.")
            if field.timezone is not None:
                if field.kind not in {"datetime", "time"}:
                    fail(path, "unexpected_timezone", "A timezone applies only to time fields.")
                else:
                    try:
                        ZoneInfo(field.timezone)
                    except (ZoneInfoNotFoundError, ValueError):
                        fail(path, "unknown_timezone", "Choose a supported timezone.")
        for field in fields:
            if field.when is None:
                continue
            path = (*parent, field.id)
            control = by_id.get(field.when.field)
            if control is None or control.when is not None or control.kind not in {"checkbox", "choice"}:
                fail(path, "invalid_condition", "Conditions need an unconditional checkbox or choice in this group.")
            elif control.kind == "checkbox" and type(field.when.equals) is not bool:
                fail(path, "condition_type", "Checkbox conditions compare a boolean value.")
            elif control.kind == "choice" and (type(field.when.equals) is not str or field.when.equals not in {choice.id for choice in control.choices}):
                fail(path, "condition_choice", "Choose a valid option identity for this condition.")

    scope(definition.fields, (), 1)
    if count > 200:
        fail((), "too_many_fields", "A form can contain at most 200 field definitions.")
    if not definition.title.strip():
        fail((), "missing_title", "Enter a form title.")
    if errors:
        raise ApplicationError("invalid_form_definition", "Resolve the form definition errors before publication.", 422, fields=errors)
    return definition
