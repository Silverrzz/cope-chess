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


class VoteChangeProof(Proof):
    message: str = Field(pattern=r"^(0|[1-9][0-9]{0,77})$")


class VoteChange(Input):
    revision: int = Field(strict=True, ge=0, le=2**53 - 2)
    proof: Proof
    authorization: VoteChangeProof


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
    witnesses = [witness.model_dump(exclude={"enabled"}) for witness in setup.witnesses if witness.enabled]
    if witnesses:
        manifest["witnesses"] = witnesses
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


def ballot_package(connection, ballot_id, receipt=None):
    row = ballot(connection, ballot_id, lock=" FOR SHARE")
    return build_evidence(connection, row, datetime.now(UTC), receipt=receipt)


def build_evidence(connection, row, now, *, receipt=None):
    manifest = row["manifest"]
    roster = row["roster"]
    electorate = len(roster["members"])
    ballot_id = str(row["id"])
    closed = now >= row["closes_at"]
    opened = now >= row["opens_at"]
    if opened and not closed:
        total = connection.execute(
            "SELECT count(*) AS total FROM cope_quorum.votes WHERE ballot_id = ?::uuid", (ballot_id,),
        ).fetchone()["total"]
        closed = total == electorate
    votes = proof_rows(connection, ballot_id) if closed else []
    evidence = {
        "version": "cope-quorum-evidence/1",
        "manifest": manifest,
        "roster": roster,
        "votes": votes,
        "status": "completed" if closed else "open" if opened else "scheduled",
        "result": tally(manifest, votes, electorate) if closed else None,
    }
    if evidence["status"] == "open" and receipt and re.fullmatch(r"0|[1-9][0-9]{0,76}", receipt):
        accepted = connection.execute(
            "SELECT revision FROM cope_quorum.votes WHERE ballot_id = ?::uuid AND nullifier = ?", (ballot_id, receipt),
        ).fetchone()
        if accepted:
            evidence["vote_revision"] = accepted["revision"]
            evidence["live_tally"] = current_tally(connection, ballot_id, ballot_options(manifest), electorate)
    return evidence


def ballot_options(manifest):
    return manifest["options"] if manifest["rule"] == "report_only" else ["Support", "Oppose", "Abstain"]


def current_tally(connection, ballot_id, options, electorate):
    counts = [0] * len(options)
    for row in connection.execute(
        """SELECT proof->>'message' AS choice, count(*) AS total
           FROM cope_quorum.votes WHERE ballot_id = ?::uuid GROUP BY choice""",
        (str(ballot_id),),
    ):
        counts[int(row["choice"])] = row["total"]
    return {"options": options, "counts": counts, "total": sum(counts), "electorate": electorate}


def tally(manifest, votes, electorate):
    options = ballot_options(manifest)
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


def witness_status(connection, evidence):
    manifest = evidence["manifest"]
    witnesses = manifest.get("witnesses", [])
    signed = {}
    if witnesses and evidence["status"] == "completed":
        signed = {
            row["public_key"]: row["signed_at"]
            for row in connection.execute(
                """SELECT public_key, signed_at FROM cope_quorum.witness_signatures
                   WHERE ballot_id = ?::uuid AND digest = ?""", (manifest["id"], digest(evidence)),
            )
        }
    return [{**witness, "signed_at": signed.get(witness["public_key"])} for witness in witnesses]


def directory(connection):
    now = datetime.now(UTC)
    entries = []
    for row in connection.execute("SELECT * FROM cope_quorum.ballots ORDER BY opens_at DESC FOR SHARE"):
        evidence = build_evidence(connection, row, now)
        entries.append({**evidence["manifest"], "status": evidence["status"], "result": evidence["result"],
                        "witnesses": witness_status(connection, evidence)})
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
    if int(proof.message) >= len(ballot_options(manifest)):
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
    if next_position > len(row["roster"]["members"]):
        fail("quorum_voting_closed", "This ballot has closed.")
    connection.execute(
        """INSERT INTO cope_quorum.votes (ballot_id, position, nullifier, proof)
           VALUES (?::uuid, ?, ?, ?::jsonb)""",
        (str(ballot_id), next_position, proof.nullifier, canonical(proof.model_dump()).decode()),
    )
    connection.commit()
    return {"position": next_position, "nullifier": proof.nullifier}


def change_vote(connection, ballot_id, submitted):
    proof = submitted.proof
    authorization = submitted.authorization
    row = ballot(connection, ballot_id)
    manifest = row["manifest"]
    expected_message = scope(["cope-quorum-vote-change/1", str(ballot_id), submitted.revision,
                              digest(proof.model_dump())])
    if authorization.nullifier != proof.nullifier or authorization.message != expected_message:
        fail("quorum_invalid_vote_change", "The authorization does not match this vote change.", 422)
    if int(proof.message) >= len(ballot_options(manifest)):
        fail("quorum_invalid_option", "This option is outside the frozen ballot.", 422)
    accepted = connection.execute(
        "SELECT position, proof, revision FROM cope_quorum.votes WHERE ballot_id = ?::uuid AND nullifier = ?",
        (str(ballot_id), proof.nullifier),
    ).fetchone()
    if accepted is None:
        fail("quorum_vote_missing", "This credential has no recorded vote on this ballot.", 404)
    if accepted["revision"] == submitted.revision + 1 and accepted["proof"] == proof.model_dump():
        return {"position": accepted["position"], "nullifier": proof.nullifier}
    if accepted["revision"] != submitted.revision:
        fail("quorum_vote_changed", "Your vote changed on another request. Review your choice and try again.")
    if build_evidence(connection, row, datetime.now(UTC))["status"] != "open":
        fail("quorum_voting_closed", "This ballot has closed.")
    connection.commit()
    ballot_scope = scope(["cope-quorum-ballot/1", manifest])
    if not proof_worker(row["roster"]["members"], proof, ballot_scope)["valid"]:
        fail("quorum_invalid_vote", "The anonymous vote proof is invalid.", 422)
    if not proof_worker(row["roster"]["members"], authorization, ballot_scope)["valid"]:
        fail("quorum_invalid_vote_change", "The anonymous vote change authorization is invalid.", 422)
    row = ballot(connection, ballot_id, lock=" FOR UPDATE")
    accepted = connection.execute(
        "SELECT position, proof, revision FROM cope_quorum.votes WHERE ballot_id = ?::uuid AND nullifier = ?",
        (str(ballot_id), proof.nullifier),
    ).fetchone()
    if accepted["revision"] == submitted.revision + 1 and accepted["proof"] == proof.model_dump():
        return {"position": accepted["position"], "nullifier": proof.nullifier}
    if accepted["revision"] != submitted.revision:
        fail("quorum_vote_changed", "Your vote changed on another request. Review your choice and try again.")
    if build_evidence(connection, row, datetime.now(UTC))["status"] != "open":
        fail("quorum_voting_closed", "This ballot has closed.")
    connection.execute(
        """UPDATE cope_quorum.votes SET proof = ?::jsonb, revision = revision + 1
           WHERE ballot_id = ?::uuid AND nullifier = ? AND revision = ?""",
        (canonical(proof.model_dump()).decode(), str(ballot_id), proof.nullifier, submitted.revision),
    )
    connection.commit()
    return {"position": accepted["position"], "nullifier": proof.nullifier}
