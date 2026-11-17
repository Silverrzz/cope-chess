from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from threading import BoundedSemaphore

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field, field_validator

from cope.application.contracts import Input
from cope.application.errors import ApplicationError
from cope.domains.quorum import STATE_LOCK, read_setup, witness_keys

FIELD = 21888242871839275222246405745257275088548364400416034343698204186575808495617
CURVE_FIELD = 21888242871839275222246405745257275088696311157297823662689037894645226208583
GENESIS_KEY = "quorum.genesis.v1"
GENESIS_SIGNATURES_KEY = "quorum.genesis-signatures.v1"
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


class Endorsement(Input):
    witness: str = Field(min_length=44, max_length=44)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    signature: str = Field(min_length=88, max_length=88)
    size: int = Field(default=0, strict=True, ge=0, le=1024)
    final: bool = False


class BallotPublication(Input):
    draft_id: str = Field(pattern=r"^[0-9a-f-]{36}$")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def scope(value):
    return str(int(digest(value), 16))


def read_setting(connection, key, default=None):
    row = connection.execute("SELECT value FROM cope_quorum.settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def put_setting(connection, key, value):
    connection.execute(
        """INSERT INTO cope_quorum.settings (key, value) VALUES (?, ?)
           ON CONFLICT (key) DO UPDATE SET value = excluded.value""",
        (key, canonical(value).decode()),
    )


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


def enrollment_scope(origin, session_token):
    return scope(["cope-quorum-enrollment/1", origin, hashlib.sha256(session_token.encode()).hexdigest()])


def enroll(connection, account_id, commitment, expected_role):
    connection.execute(STATE_LOCK)
    if read_setup(connection)[0].enrollment_role_id != expected_role:
        fail("quorum_role_changed", "The enrollment role changed. Check eligibility again.")
    existing = connection.execute(
        "SELECT commitment FROM cope_quorum.credentials WHERE account_id = ?", (account_id,),
    ).fetchone()
    if existing:
        if existing["commitment"] != commitment:
            fail("quorum_credential_exists", "This account already has a credential. Import its backup.")
        connection.commit()
        return
    if read_setting(connection, GENESIS_KEY):
        fail("quorum_founding_closed", "Founding enrollment has closed.")
    total = connection.execute("SELECT count(*) AS total FROM cope_quorum.credentials").fetchone()["total"]
    if total >= 1024:
        fail("quorum_roster_full", "The founding roster has reached its circuit limit.")
    row = connection.execute(
        """INSERT INTO cope_quorum.credentials (account_id, commitment) VALUES (?, ?)
           ON CONFLICT DO NOTHING RETURNING commitment""", (account_id, commitment),
    ).fetchone()
    if row is None:
        fail("quorum_credential_taken", "This credential is already registered.")
    connection.commit()


def founding_state(connection):
    genesis = read_setting(connection, GENESIS_KEY)
    signatures = read_setting(connection, GENESIS_SIGNATURES_KEY, {})
    return {"genesis": genesis, "signatures": signatures}


def admin_status(connection):
    return {
        **founding_state(connection),
        "enrolled": connection.execute("SELECT count(*) AS total FROM cope_quorum.credentials").fetchone()["total"],
        "published": [str(row["id"]) for row in connection.execute("SELECT id FROM cope_quorum.ballots")],
    }


def freeze_genesis(connection, origin):
    connection.execute(STATE_LOCK)
    state = founding_state(connection)
    if state["genesis"]:
        return state
    setup, _ = read_setup(connection)
    keys = witness_keys(connection)
    witnesses = sorted(keys[user] for user in setup.witness_user_ids if user in keys)
    if not setup.enrollment_role_id:
        fail("quorum_role_missing", "Choose the founding enrollment role before freezing the roster.")
    members = [row["commitment"] for row in connection.execute(
        "SELECT commitment FROM cope_quorum.credentials ORDER BY commitment",
    )]
    if len(witnesses) != 4 or len(members) < 3:
        fail("quorum_founding_incomplete", "Founding requires at least three enrolled voters and exactly four registered witnesses.")
    genesis = {"version": "cope-quorum/1", "community": origin, "members": members,
               "root": proof_worker(members)["root"], "witnesses": witnesses, "threshold": 3,
               "enrollment_role": setup.enrollment_role_id}
    put_setting(connection, GENESIS_KEY, genesis)
    connection.commit()
    return {"genesis": genesis, "signatures": {}}


def signature_for(witness, signature, fingerprint):
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(witness, validate=True))
        key.verify(base64.b64decode(signature, validate=True), bytes.fromhex(fingerprint))
    except (ValueError, InvalidSignature) as error:
        raise ApplicationError("quorum_bad_signature", "The witness signature is invalid.", 422) from error


def endorse_genesis(connection, endorsement):
    connection.execute(STATE_LOCK)
    state = founding_state(connection)
    genesis = state["genesis"]
    if not genesis or endorsement.witness not in genesis["witnesses"]:
        fail("quorum_untrusted_witness", "The witness key is not part of the founding document.", 403)
    if digest(genesis) != endorsement.digest:
        fail("quorum_genesis_mismatch", "The signature is for a different founding document.")
    signature_for(endorsement.witness, endorsement.signature, endorsement.digest)
    state["signatures"][endorsement.witness] = endorsement.signature
    put_setting(connection, GENESIS_SIGNATURES_KEY, state["signatures"])
    connection.commit()
    return state


def publish(connection, draft_id):
    connection.execute(STATE_LOCK)
    state = founding_state(connection)
    genesis = state["genesis"]
    if not genesis or len(state["signatures"]) < genesis["threshold"]:
        fail("quorum_genesis_pending", "The founding document needs three independent witness signatures.")
    setup, _ = read_setup(connection)
    draft = next((item for item in setup.drafts if item.id == draft_id), None)
    if draft is None:
        fail("quorum_missing_draft", "This ballot draft does not exist.", 404)
    if draft.closes_at <= datetime.now(UTC):
        fail("quorum_past_deadline", "Choose a closing date in the future.")
    manifest = {**draft.model_dump(mode="json"), "genesis": digest(genesis)}
    row = connection.execute(
        """INSERT INTO cope_quorum.ballots (id, manifest, opens_at, closes_at)
           VALUES (?::uuid, ?::jsonb, ?, ?) ON CONFLICT DO NOTHING RETURNING id""",
        (draft.id, canonical(manifest).decode(), draft.opens_at, draft.closes_at),
    ).fetchone()
    if row is None:
        fail("quorum_already_published", "This draft has already been published.")
    connection.commit()
    return manifest


def ballot(connection, ballot_id, *, lock=False):
    row = connection.execute(
        "SELECT * FROM cope_quorum.ballots WHERE id = ?::uuid" + (" FOR UPDATE" if lock else ""),
        (str(ballot_id),),
    ).fetchone()
    if not row:
        fail("quorum_missing_ballot", "This ballot does not exist.", 404)
    return row


def checkpoints(connection, ballot_id):
    rows = connection.execute(
        """SELECT size, final, digest, witness, signature FROM cope_quorum.endorsements
           WHERE ballot_id = ?::uuid ORDER BY final DESC, size DESC""", (str(ballot_id),),
    ).fetchall()
    groups = {}
    for row in rows:
        key = (row["size"], row["final"], row["digest"])
        group = groups.setdefault(key, {"size": key[0], "final": key[1], "digest": key[2], "signatures": {}})
        group["signatures"][row["witness"]] = row["signature"]
    return list(groups.values())


def proof_rows(connection, ballot_id, limit=1024):
    return [row["proof"] for row in connection.execute(
        """SELECT proof FROM cope_quorum.votes WHERE ballot_id = ?::uuid
           ORDER BY position LIMIT ?""", (str(ballot_id), limit),
    )]


def snapshot(row, votes, final):
    return {"version": "cope-quorum-checkpoint/1", "manifest": row["manifest"], "votes": votes, "final": final}


def ballot_package(connection, ballot_id):
    row = ballot(connection, ballot_id)
    votes = proof_rows(connection, ballot_id)
    return {
        **founding_state(connection),
        "candidate": snapshot(row, votes, datetime.now(UTC) >= row["closes_at"]),
        "checkpoints": checkpoints(connection, ballot_id),
    }


def has_open_checkpoint(groups, threshold):
    return any(not group["final"] and len(group["signatures"]) >= threshold for group in groups)


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
    state = founding_state(connection)
    if not state["genesis"]:
        return []
    genesis = state["genesis"]
    entries = []
    for row in connection.execute("SELECT * FROM cope_quorum.ballots ORDER BY opens_at DESC"):
        ballot_id = str(row["id"])
        groups = checkpoints(connection, ballot_id)
        approved = [group for group in groups if len(group["signatures"]) >= genesis["threshold"]]
        if not approved:
            continue
        signed = approved[0]
        result = tally(row["manifest"], proof_rows(connection, ballot_id, signed["size"]), len(genesis["members"]))
        now = datetime.now(UTC)
        state_name = ("completed" if signed["final"] else "scheduled" if now < row["opens_at"] else
                      "awaiting_witnesses" if now >= row["closes_at"] else "open")
        entries.append({**row["manifest"], "status": state_name, "result": result if signed["final"] else None})
    return entries


def cast_vote(connection, ballot_id, proof):
    row = ballot(connection, ballot_id)
    genesis = founding_state(connection)["genesis"]
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
    if not has_open_checkpoint(checkpoints(connection, ballot_id), genesis["threshold"]):
        fail("quorum_ballot_pending", "This ballot is awaiting independent witness signatures.")
    if int(proof.message) >= (len(manifest["options"]) if manifest["rule"] == "report_only" else 3):
        fail("quorum_invalid_option", "This option is outside the frozen ballot.", 422)
    connection.commit()
    if not proof_worker(genesis["members"], proof, scope(["cope-quorum-ballot/1", manifest]))["valid"]:
        fail("quorum_invalid_vote", "The anonymous vote proof is invalid.", 422)
    row = ballot(connection, ballot_id, lock=True)
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


def endorse_ballot(connection, ballot_id, endorsement):
    row = ballot(connection, ballot_id, lock=True)
    genesis = founding_state(connection)["genesis"]
    if endorsement.witness not in genesis["witnesses"]:
        fail("quorum_untrusted_witness", "This key is outside the founding witness set.", 403)
    votes = proof_rows(connection, ballot_id)
    if endorsement.size > len(votes) or (endorsement.final and (
            datetime.now(UTC) < row["closes_at"] or endorsement.size != len(votes))):
        fail("quorum_checkpoint_mismatch", "This checkpoint does not match the current ballot boundary.")
    expected = digest(snapshot(row, votes[:endorsement.size], endorsement.final))
    if expected != endorsement.digest:
        fail("quorum_checkpoint_mismatch", "The signed checkpoint does not match this vote prefix.")
    signature_for(endorsement.witness, endorsement.signature, expected)
    connection.execute(
        """INSERT INTO cope_quorum.endorsements (ballot_id, size, final, digest, witness, signature)
           VALUES (?::uuid, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
        (str(ballot_id), endorsement.size, endorsement.final, expected, endorsement.witness, endorsement.signature),
    )
    connection.commit()
