"""Witness checkpoints over ordered vote-proof hashes. No proofs are exposed while open."""
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

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


class PolicyProposalInput(Input):
    public_keys: list[Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]] = Field(min_length=1, max_length=64)


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


def verify_policy_signatures(policy, statement, signatures):
    keys = set(policy["operators"].values())
    if (not isinstance(signatures, dict) or not signatures.keys() <= keys
            or len(signatures) < policy["threshold"]):
        raise ValueError("The policy decision needs the current threshold of valid witness signatures.")
    message = elections.canonical(statement)
    try:
        for key, signature in signatures.items():
            if not isinstance(signature, str) or not re.fullmatch(r"[a-f0-9]{128}", signature):
                raise ValueError("Invalid policy decision signature.")
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(bytes.fromhex(signature), message)
    except InvalidSignature as error:
        raise ValueError("Invalid policy decision signature.") from error


def parse_policy_chain(data, origin):
    if isinstance(data, dict) and "initial" in data:
        if data.keys() != {"initial", "changes"} or not isinstance(data["changes"], list) or len(data["changes"]) > 128:
            raise ValueError("Invalid witness policy history.")
        policy, changes = validate_policy(data["initial"]), data["changes"]
    else:
        policy, changes = validate_policy(data), []
    digest = elections.digest(policy)
    head = elections.digest(["cope-quorum-witness-policy-anchor/1", origin, digest])
    heads = [head]
    roster = None
    ballots = {}
    epochs = {head: policy}
    for change in changes:
        expected = {"policy", "round", "commits", "ready", "stored"}
        if (not isinstance(change, dict) or change.keys() not in (expected, expected | {"approval"})
                or type(change["round"]) is not int or not 0 <= change["round"] <= 0x7fffffff):
            raise ValueError("Invalid witness policy change.")
        new = validate_policy(change["policy"])
        new_digest = elections.digest(new)
        has_approval = "approval" in change
        if has_approval != (new_digest == digest):
            raise ValueError("Approve governance separately from any change to verifier membership.")
        proposal = new_digest
        if has_approval:
            approval = change["approval"]
            valid_hash = lambda part: isinstance(part, str) and re.fullmatch(r"[0-9a-f]{64}", part)
            if not isinstance(approval, dict) or len(approval) != 1:
                raise ValueError("Invalid governance approval.")
            if "roster" in approval and valid_hash(approval["roster"]) and approval["roster"] != roster:
                roster = approval["roster"]
            elif "ballot" in approval:
                record = approval["ballot"]
                if (not isinstance(record, dict) or record.keys() != {"id", "manifest", "roster"}
                        or not isinstance(record["id"], str)
                        or not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", record["id"])
                        or record["id"] in ballots or not valid_hash(record["manifest"])
                        or not valid_hash(record["roster"]) or record["roster"] != roster):
                    raise ValueError("Invalid, duplicate, or non-current ballot approval.")
                ballots[record["id"]] = {**record, "epoch": head}
            elif "voter_release" not in approval or not valid_hash(approval["voter_release"]):
                raise ValueError("Invalid governance approval.")
            proposal = elections.digest(["cope-quorum-governance/1", new_digest, approval])
        for phase, signatures in (("commit", change["commits"]), ("store", change["stored"])):
            verify_policy_signatures(policy, [f"cope-quorum-policy-{phase}/1", origin, head, change["round"], proposal],
                                     signatures)
        verify_policy_signatures(new, ["cope-quorum-policy-ready/1", origin, head, change["round"], proposal],
                                 change["ready"])
        statement = ["cope-quorum-witness-policy-change/2", origin, head, digest, proposal]
        head, digest, policy = elections.digest(statement), new_digest, new
        heads.append(head)
        epochs[head] = policy
    return policy, heads, roster, ballots, epochs


def verify_policy_chain(data, origin):
    return parse_policy_chain(data, origin)[:2]


def configured_policy(connection, origin):
    return configured_governance(connection, origin)[:2]


def configured_governance(connection, origin):
    existing = connection.execute("SELECT head FROM cope_quorum.witness_policy_anchor WHERE singleton").fetchone()
    path = os.environ.get("COPE_QUORUM_WITNESS_POLICY")
    text = os.environ.get("COPE_QUORUM_WITNESS_POLICY_JSON")
    if not path and not text:
        if existing:
            raise ValueError("The pinned witness policy must be configured before publishing.")
        return None, [], None, {}, {}
    chain = parse_policy_chain(json.loads(Path(path).read_text(encoding="utf-8") if path else text), origin)
    heads = chain[1]
    if existing and existing["head"] not in heads:
        raise ValueError("The policy does not extend the previously approved policy.")
    return chain


def recommended_policy(witnesses, selected_keys, current):
    """Choose the most faults the selected members can tolerate without losing quorum."""
    by_key = {witness.public_key: witness.name for witness in witnesses}
    selected = set(selected_keys)
    if len(selected) != len(selected_keys):
        raise ValueError("Select each verifier only once.")
    if not selected <= by_key.keys():
        raise ValueError("Some selected verifiers are no longer registered. Refresh the list and try again.")

    previous = current["operators"] if current else {}
    operators = {name: key for name, key in previous.items() if key in selected}
    used = set(previous)
    for key in sorted(selected - set(operators.values())):
        base = re.sub(r"[^a-z0-9]+", "-", by_key[key].lower()).strip("-") or "verifier"
        operator = base[:32].rstrip("-")
        suffix = 1
        while operator in used:
            suffix += 1
            tail = f"-{suffix}"
            operator = base[:32 - len(tail)].rstrip("-") + tail
        used.add(operator)
        operators[operator] = key

    size = len(operators)
    fault = (size - 1) // 3
    threshold = (size + fault) // 2 + 1
    return validate_policy({"threshold": threshold, "max_faulty": fault, "operators": operators})


def publication_policy(connection, witnesses, origin, ballot_id, *, propose=False):
    try:
        policy, heads, roster, ballots, epochs = configured_governance(connection, origin)
    except (OSError, ValueError) as error:
        elections.fail("quorum_history_policy", f"The server's witness policy is invalid: {error}", 503)
    if policy is None:
        return None, None, None
    approval = ballots.get(ballot_id)
    if not propose and not approval:
        elections.fail("quorum_ballot_not_approved", "The verifier quorum must approve this ballot and its roster before publication.")
    if propose and approval:
        elections.fail("quorum_ballot_already_approved", "The verifier quorum already approved this ballot ID.")
    epoch = approval["epoch"] if approval else heads[-1]
    policy = epochs[epoch]
    if sorted(witness["public_key"] for witness in witnesses) != sorted(policy["operators"].values()):
        elections.fail("quorum_history_witnesses", "Enable the witnesses in the configured policy before publishing.")
    if len({item["report_origin"] for item in witnesses if item["report_origin"]}) < policy["threshold"]:
        elections.fail("quorum_relay_origins", "Configure a distinct HTTPS report address for at least a quorum of verifiers.")
    if not propose:
        connection.execute(
            """INSERT INTO cope_quorum.witness_policy_anchor (head) VALUES (?)
               ON CONFLICT (singleton) DO UPDATE SET head = EXCLUDED.head""", (heads[-1],),
        )
    return {"version": VERSION, "policy": elections.digest(policy), "epoch": epoch, "threshold": policy["threshold"]}, roster, approval


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
    if now < row["opens_at"] or (submitted.phase == "closed" and now < row["closes_at"] + elections.SETTLEMENT):
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
