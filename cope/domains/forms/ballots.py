from __future__ import annotations

from typing import Annotated

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg.types.json import Jsonb
from pydantic import Field, StringConstraints, model_validator

from cope.application.contracts import Input
from cope.application.errors import ApplicationError

from . import ballot_protocol as protocol

Slug = Annotated[str, StringConstraints(max_length=80, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
PublicKey = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Signature = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{128}$")]
Number = Annotated[str, StringConstraints(max_length=512, pattern=r"^(0|[1-9a-f][0-9a-f]*)$")]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Proof = tuple[Number, Number]
Cell = tuple[Number, Number, tuple[Number, Number, Number, Number]]
Factor = tuple[Number, Proof]


class Question(Input):
    title: Label
    options: list[Label] = Field(min_length=2, max_length=protocol.MAX_OPTIONS)

    @model_validator(mode="after")
    def distinct_options(self):
        if len({option.casefold() for option in self.options}) != len(self.options):
            raise ValueError("Each question needs distinct options.")
        return self


class Trustee(Input):
    public: Number
    proof: Proof


class Settings(Input):
    title: Label
    description: str = Field(default="", max_length=3000)
    questions: list[Question] = Field(min_length=1, max_length=protocol.MAX_QUESTIONS)
    roster: list[PublicKey] = Field(default_factory=list, max_length=protocol.MAX_VOTERS)
    trustees: list[Trustee] = Field(default_factory=list, max_length=2)
    minimum: int = Field(default=5, strict=True, ge=protocol.MIN_VOTERS, le=protocol.MAX_VOTERS)

    @model_validator(mode="after")
    def distinct_roster(self):
        if len(set(self.roster)) != len(self.roster):
            raise ValueError("The voter roster contains a duplicate enrollment code.")
        return self


class NewBallotForm(Input):
    slug: Slug
    settings: Settings


class DraftEdit(Input):
    revision: int = Field(strict=True, ge=1)
    settings: Settings


class Visibility(Input):
    listed: bool = Field(strict=True)


class BallotQuestion(Input):
    cells: list[Cell] = Field(min_length=2, max_length=protocol.MAX_OPTIONS)
    proof: Proof


class Envelope(Input):
    manifest: PublicKey
    credential: PublicKey
    questions: list[BallotQuestion] = Field(min_length=1, max_length=protocol.MAX_QUESTIONS)
    signature: Signature


class Share(Input):
    trustee: Number
    board_digest: PublicKey
    factors: list[Annotated[list[Factor], Field(min_length=2, max_length=protocol.MAX_OPTIONS)]] = Field(
        min_length=1, max_length=protocol.MAX_QUESTIONS
    )


def get_form(connection, slug: str, lock: str = "") -> dict:
    row = connection.execute("SELECT * FROM secret_ballot_forms WHERE slug = %s" + lock, (slug,)).fetchone()
    if row is None:
        raise ApplicationError("ballot_form_missing", "This ballot form is unavailable.", 404)
    return row


def require_public(row: dict) -> None:
    if row["manifest"] is None:
        raise ApplicationError("ballot_form_missing", "This ballot form is unavailable.", 404)


def count_ballots(connection, slug: str) -> int:
    return connection.execute(
        "SELECT count(*) AS total FROM secret_ballots WHERE form_slug = %s", (slug,)
    ).fetchone()["total"]


def status(row: dict, count: int) -> str:
    if row["opened_at"] is None:
        return "draft"
    if row["closed_at"] is None:
        return "open"
    if row["results"] is not None:
        return "published"
    if count < row["settings"]["minimum"]:
        return "sealed"
    return "tallying"


def summary(row: dict, count: int) -> dict:
    settings = row["settings"]
    return {
        "slug": row["slug"], "title": settings["title"], "description": settings["description"],
        "question_count": len(settings["questions"]), "ballot_count": count,
        "opened_at": row["opened_at"], "closed_at": row["closed_at"], "status": status(row, count),
    }


def directory(connection, principal, limit: int, offset: int) -> dict:
    rows = connection.execute(
        """SELECT f.slug, f.settings, f.opened_at, f.closed_at, f.results,
                  (SELECT count(*) FROM secret_ballots b WHERE b.form_slug = f.slug) AS ballot_count
           FROM secret_ballot_forms f WHERE listed AND opened_at IS NOT NULL
           ORDER BY (closed_at IS NULL) DESC, opened_at DESC, slug LIMIT %s OFFSET %s""", (limit + 1, offset)
    ).fetchall()
    return {"items": [summary(row, row["ballot_count"]) for row in rows[:limit]], "more": len(rows) > limit}


def admin_list(connection, principal, limit: int, offset: int) -> dict:
    principal.require("forms:write")
    rows = connection.execute(
        """SELECT f.slug, f.settings, f.listed, f.opened_at, f.closed_at, f.results,
                  (SELECT count(*) FROM secret_ballots b WHERE b.form_slug = f.slug) AS ballot_count
           FROM secret_ballot_forms f ORDER BY created_at DESC, slug LIMIT %s OFFSET %s""", (limit + 1, offset)
    ).fetchall()
    return {
        "items": [{**summary(row, row["ballot_count"]), "listed": row["listed"]} for row in rows[:limit]],
        "more": len(rows) > limit,
    }


def public_detail(connection, principal, slug: str) -> dict:
    row = get_form(connection, slug)
    require_public(row)
    return {
        **summary(row, count_ballots(connection, slug)),
        "questions": row["settings"]["questions"], "minimum": row["settings"]["minimum"],
        "manifest_digest": row["manifest_digest"], "board_digest": row["board_digest"], "results": row["results"],
    }


def public_manifest(connection, principal, slug: str) -> dict:
    row = get_form(connection, slug)
    require_public(row)
    return row["manifest"]


def admin_detail(connection, principal, slug: str) -> dict:
    principal.require("forms:write")
    row = get_form(connection, slug)
    shares = connection.execute("SELECT trustee FROM secret_ballot_shares WHERE form_slug = %s", (slug,)).fetchall()
    return {
        **summary(row, count_ballots(connection, slug)),
        "settings": row["settings"], "revision": row["revision"], "listed": row["listed"],
        "manifest_digest": row["manifest_digest"], "board_digest": row["board_digest"],
        "trustees_submitted": [entry["trustee"] for entry in shares], "results": row["results"],
    }


def create_form(connection, principal, payload: NewBallotForm) -> dict:
    principal.require("forms:write")
    key = Ed25519PrivateKey.generate().private_bytes_raw()
    connection.execute(
        "INSERT INTO secret_ballot_forms(slug, settings, receipt_private) VALUES (%s, %s, %s)",
        (payload.slug, Jsonb(payload.settings.model_dump(mode="json")), key),
    )
    return {"slug": payload.slug}


def save_draft(connection, principal, slug: str, payload: DraftEdit) -> dict:
    principal.require("forms:write")
    row = get_form(connection, slug, " FOR UPDATE")
    if row["manifest"] is not None or row["revision"] != payload.revision:
        raise ApplicationError("ballot_draft_changed", "This draft changed or was opened. Refresh before editing.")
    connection.execute(
        "UPDATE secret_ballot_forms SET settings = %s, revision = revision + 1 WHERE slug = %s",
        (Jsonb(payload.settings.model_dump(mode="json")), slug),
    )
    return admin_detail(connection, principal, slug)


def set_visibility(connection, principal, slug: str, payload: Visibility) -> dict:
    principal.require("forms:write")
    get_form(connection, slug, " FOR UPDATE")
    connection.execute("UPDATE secret_ballot_forms SET listed = %s WHERE slug = %s", (payload.listed, slug))
    return admin_detail(connection, principal, slug)


def open_form(connection, principal, slug: str, revision: int) -> dict:
    principal.require("forms:write")
    row = get_form(connection, slug, " FOR UPDATE")
    if row["manifest"] is not None:
        return admin_detail(connection, principal, slug)
    if row["revision"] != revision:
        raise ApplicationError("ballot_draft_changed", "This draft changed. Refresh before opening.")
    settings = row["settings"]
    if len(settings["roster"]) < settings["minimum"]:
        raise ApplicationError("ballot_roster_small", "Enroll at least as many voters as the publication minimum.", 422)
    try:
        key = protocol.election_key(slug, settings["trustees"])
    except ValueError as exc:
        raise ApplicationError("invalid_trustee_key", str(exc), 422) from exc
    receipt_key = Ed25519PrivateKey.from_private_bytes(row["receipt_private"]).public_key().public_bytes_raw().hex()
    manifest = {
        "protocol": protocol.PROTOCOL, "slug": slug, "title": settings["title"],
        "description": settings["description"], "questions": settings["questions"], "minimum": settings["minimum"],
        "roster": sorted(settings["roster"]), "trustees": settings["trustees"], "key": protocol.number(key),
        "receipt_key": receipt_key,
    }
    connection.execute(
        """UPDATE secret_ballot_forms SET manifest = %s, manifest_digest = %s, opened_at = clock_timestamp()
           WHERE slug = %s""", (Jsonb(manifest), protocol.digest(manifest), slug)
    )
    return admin_detail(connection, principal, slug)


def close_form(connection, principal, slug: str) -> dict:
    principal.require("forms:write")
    row = get_form(connection, slug, " FOR UPDATE")
    require_public(row)
    if row["closed_at"] is None:
        fingerprints = connection.execute("SELECT fingerprint FROM secret_ballots WHERE form_slug = %s", (slug,)).fetchall()
        root = protocol.board_digest(row["manifest_digest"], [entry["fingerprint"] for entry in fingerprints])
        connection.execute(
            "UPDATE secret_ballot_forms SET closed_at = clock_timestamp(), board_digest = %s WHERE slug = %s", (root, slug)
        )
    return admin_detail(connection, principal, slug)


def receipt(manifest_id: str, fingerprint: str, signature: str) -> dict:
    return {"manifest": manifest_id, "fingerprint": fingerprint, "signature": signature}


def existing_receipt(connection, slug: str, credential: str, manifest_id: str, fingerprint: str) -> dict | None:
    entry = connection.execute(
        "SELECT fingerprint, receipt_signature FROM secret_ballots WHERE form_slug = %s AND credential = %s", (slug, credential)
    ).fetchone()
    if entry is None:
        return None
    if entry["fingerprint"] != fingerprint:
        raise ApplicationError("ballot_already_cast", "This voter proof already signed a different accepted ballot.")
    return receipt(manifest_id, fingerprint, entry["receipt_signature"])


def cast(connection, principal, slug: str, payload: Envelope) -> dict:
    row = get_form(connection, slug)
    require_public(row)
    envelope = payload.model_dump(mode="json")
    try:
        protocol.verify_ballot_signature(row["manifest"], row["manifest_digest"], envelope)
    except ValueError as exc:
        raise ApplicationError("invalid_ballot", str(exc), 422) from exc
    fingerprint = protocol.digest(envelope)
    current = existing_receipt(connection, slug, payload.credential, row["manifest_digest"], fingerprint)
    if current is not None:
        return current
    if row["closed_at"] is not None:
        raise ApplicationError("ballot_form_closed", "This ballot form is closed.")
    leased = connection.execute(
        "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS acquired",
        (f"secret-ballot:{slug}:{payload.credential}",),
    ).fetchone()["acquired"]
    if not leased:
        raise ApplicationError("ballot_verifying", "Cope is already verifying a ballot for this key. Retry the same ballot shortly.")
    current = existing_receipt(connection, slug, payload.credential, row["manifest_digest"], fingerprint)
    if current is not None:
        return current
    try:
        protocol.verify_encrypted_choices(row["manifest"], row["manifest_digest"], envelope)
    except ValueError as exc:
        raise ApplicationError("invalid_ballot", str(exc), 422) from exc
    row = get_form(connection, slug, " FOR SHARE")
    if row["closed_at"] is not None:
        current = existing_receipt(connection, slug, payload.credential, row["manifest_digest"], fingerprint)
        if current is not None:
            return current
        raise ApplicationError("ballot_form_closed", "This ballot form is closed.")
    private = Ed25519PrivateKey.from_private_bytes(row["receipt_private"])
    signature = private.sign(protocol.receipt_message(row["manifest_digest"], fingerprint)).hex()
    connection.execute(
        """INSERT INTO secret_ballots(form_slug, credential, fingerprint, envelope, receipt_signature)
           VALUES (%s, %s, %s, %s, %s)""",
        (slug, payload.credential, fingerprint, Jsonb(envelope), signature),
    )
    return receipt(row["manifest_digest"], fingerprint, signature)


def find_receipt(connection, principal, slug: str, fingerprint: str) -> dict:
    row = get_form(connection, slug)
    require_public(row)
    entry = connection.execute(
        "SELECT receipt_signature FROM secret_ballots WHERE form_slug = %s AND fingerprint = %s", (slug, fingerprint)
    ).fetchone()
    if entry is None:
        raise ApplicationError("ballot_receipt_missing", "This receipt is missing from the ballot board.", 404)
    return receipt(row["manifest_digest"], fingerprint, entry["receipt_signature"])


def board(connection, row: dict) -> dict:
    if row["board_digest"] is None:
        raise ApplicationError("ballot_board_open", "The audit archive is available after voting closes.", 409)
    ballots = connection.execute(
        "SELECT envelope, receipt_signature FROM secret_ballots WHERE form_slug = %s ORDER BY fingerprint", (row["slug"],)
    ).fetchall()
    return {
        "manifest": row["manifest"], "board_digest": row["board_digest"], "ballot_count": len(ballots),
        "ballots": [{"ballot": entry["envelope"], "signature": entry["receipt_signature"]} for entry in ballots],
    }


def audit(connection, principal, slug: str) -> dict:
    row = get_form(connection, slug, " FOR SHARE")
    require_public(row)
    snapshot = board(connection, row)
    shares = tally_shares(connection, slug) if row["results"] is not None else []
    return {**snapshot, "results": row["results"], "shares": shares}


def tally_shares(connection, slug: str) -> list[dict]:
    return [entry["share"] for entry in connection.execute(
        "SELECT share FROM secret_ballot_shares WHERE form_slug = %s ORDER BY trustee", (slug,)
    ).fetchall()]


def submit_share(connection, principal, slug: str, payload: Share) -> dict:
    principal.require("forms:write")
    row = get_form(connection, slug, " FOR UPDATE")
    require_public(row)
    share = payload.model_dump(mode="json")
    shares = tally_shares(connection, slug)
    existing = next((entry for entry in shares if entry["trustee"] == payload.trustee), None)
    if existing is not None:
        if share == existing:
            return admin_detail(connection, principal, slug)
        raise ApplicationError("ballot_share_exists", "A different share was already accepted for this trustee.")
    if row["closed_at"] is None or count_ballots(connection, slug) < row["settings"]["minimum"]:
        raise ApplicationError("ballot_tally_unavailable", "Close voting and meet the publication minimum before tallying.")
    snapshot = board(connection, row)
    sums = protocol.aggregate(snapshot["manifest"], snapshot["ballots"])
    try:
        protocol.check_share(snapshot, share, sums)
        shares.append(share)
        results = protocol.tally(snapshot, shares, sums) if len(shares) == 2 else None
    except ValueError as exc:
        raise ApplicationError("invalid_tally_share", str(exc), 422) from exc
    connection.execute(
        "INSERT INTO secret_ballot_shares(form_slug, trustee, share) VALUES (%s, %s, %s)",
        (slug, payload.trustee, Jsonb(share)),
    )
    if results is not None:
        connection.execute("UPDATE secret_ballot_forms SET results = %s WHERE slug = %s", (Jsonb(results), slug))
    return admin_detail(connection, principal, slug)
