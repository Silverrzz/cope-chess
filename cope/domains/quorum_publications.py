from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field

from cope.application.contracts import Input
from cope.domains import quorum_elections as elections


class WitnessSignature(Input):
    public_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    signature: str = Field(pattern=r"^[a-f0-9]{128}$")


class DisclosureProof(elections.Proof):
    message: str = Field(pattern=r"^(0|[1-9][0-9]{0,77})$")


class Disclosure(Input):
    commitment: str = Field(pattern=r"^[1-9][0-9]{0,76}$")
    proof: DisclosureProof


def closed_evidence(connection, ballot_id):
    evidence = elections.ballot_package(connection, ballot_id)
    if evidence["status"] != "completed":
        elections.fail("quorum_ballot_open", "Available after voting closes.")
    return evidence


def publications(connection, ballot_id):
    evidence = closed_evidence(connection, ballot_id)
    digest = elections.digest(evidence)
    signed = {
        row["public_key"]: row["signed_at"]
        for row in connection.execute(
            """SELECT public_key, signed_at FROM cope_quorum.witness_signatures
               WHERE ballot_id = ?::uuid AND digest = ?""", (str(ballot_id), digest)
        )
    }
    witnesses = [
        {**item, "signed_at": signed.get(item["public_key"])}
        for item in evidence["manifest"].get("witnesses", [])
    ]
    options = evidence["result"]["options"]
    disclosures = [
        {"username": row["username"], "option": options[int(row["message"])]}
        for row in connection.execute(
            """SELECT d.username, v.proof->>'message' AS message
               FROM cope_quorum.vote_disclosures d JOIN cope_quorum.votes v USING (ballot_id, nullifier)
               WHERE d.ballot_id = ?::uuid ORDER BY d.published_at, d.nullifier""", (str(ballot_id),)
        )
    ]
    return {"certified": bool(witnesses) and all(item["signed_at"] for item in witnesses),
            "witnesses": witnesses, "disclosures": disclosures}


def record_signature(connection, ballot_id, origin, submitted):
    evidence = closed_evidence(connection, ballot_id)
    witnesses = evidence["manifest"].get("witnesses", [])
    if not any(item["public_key"] == submitted.public_key for item in witnesses):
        elections.fail("quorum_unknown_witness", "This witness was not assigned to the ballot.", 403)
    if elections.digest(evidence) != submitted.digest:
        elections.fail("quorum_witness_digest_mismatch", "The witness checked different ballot evidence.")
    statement = elections.canonical(["cope-quorum-witness-result/1", origin, str(ballot_id), submitted.digest])
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(submitted.public_key)).verify(bytes.fromhex(submitted.signature), statement)
    except (ValueError, InvalidSignature):
        elections.fail("quorum_bad_signature", "Invalid witness signature.", 422)
    connection.execute(
        """INSERT INTO cope_quorum.witness_signatures (ballot_id, public_key, digest, signature)
           VALUES (?::uuid, ?, ?, ?) ON CONFLICT DO NOTHING""",
        (str(ballot_id), submitted.public_key, submitted.digest, submitted.signature),
    )
    connection.commit()
    return {"signed": True}


def disclosure_message(ballot_id, discord_id, session_token):
    return elections.scope(["cope-quorum-disclosure/1", str(ballot_id), discord_id,
                            elections.digest(session_token)])


def validate_disclosure(connection, ballot_id, submitted, message):
    evidence = closed_evidence(connection, ballot_id)
    if submitted.commitment not in evidence["roster"]["members"] or submitted.proof.message != message:
        elections.fail("quorum_disclosure_invalid", "The proof does not match this ballot or Discord session.", 422)
    if not any(vote["nullifier"] == submitted.proof.nullifier for vote in evidence["votes"]):
        elections.fail("quorum_disclosure_missing", "This credential has no recorded vote on this ballot.", 404)
    scope = elections.scope(["cope-quorum-ballot/1", evidence["manifest"]])
    connection.commit()
    if not elections.proof_worker([submitted.commitment], submitted.proof, scope)["valid"]:
        elections.fail("quorum_disclosure_invalid", "Invalid vote ownership proof.", 422)


def record_disclosure(connection, ballot_id, account, nullifier):
    row = connection.execute(
        """INSERT INTO cope_quorum.vote_disclosures (ballot_id, nullifier, account_id, username)
           VALUES (?::uuid, ?, ?, ?) ON CONFLICT DO NOTHING RETURNING nullifier""",
        (str(ballot_id), nullifier, account.id, account.username),
    ).fetchone()
    if row is None:
        existing = connection.execute(
            """SELECT account_id FROM cope_quorum.vote_disclosures
               WHERE ballot_id = ?::uuid AND nullifier = ?""", (str(ballot_id), nullifier),
        ).fetchone()
        if existing is None or existing["account_id"] != account.id:
            elections.fail("quorum_disclosure_exists", "This vote or Discord account already has a public disclosure.")
    connection.commit()
    return {"published": True}
