from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, StrictBool

from .contracts import Command, CommandContext, Input, Outcome, Target
from .errors import ApplicationError


class Metadata(Input):
    name: str = Field(min_length=1, max_length=80)
    author: str = Field(max_length=120)


class Rename(Input):
    name: str = Field(min_length=1, max_length=160)


class Drain(Input):
    draining: StrictBool


class DrainResult(Drain):
    active_games: Literal["continue"]


@dataclass(frozen=True)
class LegacyResource:
    type_key: str
    table: str
    version_channel: str
    fields: str
    join: str = ""

    def predicate(self, target: str) -> tuple[str, object]:
        try:
            return "d.object_id = %s", uuid.UUID(target)
        except ValueError:
            if not target.isascii() or not target.isdecimal() or len(target) > 18 or int(target) < 1:
                raise ApplicationError("invalid_target", "Supply an Object UUID or a positive legacy identifier.", 422)
            return "d.id = %s", int(target)

    def resolve(self, connection, principal, target: str, *, lock: bool) -> Target:
        predicate, value = self.predicate(target)
        if lock:
            row = connection.execute(f"SELECT d.id, d.object_id FROM {self.table} d WHERE {predicate} FOR UPDATE", (value,)).fetchone()
            if row is None:
                raise ApplicationError("not_found", "The requested resource does not exist.", 404)
            version = connection.execute(
                f"SELECT {self.version_channel} AS version FROM application_objects WHERE id = %s AND type_key = %s FOR UPDATE",
                (row["object_id"], self.type_key),
            ).fetchone()
            if version is None:
                raise ApplicationError("identity_mismatch", "The resource identity is inconsistent.")
            return Target(str(row["object_id"]), version["version"], row["id"])
        row = connection.execute(
            f"""SELECT d.id, d.object_id, o.{self.version_channel} AS version, {self.fields}
                FROM {self.table} d JOIN application_objects o ON o.id = d.object_id AND o.type_key = %s
                {self.join} WHERE {predicate}""", (self.type_key, value),
        ).fetchone()
        if row is None:
            raise ApplicationError("not_found", "The requested resource does not exist.", 404)
        return Target(str(row["object_id"]), row["version"], row["id"], dict(row))

    def describe(self, connection, principal, target: Target, model) -> dict:
        return {"object_id": target.object_id, "legacy_id": target.legacy_id, "type": self.type_key,
                "version_channel": self.version_channel, "version": target.version,
                "input": {key: target.data[key] for key in model.model_fields}}

    def project(self, connection, principal, result: dict) -> dict:
        self.resolve(connection, principal, result["object_id"], lock=False)
        return result


ENGINE = LegacyResource("engine", "engines", "content_revision", "d.name, d.author")
COMPETITION = LegacyResource("competition", "tournaments", "content_revision", "d.name")
AGENT = LegacyResource("agent", "workers", "state_version", "COALESCE(s.draining, FALSE) AS draining",
                       "LEFT JOIN control_worker_settings s ON s.worker_id = d.id")


def update_metadata(context: CommandContext, payload: Metadata) -> Outcome:
    context.connection.execute("UPDATE engines SET name = %s, author = %s WHERE id = %s", (payload.name, payload.author, context.target.legacy_id))
    updated = ENGINE.resolve(context.connection, context.principal, context.target.object_id, lock=True)
    return Outcome(updated.object_id, updated.version, payload.model_dump(), updated.legacy_id)


def rename_competition(context: CommandContext, payload: Rename) -> Outcome:
    context.connection.execute("UPDATE tournaments SET name = %s WHERE id = %s", (payload.name, context.target.legacy_id))
    updated = COMPETITION.resolve(context.connection, context.principal, context.target.object_id, lock=True)
    return Outcome(updated.object_id, updated.version, payload.model_dump(), updated.legacy_id)


def set_drain(context: CommandContext, payload: Drain) -> Outcome:
    context.connection.execute(
        """INSERT INTO control_worker_settings(worker_id, draining, changed_by) VALUES (%s, %s, %s)
           ON CONFLICT(worker_id) DO UPDATE SET draining = EXCLUDED.draining,
           changed_by = EXCLUDED.changed_by, changed_at = clock_timestamp()""",
        (context.target.legacy_id, payload.draining, context.principal.actor),
    )
    updated = AGENT.resolve(context.connection, context.principal, context.target.object_id, lock=True)
    return Outcome(updated.object_id, updated.version, {"draining": payload.draining, "active_games": "continue"}, updated.legacy_id)


def commands() -> tuple[Command, ...]:
    return (
        Command("agents.set_drain.v1", "agent", "workers:write", "state_version", Drain, DrainResult,
                AGENT.resolve, set_drain, lambda c, p, t: AGENT.describe(c, p, t, Drain), AGENT.project),
        Command("engines.update_metadata.v1", "engine", "engines:write", "content_revision", Metadata, Metadata,
                ENGINE.resolve, update_metadata, lambda c, p, t: ENGINE.describe(c, p, t, Metadata), ENGINE.project),
        Command("competitions.rename.v1", "competition", "tournaments:write", "content_revision", Rename, Rename,
                COMPETITION.resolve, rename_competition, lambda c, p, t: COMPETITION.describe(c, p, t, Rename), COMPETITION.project),
    )
