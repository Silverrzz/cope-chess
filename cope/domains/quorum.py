import json
from typing import Literal

from cope.application.errors import ApplicationError

from pydantic import AwareDatetime, Field, model_validator

from cope.application.contracts import Input
from cope.discord_bot.accounts import snowflake

STATE_LOCK = "SELECT pg_advisory_xact_lock(692784510936)"
SETUP_KEY = "quorum.setup.v1"


class BallotDraft(Input):
    id: str = Field(pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$")
    title: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=4000)
    subject: Literal["engine", "member", "poll"]
    action: Literal["include", "exclude"] | None = None
    rule: Literal["majority", "unanimous_electorate", "unanimous_decisive", "report_only"]
    quorum_percent: int | None = Field(default=None, strict=True, ge=1, le=100)
    opens_at: AwareDatetime
    closes_at: AwareDatetime
    options: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def read_legacy_target(cls, value):
        if isinstance(value, dict) and "target" in value:
            return {key: item for key, item in value.items() if key != "target"}
        return value

    @model_validator(mode="after")
    def validate_draft(self):
        if self.closes_at <= self.opens_at:
            raise ValueError("Closing time must follow opening time.")
        if self.subject == "poll":
            if self.rule != "report_only" or self.action or self.quorum_percent is not None:
                raise ValueError("Polls report turnout and results without applying a decision.")
            if (len(self.options) < 2 or any(not 1 <= len(option.strip()) <= 100 for option in self.options)
                    or len({option.strip().casefold() for option in self.options}) != len(self.options)):
                raise ValueError("Enter 2–20 distinct options of up to 100 characters.")
        elif self.rule == "report_only" or not self.action or self.options:
            raise ValueError("A governance motion needs an action and decision rule.")
        return self


class Setup(Input):
    revision: int = Field(default=0, strict=True, ge=0)
    enrollment_role_id: str | None = None
    drafts: list[BallotDraft] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_setup(self):
        if self.enrollment_role_id is not None and not snowflake(self.enrollment_role_id):
            raise ValueError("Enter a valid Discord role ID.")
        if len({draft.id for draft in self.drafts}) != len(self.drafts):
            raise ValueError("Draft identifiers must be unique.")
        return self


def read_setup(connection):
    row = connection.execute("SELECT value FROM cope_quorum.settings WHERE key = ?", (SETUP_KEY,)).fetchone()
    raw = row["value"] if row else None
    if raw is None:
        return Setup()
    data = json.loads(raw)
    data.pop("witness_user_ids", None)
    data.pop("witness_labels", None)
    return Setup.model_validate(data)


def save_setup(connection, proposed: Setup):
    connection.execute(STATE_LOCK)
    current = read_setup(connection)
    if proposed.revision != current.revision:
        return None
    if proposed.enrollment_role_id != current.enrollment_role_id and connection.execute(
            "SELECT 1 FROM cope_quorum.credentials LIMIT 1").fetchone():
        raise ApplicationError("quorum_role_locked", "The enrollment role is fixed once a credential has enrolled.", 409)
    updated = proposed.model_copy(update={"revision": current.revision + 1})
    row = connection.execute(
        """INSERT INTO cope_quorum.settings (key, value) VALUES (?, ?)
           ON CONFLICT (key) DO UPDATE SET value = excluded.value
           RETURNING value""",
        (SETUP_KEY, updated.model_dump_json()),
    ).fetchone()
    if row is None:
        return None
    connection.commit()
    return updated
