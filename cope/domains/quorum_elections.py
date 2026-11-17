from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from threading import BoundedSemaphore

from pydantic import Field, field_validator

from cope.application.contracts import Input
from cope.application.errors import ApplicationError
from cope.domains.quorum import STATE_LOCK, read_setup

FIELD = 21888242871839275222246405745257275088548364400416034343698204186575808495617
CURVE_FIELD = 21888242871839275222246405745257275088696311157297823662689037894645226208583
VERIFY_SLOTS = BoundedSemaphore(2)


def fail(code, message, status=409):
    raise ApplicationError(code, message, status)


class Proof(Input):
    merkleTreeDepth: int = Field(strict=True, ge=10, le=10)
    merkleTreeRoot: str = Field(pattern=r"^(0|[1-9][0-9]{0,76})$")
    nullifier: str = Field(pattern=r"^(0|[1-9][0-9]{0,76})$")
    scope: str = Field(pattern=r"^(0|[1-9][0-9]{0,77})$")
    message: str = Field(pattern=r"^(0|[1-9][0-9]?)$")
    points: list[str] = Field(min_length=8, max_length=8)

    @field_validator("merkleTreeRoot", "nullifier")
    @classmethod
    def field_element(cls, value):
        if int(value) >= FIELD:
            raise ValueError("Invalid field element.")
        return value

    @field_validator("points")
    @classmethod
    def points_in_range(cls, points):
        if any(not re.fullmatch(r"(?:0|[1-9][0-9]{0,76})", p) or int(p) >= CURVE_FIELD for p in points):
            raise ValueError("Invalid proof points.")
        return points


class Enrollment(Input):
    commitment: str = Field(pattern=r"^[1-9][0-9]{0,76}$")
    proof: Proof
    replaces: str | None = Field(default=None, pattern=r"^[1-9][0-9]{0,76}$")


class BallotPublication(Input):
    draft_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    revision: int = Field(strict=True, ge=0)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def scope(value):
    return str(int(digest(value), 16))


def proof_worker(members, proof=None, expected_scope=None):
    if not VERIFY_SLOTS.acquire(blocking=False):
        fail("quorum_verifier_busy", "The proof verifier is busy. Try again shortly.", 503)
    try:
        request = {"members": members}
        if proof is not None:
            request.update(proof=proof.model_dump(), scope=expected_scope)
        default = Path(__file__).resolve().parents[2] / "frontend/quorum-verifier.mjs"
        verifier = os.environ.get("COPE_QUORUM_VERIFIER", str(default))
        worker = subprocess.run(
            ["node", verifier], input=canonical(request), capture_output=True, timeout=20, check=True,
        )
        return json.loads(worker.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise ApplicationError("quorum_verifier_unavailable", "The Quorum proof verifier is unavailable.", 503) from error
    finally:
        VERIFY_SLOTS.release()


def enrollment_scope(origin, session_token, replaces=None):
    session = hashlib.sha256(session_token.encode()).hexdigest()
    if replaces is not None:
        return scope(["cope-quorum-replacement/1", origin, session, replaces])
    return scope(["cope-quorum-enrollment/1", origin, session])


def enroll(connection, account_id, commitment, expected_role, replaces=None):
    connection.execute(STATE_LOCK)
    if read_setup(connection).enrollment_role_id != expected_role:
        fail("quorum_role_changed", "The enrollment role changed. Check eligibility again.")
    existing = connection.execute(
        "SELECT commitment FROM cope_quorum.credentials WHERE account_id = ?", (account_id,),
    ).fetchone()
    current = existing["commitment"] if existing else None
    if current == commitment:
        connection.commit()
        return
    if current != replaces:
        if replaces is None:
            fail("quorum_credential_exists", "This account already has a credential. Import its backup.")
        fail("quorum_credential_changed", "Your enrolled credential changed. Reopen Voting credential and try again.")
    if connection.execute("SELECT 1 FROM cope_quorum.retired_credentials WHERE commitment = ?", (commitment,)).fetchone():
        fail("quorum_credential_retired", "This credential was replaced. Use your current credential.")
    if current is not None:
        if connection.execute("SELECT 1 FROM cope_quorum.credentials WHERE commitment = ?", (commitment,)).fetchone():
            fail("quorum_credential_taken", "This credential is already registered.")
        connection.execute("INSERT INTO cope_quorum.retired_credentials (commitment) VALUES (?)", (current,))
        connection.execute(
            "UPDATE cope_quorum.credentials SET commitment = ? WHERE account_id = ?", (commitment, account_id),
        )
        connection.commit()
        return
    total = connection.execute("SELECT count(*) AS total FROM cope_quorum.credentials").fetchone()["total"]
    if total >= 1024:
        fail("quorum_roster_full", "Quorum has reached its 1,024-voter enrollment limit.")
    row = connection.execute(
        """INSERT INTO cope_quorum.credentials (account_id, commitment) VALUES (?, ?)
           ON CONFLICT DO NOTHING RETURNING commitment""", (account_id, commitment),
    ).fetchone()
    if row is None:
        fail("quorum_credential_taken", "This credential is already registered.")
    connection.commit()


def admin_status(connection):
    return {
        "enrolled": connection.execute("SELECT count(*) AS total FROM cope_quorum.credentials").fetchone()["total"],
        "published": [str(row["id"]) for row in connection.execute("SELECT id FROM cope_quorum.ballots")],
    }


def publish(connection, draft_id, revision):
    connection.execute(STATE_LOCK)
    setup = read_setup(connection)
    if setup.revision != revision:
        fail("quorum_draft_changed", "Quorum settings changed. Reload the draft before publishing.")
    draft = next((item for item in setup.drafts if item.id == draft_id), None)
    if draft is None:
        fail("quorum_missing_draft", "This ballot draft does not exist.", 404)
    if not setup.enrollment_role_id:
        fail("quorum_role_missing", "Choose a Discord enrollment role before publishing.")
    members = [row["commitment"] for row in connection.execute(
        "SELECT commitment FROM cope_quorum.credentials ORDER BY commitment",
    )]
    if not members:
        fail("quorum_roster_too_small", "Enroll at least one voter before publishing.")
    roster = {"members": members, "root": proof_worker(members)["root"]}
    if draft.closes_at <= datetime.now(UTC):
        fail("quorum_past_deadline", "Choose a closing date in the future.")
    manifest = {**draft.model_dump(mode="json"), "roster": digest(roster)}
    row = connection.execute(
        """INSERT INTO cope_quorum.ballots (id, manifest, roster, opens_at, closes_at)
           VALUES (?::uuid, ?::jsonb, ?::jsonb, ?, ?) ON CONFLICT DO NOTHING RETURNING id""",
        (draft.id, canonical(manifest).decode(), canonical(roster).decode(), draft.opens_at, draft.closes_at),
    ).fetchone()
    if row is None:
        fail("quorum_already_published", "This draft has already been published.")
    connection.commit()
    return manifest


def ballot(connection, ballot_id, *, lock=""):
    row = connection.execute(
        "SELECT * FROM cope_quorum.ballots WHERE id = ?::uuid" + lock,
        (str(ballot_id),),
    ).fetchone()
    if not row:
        fail("quorum_missing_ballot", "This ballot does not exist.", 404)
    return row


def proof_rows(connection, ballot_id):
    return [row["proof"] for row in connection.execute(
        """SELECT proof FROM cope_quorum.votes WHERE ballot_id = ?::uuid
           ORDER BY position""", (str(ballot_id),),
    )]


def ballot_package(connection, ballot_id):
    row = ballot(connection, ballot_id, lock=" FOR SHARE")
    now = datetime.now(UTC)
    closed = now >= row["closes_at"]
    votes = proof_rows(connection, ballot_id) if closed else []
    roster = row["roster"]
    return {
        "version": "cope-quorum-evidence/1",
        "manifest": row["manifest"],
        "roster": roster,
        "votes": votes,
        "status": "completed" if closed else "scheduled" if now < row["opens_at"] else "open",
        "result": tally(row["manifest"], votes, len(roster["members"])) if closed else None,
    }


def tally(manifest, votes, electorate):
    options = manifest["options"] if manifest["rule"] == "report_only" else ["Support", "Oppose", "Abstain"]
    counts = [0] * len(options)
    for proof in votes:
        counts[int(proof["message"])] += 1
    total = len(votes)
    minimum = manifest["quorum_percent"]
    if manifest["rule"] == "report_only":
        result = "report_only"
    elif minimum is not None and total * 100 < minimum * electorate:
        result = "quorum_not_met"
    else:
        support, opposition, _ = counts
        passed = {
            "majority": support > opposition,
            "unanimous_electorate": electorate > 0 and support == electorate,
            "unanimous_decisive": support > 0 and opposition == 0,
        }[manifest["rule"]]
        result = "passed" if passed else "rejected"
    return {"options": options, "counts": counts, "total": total, "electorate": electorate, "outcome": result}


def directory(connection):
    now = datetime.now(UTC)
    entries = []
    for row in connection.execute("SELECT * FROM cope_quorum.ballots ORDER BY opens_at DESC FOR SHARE"):
        closed = now >= row["closes_at"]
        result = tally(row["manifest"], proof_rows(connection, row["id"]), len(row["roster"]["members"])) if closed else None
        entries.append({**row["manifest"], "status": "completed" if closed else "scheduled" if now < row["opens_at"] else "open",
                        "result": result})
    return entries


def cast_vote(connection, ballot_id, proof):
    row = ballot(connection, ballot_id)
    manifest = row["manifest"]
    now = datetime.now(UTC)
    if now < row["opens_at"]:
        fail("quorum_voting_closed", "This ballot is outside its voting window.")
    if now >= row["closes_at"]:
        accepted = connection.execute(
            "SELECT position, proof FROM cope_quorum.votes WHERE ballot_id = ?::uuid AND nullifier = ?",
            (str(ballot_id), proof.nullifier),
        ).fetchone()
        if accepted and accepted["proof"] == proof.model_dump():
            return {"position": accepted["position"], "nullifier": proof.nullifier}
        fail("quorum_voting_closed", "This ballot has closed.")
    if int(proof.message) >= (len(manifest["options"]) if manifest["rule"] == "report_only" else 3):
        fail("quorum_invalid_option", "This option is outside the frozen ballot.", 422)
    connection.commit()
    if not proof_worker(row["roster"]["members"], proof, scope(["cope-quorum-ballot/1", manifest]))["valid"]:
        fail("quorum_invalid_vote", "The anonymous vote proof is invalid.", 422)
    row = ballot(connection, ballot_id, lock=" FOR UPDATE")
    accepted = connection.execute(
        "SELECT position, proof FROM cope_quorum.votes WHERE ballot_id = ?::uuid AND nullifier = ?",
        (str(ballot_id), proof.nullifier),
    ).fetchone()
    if accepted:
        if accepted["proof"]["message"] != proof.message:
            fail("quorum_already_voted", "This credential already cast a different choice.")
        return {"position": accepted["position"], "nullifier": proof.nullifier}
    if datetime.now(UTC) >= row["closes_at"]:
        fail("quorum_voting_closed", "This ballot has closed.")
    next_position = connection.execute(
        "SELECT coalesce(max(position), 0) + 1 AS position FROM cope_quorum.votes WHERE ballot_id = ?::uuid",
        (str(ballot_id),),
    ).fetchone()["position"]
    connection.execute(
        """INSERT INTO cope_quorum.votes (ballot_id, position, nullifier, proof)
           VALUES (?::uuid, ?, ?, ?::jsonb)""",
        (str(ballot_id), next_position, proof.nullifier, canonical(proof.model_dump()).decode()),
    )
    connection.commit()
    return {"position": next_position, "nullifier": proof.nullifier}
