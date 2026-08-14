from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, TypeVar

from pydantic import BaseModel, TypeAdapter, ValidationError

from .models import BenchmarkEnvelope, Envelope


EnvelopeType = TypeVar("EnvelopeType", Envelope, BenchmarkEnvelope)


class ProtocolError(ValueError):
    close_code = 4000


class ProtocolValidationError(ProtocolError):
    close_code = 4000


def make_message(
    message_type: str,
    data: BaseModel | Mapping[str, Any] | None = None,
    *,
    seq: int = 0,
) -> Envelope:
    return _make_message(Envelope, message_type, data, seq=seq)


def make_benchmark_message(
    message_type: str,
    data: BaseModel | Mapping[str, Any] | None = None,
    *,
    seq: int = 0,
) -> BenchmarkEnvelope:
    return _make_message(BenchmarkEnvelope, message_type, data, seq=seq)


def _make_message(
    envelope_type: type[EnvelopeType],
    message_type: str,
    data: BaseModel | Mapping[str, Any] | None,
    *,
    seq: int,
) -> EnvelopeType:
    if isinstance(data, BaseModel):
        payload = data.model_dump(mode="json")
    else:
        payload = dict(data or {})

    return envelope_type(
        type=message_type,
        seq=seq,
        t_mono_ms=time.monotonic_ns() // 1_000_000,
        data=payload,
    )


def encode_message(message: Envelope | BenchmarkEnvelope) -> str:
    return message.model_dump_json()


def decode_message(
    payload: str | bytes | bytearray,
    message_type: str,
    data_type: Any,
) -> Any:
    return _decode_message(decode_envelope(payload), message_type, data_type)


def decode_benchmark_message(
    payload: str | bytes | bytearray,
    message_type: str,
    data_type: Any,
) -> Any:
    return _decode_message(
        decode_benchmark_envelope(payload),
        message_type,
        data_type,
    )


def _decode_message(
    envelope: Envelope | BenchmarkEnvelope,
    message_type: str,
    data_type: Any,
) -> Any:
    if envelope.type != message_type:
        raise ProtocolValidationError(
            f"expected {message_type} message, got {envelope.type}"
        )

    try:
        return TypeAdapter(data_type).validate_python(envelope.data)
    except ValidationError as error:
        raise ProtocolValidationError(str(error)) from error


def decode_envelope(payload: str | bytes | bytearray) -> Envelope:
    return _decode_envelope(payload, Envelope)


def decode_benchmark_envelope(
    payload: str | bytes | bytearray,
) -> BenchmarkEnvelope:
    return _decode_envelope(payload, BenchmarkEnvelope)


def _decode_envelope(
    payload: str | bytes | bytearray,
    envelope_type: type[EnvelopeType],
) -> EnvelopeType:
    try:
        return envelope_type.model_validate_json(payload)
    except ValidationError as error:
        raise ProtocolValidationError(str(error)) from error
    except ValueError as error:
        raise ProtocolValidationError(str(error)) from error
