from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, ValidationError, field_validator

from cope.core.models import TournamentConfig
from cope.db import (
    engine_build_is_benchmarked,
    get_opening_suite,
    list_benchmark_hardware,
    list_engine_records,
    list_opening_suites,
)
from cope.db.repo import validate_tournament_variant
from cope.variants import VARIANTS


class TournamentPayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    config: TournamentConfig

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("tournament name cannot be blank")
        return value


def _validate_engine_variant(connection: sqlite3.Connection, record, variant: str) -> None:
    if variant == "standard":
        return
    capability = connection.execute("SELECT status FROM engine_variant_capabilities WHERE build_hash = ? AND variant = ?", (record.build_hash, variant)).fetchone()
    if capability is None or capability["status"] != "supported":
        raise HTTPException(status_code=422, detail=f"{variant} participants require recorded capability evidence for their current build.")


def _engine_is_tournament_ready(
    connection: sqlite3.Connection,
    record,
) -> bool:
    if not record.active or not record.engine_active or not record.version.strip():
        return False
    if record.distribution == "worker_local":
        return True
    return engine_build_is_benchmarked(
        connection,
        engine_version_id=record.id,
        build_hash=record.build_hash,
    )


def _category_settings(value: dict[str, Any]) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "format": "round_robin",
        "format_options": {"cycles": 1},
        "time_control": {
            "category": "increment",
            "initial_ms": 60_000,
            "increment_ms": 1_000,
        },
        "concurrency": 1,
        "opening_suite_id": None,
        "adjudication": {
            "draw": None,
            "resign": None,
            "max_moves": None,
        },
        "rated": True,
        "lag_compensation_ms": 50,
        "engine_threads": 1,
        "engine_hash_mb": 16,
    }
    category_keys = {
        "time_control",
        "adjudication",
        "rated",
        "lag_compensation_ms",
        "engine_threads",
        "engine_hash_mb",
    }
    merged = {**defaults, **{key: item for key, item in value.items() if key in category_keys}}
    try:
        config = TournamentConfig(
            participants=[1, 2],
            **merged,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=[error["msg"] for error in exc.errors()],
        ) from exc
    serialized = config.model_dump(mode="json")
    for key in ("participants", "uci_options"):
        serialized.pop(key, None)
    return serialized


def _validated_tournament_config(
    connection: sqlite3.Connection,
    submitted: TournamentConfig,
) -> TournamentConfig:
    records = {engine.id: engine for engine in list_engine_records(connection)}
    missing = [engine_id for engine_id in submitted.participants if engine_id not in records]
    if missing:
        raise HTTPException(status_code=422, detail="One or more selected engines no longer exist.")
    unavailable = [
        engine_id for engine_id in submitted.participants
        if not _engine_is_tournament_ready(connection, records[engine_id])
    ]
    if unavailable:
        raise HTTPException(status_code=422, detail="Every participant must be active and ready to run.")
    if submitted.clock_reference_hardware_key is not None:
        if any(records[engine_id].distribution != "managed" for engine_id in submitted.participants):
            raise HTTPException(status_code=422, detail="Hardware-scaled clocks require managed builds for every participant.")
        if not any(profile.hardware_key == submitted.clock_reference_hardware_key for profile in list_benchmark_hardware(connection)):
            raise HTTPException(status_code=422, detail="Choose a known benchmark hardware profile.")

    _validate_opening_suite_reference(connection, submitted.opening_suite_id)
    try:
        validate_tournament_variant(connection, submitted)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for engine_id in submitted.participants:
        _validate_engine_variant(connection, records[engine_id], submitted.variant)
    return submitted


def _validate_opening_suite_reference(
    connection: sqlite3.Connection,
    suite_id: int | None,
) -> None:
    if suite_id is not None and get_opening_suite(connection, suite_id) is None:
        raise HTTPException(status_code=422, detail="Choose an existing opening suite.")


def _tournament_form_payload(
    web_app,
    request: Request,
    connection: sqlite3.Connection,
    *,
    tournament=None,
) -> dict[str, Any]:
    if tournament is not None:
        config = tournament.config.model_dump(mode="json")
        name = tournament.name
        participants = list(tournament.config.participants)
    else:
        default_settings = _category_settings({})
        config = {
            "participants": [],
            "engine_threads": 1,
            "engine_hash_mb": 16,
            "uci_options": {},
            **default_settings,
        }
        name = ""
        participants = []

    participant_ids = set(participants)
    engines = [
        engine
        for engine in list_engine_records(connection)
        if (
            _engine_is_tournament_ready(connection, engine)
        ) or engine.id in participant_ids
    ]
    return {
        "name": name,
        "config": config,
        "participants": participants,
        "engine_options": engines,
        "opening_suites": list_opening_suites(connection),
        "clock_reference_profiles": list_benchmark_hardware(connection),
        "variants": [dict(id=v.id, name=v.name, rules_revision=v.rules_revision) for v in VARIANTS.values()],
    }
