"""Witness checkpoints over ordered vote-proof hashes. No proofs are exposed while open."""
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field

from cope.application.contracts import Input
from cope.domains import quorum_elections as elections

VERSION = "cope-quorum-vote-history/1"


class CheckpointInput(Input):
    phase: str = Field(pattern=r"^(open|closed)$")
    size: int = Field(strict=True, ge=0, le=1024)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    public_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    signature: str = Field(pattern=r"^[a-f0-9]{128}$")


def validate_policy(policy):
    if not isinstance(policy, dict) or not {"threshold", "operators"} <= policy.keys() <= {
        "threshold", "operators", "max_faulty"
    } or not isinstance(policy["operators"], dict):
        raise ValueError("Invalid witness policy.")
    keys = policy["operators"]
    threshold = policy["threshold"]
    size = len(keys)
    if type(threshold) is not int or not 1 <= threshold <= size <= 64:
        raise ValueError("Invalid witness threshold.")
    fault = policy.get("max_faulty", 2 * threshold - size - 1)
    if type(fault) is not int or fault < 0 or 2 * threshold - size <= fault:
        raise ValueError("The witness threshold must force an honest overlap between any two quorums.")
    if not all(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", operator) and isinstance(key, str)
               and re.fullmatch(r"[a-f0-9]{64}", key) for operator, key in keys.items()) or len(set(keys.values())) != size:
        raise ValueError("Use distinct Ed25519 keys for the independent witness operators.")
    return policy


def verify_policy_chain(data, origin):
    if isinstance(data, dict) and "initial" in data:
        if data.keys() != {"initial", "changes"} or not isinstance(data["changes"], list) or len(data["changes"]) > 128:
            raise ValueError("Invalid witness policy history.")
        policy, changes = validate_policy(data["initial"]), data["changes"]
    else:
        policy, changes = validate_policy(data), []
    digest = elections.digest(policy)
    head = elections.digest(["cope-quorum-witness-policy-anchor/1", origin, digest])
    heads = [head]
    for change in changes:
        if not isinstance(change, dict) or change.keys() != {"policy", "signatures"}:
            raise ValueError("Invalid witness policy change.")
        new = validate_policy(change["policy"])
        new_digest = elections.digest(new)
        statement = ["cope-quorum-witness-policy-change/1", origin, head, digest, new_digest]
        keys = set(policy["operators"].values())
        signatures = change["signatures"]
        if (not isinstance(signatures, dict) or not signatures.keys() <= keys
                or len(signatures) < policy["threshold"]):
            raise ValueError("The policy change needs the current threshold of valid witness approvals.")
        try:
            for key, signature in signatures.items():
                if not isinstance(signature, str) or not re.fullmatch(r"[a-f0-9]{128}", signature):
                    raise ValueError("Invalid policy change signature.")
                Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(
                    bytes.fromhex(signature), elections.canonical(statement),
                )
        except InvalidSignature as error:
            raise ValueError("Invalid policy change signature.") from error
        head, digest, policy = elections.digest(statement), new_digest, new
        heads.append(head)
    return policy, heads


def publication_policy(connection, witnesses, origin):
    existing = connection.execute("SELECT head FROM cope_quorum.witness_policy_anchor WHERE singleton").fetchone()
    path = os.environ.get("COPE_QUORUM_WITNESS_POLICY")
    text = os.environ.get("COPE_QUORUM_WITNESS_POLICY_JSON")
    if not path and not text:
        if existing:
            elections.fail("quorum_history_policy", "The pinned witness policy must be configured before publishing.", 503)
        return None
    try:
        policy, heads = verify_policy_chain(json.loads(Path(path).read_text(encoding="utf-8") if path else text), origin)
        if existing and existing["head"] not in heads:
            raise ValueError("The policy does not extend the previously approved policy.")
    except (OSError, ValueError) as error:
        elections.fail("quorum_history_policy", f"The server's witness policy is invalid: {error}", 503)
    if sorted(witness["public_key"] for witness in witnesses) != sorted(policy["operators"].values()):
        elections.fail("quorum_history_witnesses", "Enable the witnesses in the configured policy before publishing.")
    connection.execute(
        """INSERT INTO cope_quorum.witness_policy_anchor (head) VALUES (?)
           ON CONFLICT (singleton) DO UPDATE SET head = EXCLUDED.head""", (heads[-1],),
    )
    return {"version": VERSION, "policy": elections.digest(policy), "epoch": heads[-1], "threshold": policy["threshold"]}


def hashes(connection, ballot_id):
    return [elections.digest(proof) for proof in elections.proof_rows(connection, ballot_id)]


def history_digest(votes):
    return elections.digest([VERSION, votes])


def statement(origin, manifest, phase, size, digest):
    return ["cope-quorum-history-checkpoint/1", origin, manifest["id"], elections.digest(manifest), phase, size, digest]


def directory(connection):
    now = datetime.now(UTC)
    return {"ballots": [
        {"id": str(row["id"]), "status": "completed" if now >= row["closes_at"] else "open"}
        for row in connection.execute(
            """SELECT id, closes_at FROM cope_quorum.ballots
               WHERE manifest->'vote_history' IS NOT NULL AND opens_at <= ?
               ORDER BY (closes_at > ?) DESC, closes_at DESC""", (now, now),
        )
    ]}


def read_history(connection, ballot_id):
    row = elections.ballot(connection, ballot_id, lock=" FOR SHARE")
    manifest = row["manifest"]
    if not manifest.get("vote_history"):
        elections.fail("quorum_history_missing", "This ballot has no witnessed history.", 404)
    votes = hashes(connection, ballot_id)
    threshold = manifest["vote_history"]["threshold"]
    # Each group is an attestation to one exact ordered prefix. Retain the latest
    # quorum-backed open prefix even when more votes are waiting for witnesses.
    groups = connection.execute(
        """SELECT phase, size, digest, jsonb_object_agg(public_key, signature) AS signatures
           FROM cope_quorum.vote_checkpoints WHERE ballot_id = ?::uuid
           GROUP BY phase, size, digest HAVING count(*) >= ?
           ORDER BY size DESC""", (str(ballot_id), threshold),
    )
    result = {"manifest_digest": elections.digest(manifest), "vote_hashes": votes, "open": None, "closed": None}
    for item in groups:
        phase, size = item["phase"], item["size"]
        if result[phase] is None and size <= len(votes) and (phase == "open" or size == len(votes)):
            if item["digest"] == history_digest(votes[:size]):
                result[phase] = {key: item[key] for key in ("size", "digest", "signatures")}
    return result


def record_checkpoint(connection, ballot_id, origin, submitted):
    row = elections.ballot(connection, ballot_id, lock=" FOR SHARE")
    manifest = row["manifest"]
    if not manifest.get("vote_history"):
        elections.fail("quorum_history_missing", "This ballot has no witnessed history.", 404)
    if submitted.public_key not in {item["public_key"] for item in manifest.get("witnesses", [])}:
        elections.fail("quorum_unknown_witness", "This witness is not assigned to the ballot.", 403)
    now = datetime.now(UTC)
    if now < row["opens_at"] or (submitted.phase == "closed" and now < row["closes_at"]):
        elections.fail("quorum_history_phase", "This checkpoint is outside the ballot phase.")
    votes = hashes(connection, ballot_id)
    if (submitted.size > len(votes) or (submitted.phase == "closed" and submitted.size != len(votes))
            or history_digest(votes[:submitted.size]) != submitted.digest):
        elections.fail("quorum_history_mismatch", "This checkpoint does not match the ordered vote history.")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(submitted.public_key)).verify(
            bytes.fromhex(submitted.signature), elections.canonical(statement(
                origin, manifest, submitted.phase, submitted.size, submitted.digest)),
        )
    except (ValueError, InvalidSignature):
        elections.fail("quorum_bad_signature", "Invalid checkpoint signature.", 422)
    connection.execute(
        """INSERT INTO cope_quorum.vote_checkpoints (ballot_id, phase, size, digest, public_key, signature)
           VALUES (?::uuid, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
        (str(ballot_id), submitted.phase, submitted.size, submitted.digest, submitted.public_key, submitted.signature),
    )
    connection.commit()
    return {"signed": True}
