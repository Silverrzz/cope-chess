from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Callable
from zoneinfo import ZoneInfo

from cope.application.errors import ApplicationError

from .definition import FormDefinition, FormField, compile_definition


@dataclass(frozen=True)
class ResponseContext:
    resolve_reference: Callable[[FormField, str], str] | None = None
    resolve_attachment: Callable[[FormField, str], str] | None = None


class FieldValueError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def decode_scalar(field: FormField, value: Any, context: ResponseContext) -> Any:
    def invalid(code: str, message: str):
        raise FieldValueError(code, message)

    if value is None:
        if field.nullable:
            return None
        invalid("null_not_allowed", "This field cannot be cleared.")
    if field.kind in {"text", "long_text", "email"}:
        if type(value) is not str:
            invalid("text_required", "Enter a text value.")
        minimum = max(field.min_length, 1 if field.required else 0)
        if not minimum <= len(value) <= field.max_length:
            invalid("text_length", "The text is outside the permitted length.")
        if field.kind == "email":
            if len(value) > 254 or value.count("@") != 1 or any(character.isspace() for character in value):
                invalid("invalid_email", "Enter an email address.")
            local, domain = value.rsplit("@", 1)
            if not local or len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local:
                invalid("invalid_email", "Enter an email address.")
            if re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local) is None:
                invalid("invalid_email", "Enter an email address.")
            try:
                domain = domain.encode("idna").decode("ascii").lower()
            except UnicodeError:
                invalid("invalid_email", "Enter an email address.")
            labels = domain.split(".")
            if any(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None for label in labels):
                invalid("invalid_email", "Enter an email address.")
            return local + "@" + domain
        if field.required and not value.strip():
            invalid("required", "Enter a value for this field.")
        return value
    if field.kind == "checkbox":
        if type(value) is not bool:
            invalid("boolean_required", "Supply a boolean value.")
        return value
    if field.kind in {"integer", "decimal"}:
        if field.kind == "integer":
            if type(value) is str and re.fullmatch(r"-?(?:0|[1-9][0-9]{0,23})", value):
                value = int(value)
            if type(value) is not int or abs(value) >= 10**24:
                invalid("integer_required", "Enter an integer within the supported range.")
            number = Decimal(value)
        else:
            if type(value) not in {str, int, float} or len(str(value)) > 100:
                invalid("decimal_required", "Enter a decimal number.")
            if type(value) is str and re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value) is None:
                invalid("decimal_required", "Enter a decimal number using a decimal point.")
            try:
                number = Decimal(str(value))
                if not number.is_finite() or abs(number) >= Decimal(10)**24:
                    invalid("decimal_required", "Enter a finite decimal within the supported range.")
                with localcontext() as decimal_context:
                    decimal_context.prec = 50
                    if number.quantize(Decimal(1).scaleb(-field.precision)) != number:
                        invalid("decimal_precision", "The number has too many decimal places.")
            except InvalidOperation:
                invalid("decimal_required", "Enter a supported decimal number.")
        if field.minimum is not None and number < field.minimum or field.maximum is not None and number > field.maximum:
            invalid("number_range", "The number is outside the permitted range.")
        return value if field.kind == "integer" else format(number, "f")
    if field.kind in {"date", "time", "datetime"}:
        if type(value) is not str or len(value) > 100:
            invalid("invalid_date_time", "Enter a date or time in the declared format.")
        try:
            if field.kind == "date":
                if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
                    invalid("invalid_date", "Enter a date as YYYY-MM-DD.")
                return date.fromisoformat(value).isoformat()
            if field.kind == "time":
                if re.fullmatch(r"[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|[+-][0-9]{2}:[0-9]{2})?", value) is None:
                    invalid("invalid_time", "Enter a time with hours and minutes.")
                return time.fromisoformat(value).isoformat()
            if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|[+-][0-9]{2}:[0-9]{2})", value) is None:
                invalid("timezone_required", "Include an ISO date, time and selected UTC offset.")
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                invalid("timezone_required", "Include the selected UTC offset with this date and time.")
            if field.timezone:
                localized = parsed.astimezone(ZoneInfo(field.timezone))
                if localized.utcoffset() != parsed.utcoffset() or localized.replace(tzinfo=None) != parsed.replace(tzinfo=None):
                    invalid("timezone_mismatch", "The selected offset does not match this local date and timezone.")
            return parsed.isoformat()
        except FieldValueError:
            raise
        except ValueError:
            invalid("invalid_date_time", "Enter a valid date and time.")
    if field.kind in {"choice", "multiple_choice"}:
        permitted = {choice.id for choice in field.choices}
        if field.kind == "choice":
            if type(value) is not str or value not in permitted:
                invalid("invalid_choice", "Choose an available option.")
            return value
        if type(value) is not list or len(value) > field.max_items or len(value) < max(field.min_items, 1 if field.required else 0):
            invalid("choice_count", "Choose the permitted number of options.")
        if any(type(item) is not str or item not in permitted for item in value) or len(set(value)) != len(value):
            invalid("invalid_choice", "Choose distinct available options.")
        return value
    if field.kind in {"attachment", "reference"}:
        if type(value) is not str:
            invalid("invalid_reference", "Choose a permitted resource.")
        try:
            identity = str(uuid.UUID(value))
        except (ValueError, AttributeError):
            invalid("invalid_reference", "Choose a permitted resource.")
        resolver = context.resolve_attachment if field.kind == "attachment" else context.resolve_reference
        if resolver is None:
            invalid("reference_not_permitted", "This resource cannot be used in this response.")
        try:
            return str(uuid.UUID(resolver(field, identity)))
        except (ApplicationError, PermissionError, LookupError, ValueError, TypeError, AttributeError):
            invalid("reference_not_permitted", "This resource cannot be used in this response.")
    invalid("unsupported_field", "This field cannot accept a scalar value.")


def validate_response(definition: FormDefinition, values: dict, context: ResponseContext | None = None) -> dict:
    compile_definition(definition)
    context = context or ResponseContext()
    try:
        encoded = json.dumps(values, allow_nan=False, ensure_ascii=False).encode()
    except (TypeError, ValueError, RecursionError) as exc:
        raise ApplicationError("invalid_response", "The response must contain supported JSON values.", 422) from exc
    if len(encoded) > definition.max_payload_bytes:
        raise ApplicationError("response_too_large", "The response exceeds this form's payload limit.", 413)
    errors = []
    row_count = 0

    def fail(path: tuple, code: str, message: str):
        if len(errors) < 200:
            errors.append({"path": list(path), "code": code, "message": message})

    def scope(fields: tuple[FormField, ...], submitted: Any, parent: tuple) -> dict:
        nonlocal row_count
        result = {}
        if type(submitted) is not dict:
            fail(parent, "object_required", "Supply answers by field identity.")
            return result
        known = {field.id for field in fields}
        for key in submitted:
            if key not in known:
                fail((*parent, str(key)), "unknown_field", "This field is not part of the published form.")
        ordered = sorted(fields, key=lambda field: field.when is not None)
        for field in ordered:
            path = (*parent, field.id)
            if field.when:
                control = result.get(field.when.field)
                active = control is not None and type(control) is type(field.when.equals) and control == field.when.equals
                if not active:
                    if field.id in submitted:
                        fail(path, "inactive_field_supplied", "Remove this inactive answer before submitting.")
                    continue
            if field.id not in submitted:
                if field.required:
                    fail(path, "required", "Answer this required field.")
                continue
            value = submitted[field.id]
            if field.kind != "group" or value is None:
                try:
                    result[field.id] = decode_scalar(field, value, context)
                except FieldValueError as error:
                    fail(path, error.code, str(error))
                continue
            minimum = max(field.min_items, 1 if field.required else 0)
            if type(value) is not list or not minimum <= len(value) <= field.max_items:
                fail(path, "row_count", "Supply the permitted number of rows.")
                continue
            rows = []
            seen = set()
            for index, row in enumerate(value):
                row_count += 1
                if row_count > definition.max_rows:
                    fail(path, "total_row_limit", "The response contains too many rows.")
                    break
                row_path = (*path, index)
                if type(row) is not dict:
                    fail(row_path, "object_required", "Supply a group row.")
                    continue
                try:
                    row_id = str(uuid.UUID(row.get("_row_id", "")))
                except (ValueError, AttributeError, TypeError):
                    fail(row_path, "row_identity", "Each row needs a stable UUID identity.")
                    continue
                if row_id in seen:
                    fail(row_path, "duplicate_row_identity", "Each row needs a distinct identity.")
                    continue
                seen.add(row_id)
                answers = scope(field.fields, {key: item for key, item in row.items() if key != "_row_id"}, (*path, row_id))
                rows.append({"_row_id": row_id, **answers})
            result[field.id] = rows
        return result

    canonical = scope(definition.fields, values, ())
    if errors:
        raise ApplicationError("invalid_response", "Check the highlighted answers.", 422, fields=errors)
    return canonical
