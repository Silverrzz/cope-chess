from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from cope.application.contracts import Input
from cope.discord_bot.accounts import snowflake

SETUP_KEY = "quorum.setup.v1"


class BallotDraft(Input):
    id: str = Field(pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$")
    title: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=4000)
    subject: Literal["engine", "member", "poll"]
    action: Literal["include", "exclude"] | None = None
    target: str | None = Field(default=None, min_length=1, max_length=128)
    rule: Literal["majority", "unanimous_electorate", "unanimous_decisive", "report_only"]
    quorum_percent: int | None = Field(default=None, strict=True, ge=1, le=100)
    opens_at: AwareDatetime
    closes_at: AwareDatetime
    options: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_draft(self):
        if self.closes_at <= self.opens_at:
            raise ValueError("Closing time must follow opening time.")
        if self.subject == "poll":
            if self.rule != "report_only" or self.target or self.action or self.quorum_percent is not None:
                raise ValueError("Polls report turnout and results without applying a decision.")
            if (len(self.options) < 2 or any(not 1 <= len(option.strip()) <= 100 for option in self.options)
                    or len({option.strip().casefold() for option in self.options}) != len(self.options)):
                raise ValueError("Enter 2–20 distinct options of up to 100 characters.")
        elif self.rule == "report_only" or not self.target or not self.action or self.options:
            raise ValueError("A governance motion needs a target, action and decision rule.")
        return self


class Setup(Input):
    revision: int = Field(default=0, strict=True, ge=0)
    enrollment_role_id: str | None = None
    witness_user_ids: list[str] = Field(default_factory=list, max_length=12)
    drafts: list[BallotDraft] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_setup(self):
        if self.enrollment_role_id is not None and not snowflake(self.enrollment_role_id):
            raise ValueError("Enter a valid Discord role ID.")
        if (any(not snowflake(user) for user in self.witness_user_ids)
                or len(set(self.witness_user_ids)) != len(self.witness_user_ids)):
            raise ValueError("Enter unique Discord user IDs for witness nominees.")
        if len({draft.id for draft in self.drafts}) != len(self.drafts):
            raise ValueError("Draft identifiers must be unique.")
        return self


def read_setup(connection):
    row = connection.execute("SELECT value FROM platform_settings WHERE key = ?", (SETUP_KEY,)).fetchone()
    raw = row["value"] if row else None
    return (Setup.model_validate_json(raw) if raw is not None else Setup()), raw


def save_setup(connection, proposed: Setup):
    current, raw = read_setup(connection)
    if proposed.revision != current.revision:
        return None
    updated = proposed.model_copy(update={"revision": current.revision + 1})
    row = connection.execute(
        """INSERT INTO platform_settings (key, value) VALUES (?, ?)
           ON CONFLICT (key) DO UPDATE SET value = excluded.value
           WHERE platform_settings.value = ?
           RETURNING value""",
        (SETUP_KEY, updated.model_dump_json(), raw),
    ).fetchone()
    if row is None:
        return None
    connection.commit()
    return updated
