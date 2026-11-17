from datetime import UTC, datetime, timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field

from cope.application.contracts import Input
from cope.db import update_deployment_target_status
from cope.db.repo import utc_now
from cope.domains import quorum_elections as elections
from cope.domains.quorum import read_setup


class WitnessSignature(Input):
    public_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    signature: str = Field(pattern=r"^[a-f0-9]{128}$")


class VerifierCheckin(Input):
    public_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    sent_at: int = Field(strict=True, ge=0, le=2**53 - 1)
    last_scan: int | None = Field(ge=0, le=2**53 - 1)
    archived: int = Field(strict=True, ge=0, le=1_000_000)
    scan_errors: int = Field(strict=True, ge=0, le=1_000_000)
    runtime: str = Field(pattern=r"^v[0-9]+\.[0-9]+\.[0-9]+$", max_length=30)
    app_commit: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")
    failed_job: int | None = Field(default=None, strict=True, gt=0)
    signature: str = Field(pattern=r"^[a-f0-9]{128}$")


def record_checkin(connection, origin, submitted, web_commit=None):
    witness = next((item for item in read_setup(connection).witnesses if item.public_key == submitted.public_key), None)
    if witness is None:
        elections.fail("quorum_unknown_witness", "Register this verifier's key in Admin → Quorum.", 403)
    if abs(int(datetime.now(UTC).timestamp()) - submitted.sent_at) > 120 or (
            submitted.last_scan is not None and submitted.last_scan > submitted.sent_at):
        elections.fail("quorum_checkin_expired", "Verifier check-in timestamp is out of range.", 409)
    statement = ["cope-quorum-verifier-checkin/1", origin, submitted.public_key, submitted.sent_at, submitted.last_scan,
                 submitted.archived, submitted.scan_errors, submitted.runtime]
    if submitted.app_commit:
        statement[0] = "cope-quorum-verifier-checkin/2"
        statement.extend((submitted.app_commit, submitted.failed_job))
    elif submitted.failed_job is not None:
        elections.fail("quorum_checkin_invalid", "An update status requires a verifier revision.", 422)
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(submitted.public_key)).verify(
            bytes.fromhex(submitted.signature), elections.canonical(statement)
        )
    except (ValueError, InvalidSignature):
        elections.fail("quorum_bad_signature", "Invalid verifier check-in signature.", 422)
    row = connection.execute(
        """INSERT INTO cope_quorum.verifier_checkins (public_key, sent_at, last_scan, archived, scan_errors, runtime, app_commit)
           VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (public_key) DO UPDATE SET
           sent_at = EXCLUDED.sent_at, seen_at = clock_timestamp(), last_scan = EXCLUDED.last_scan,
           archived = EXCLUDED.archived, scan_errors = EXCLUDED.scan_errors, runtime = EXCLUDED.runtime,
           app_commit = COALESCE(EXCLUDED.app_commit, verifier_checkins.app_commit)
           WHERE EXCLUDED.sent_at > verifier_checkins.sent_at
           RETURNING id""",
        (submitted.public_key, submitted.sent_at, submitted.last_scan, submitted.archived, submitted.scan_errors,
         submitted.runtime, submitted.app_commit),
    ).fetchone()
    release = None
    if row is not None and submitted.app_commit:
        release = verifier_release(connection, row["id"], witness.name, submitted, web_commit)
    connection.commit()
    return {"received": True, "release": release}


def verifier_release(connection, verifier_id, label, submitted, web_commit):
    job = connection.execute(
        """SELECT job.id, job.target_commit AS commit, server.repository_url
           FROM deployment_jobs AS job
           JOIN deployment_targets AS server ON server.job_id = job.id AND server.target_kind = 'server'
           WHERE job.scope = 'platform' AND job.target_commit IS NOT NULL
             AND (job.status = 'succeeded' OR (job.status = 'verifying' AND job.target_commit = ?))
           ORDER BY job.id DESC LIMIT 1""",
        (web_commit,),
    ).fetchone()
    if not job or not job["repository_url"]:
        return None
    connection.execute(
        """INSERT INTO deployment_targets
           (job_id, target_kind, target_id, label, current_commit, target_commit, repository_url, status, updated_at)
           VALUES (?, 'verifier', ?, ?, ?, ?, ?, 'waiting', ?) ON CONFLICT DO NOTHING""",
        (job["id"], verifier_id, label, submitted.app_commit, job["commit"], job["repository_url"], utc_now()),
    )
    target = connection.execute(
        """SELECT id, status FROM deployment_targets
           WHERE job_id = ? AND target_kind = 'verifier' AND target_id = ?""",
        (job["id"], verifier_id),
    ).fetchone()
    if job["commit"] == submitted.app_commit:
        if target["status"] != "succeeded":
            update_deployment_target_status(connection, target["id"], "succeeded", current_commit=submitted.app_commit)
        return None
    if submitted.failed_job == job["id"]:
        if target["status"] != "failed":
            update_deployment_target_status(connection, target["id"], "failed", current_commit=submitted.app_commit,
                                            detail="Verifier could not install this release and kept its previous revision.")
        return None
    if target["status"] != "updating":
        update_deployment_target_status(connection, target["id"], "updating", current_commit=submitted.app_commit)
    return {"job_id": job["id"], "commit": job["commit"], "repository_url": job["repository_url"]}


def verifier_directory(connection):
    setup = read_setup(connection)
    now = datetime.now(UTC)
    checkins = {row["public_key"]: row for row in connection.execute("SELECT * FROM cope_quorum.verifier_checkins")}
    signatures = {
        row["public_key"]: row
        for row in connection.execute("""SELECT public_key, count(*) AS signed_count, max(signed_at) AS last_signed
                                         FROM cope_quorum.witness_signatures GROUP BY public_key""")
    }
    witnesses = []
    for witness in setup.witnesses:
        checkin = checkins.get(witness.public_key)
        signed = signatures.get(witness.public_key, {})
        status = "awaiting_checkin" if not checkin else "online" if now - checkin["seen_at"] <= timedelta(seconds=150) else "offline"
        witnesses.append({**witness.model_dump(), "status": status, "checkin": checkin,
                          "signed_count": signed.get("signed_count", 0), "last_signed": signed.get("last_signed")})
    return {"revision": setup.revision, "witnesses": witnesses}


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
    evidence = elections.ballot_package(connection, ballot_id)
    witnesses = elections.witness_status(connection, evidence)
    if evidence["status"] != "completed":
        return {"certified": False, "witnesses": witnesses, "disclosures": []}
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


def signature_statement(origin, ballot_id, digest):
    return ["cope-quorum-witness-result/1", origin, str(ballot_id), digest]


def witness_report(connection, ballot_id, public_key, origin):
    evidence = closed_evidence(connection, ballot_id)
    witness = next((item for item in evidence["manifest"].get("witnesses", []) if item["public_key"] == public_key), None)
    digest = elections.digest(evidence)
    row = connection.execute(
        """SELECT signature, signed_at FROM cope_quorum.witness_signatures
           WHERE ballot_id = ?::uuid AND public_key = ? AND digest = ?""",
        (str(ballot_id), public_key, digest),
    ).fetchone()
    if witness is None or row is None:
        elections.fail("quorum_report_missing", "No signed report is available for this witness.", 404)
    return {
        "ballot_id": str(ballot_id),
        "title": evidence["manifest"]["title"],
        "witness": witness["name"],
        "public_key": public_key,
        "digest": digest,
        "signature": row["signature"],
        "signed_at": row["signed_at"],
        "statement": signature_statement(origin, ballot_id, digest),
        "result": evidence["result"],
    }


def record_signature(connection, ballot_id, origin, submitted):
    evidence = closed_evidence(connection, ballot_id)
    witnesses = evidence["manifest"].get("witnesses", [])
    if not any(item["public_key"] == submitted.public_key for item in witnesses):
        elections.fail("quorum_unknown_witness", "This witness was not assigned to the ballot.", 403)
    if elections.digest(evidence) != submitted.digest:
        elections.fail("quorum_witness_digest_mismatch", "The witness checked different ballot evidence.")
    statement = elections.canonical(signature_statement(origin, ballot_id, submitted.digest))
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
