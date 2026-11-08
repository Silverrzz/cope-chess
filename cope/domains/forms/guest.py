from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
import uuid
from datetime import timedelta

from cope.application.errors import ApplicationError

from .storage import definition_from_json, pinned_release, take_rate

DIFFICULTY = 14
LIFETIME_SECONDS = 300
NONCE = re.compile(r"[0-9a-f]{32}")
SIGNATURE = re.compile(r"[0-9a-f]{64}")


def _public_release(connection, slug: str, release_id: str) -> tuple[dict, int]:
    form = connection.execute("SELECT * FROM form_definitions WHERE slug = %s FOR SHARE", (slug,)).fetchone()
    if form is None or form["closed"] or form["published_release"] is None:
        raise ApplicationError("form_unavailable", "This form is not accepting submissions.", 404)
    release = pinned_release(connection, str(form["id"]), release_id, accepting=True)
    definition = definition_from_json(release["definition"])
    if definition.audience != "public":
        raise ApplicationError("authentication_required", "This form requires an authenticated invitation or account.", 401)
    return release, definition.submissions_per_hour


def _guest_key(connection, address: str) -> tuple[bytes, str, int]:
    row = connection.execute(
        """SELECT secret, floor(extract(epoch FROM clock_timestamp()))::bigint AS now
           FROM form_private_keys WHERE purpose = 'anonymous_rate_key'"""
    ).fetchone()
    secret = row["secret"].encode()
    try:
        ip = ipaddress.ip_address(address)
        if isinstance(ip, ipaddress.IPv6Address):
            address = str(ip.ipv4_mapped or ipaddress.ip_network((ip, 64), strict=False).network_address)
        else:
            address = str(ip)
    except ValueError:
        address = "unknown"
    bucket = hmac.new(secret, f"network:{address}".encode(), hashlib.sha256).hexdigest()
    return secret, bucket, row["now"]


def _signature(secret: bytes, release_id: str, bucket: str, challenge: str) -> str:
    return hmac.new(secret, f"form-proof:v1:{release_id}:{bucket}:{challenge}".encode(), hashlib.sha256).hexdigest()


def issue_challenge(connection, slug: str, address: str, release_id: str) -> dict:
    release, _ = _public_release(connection, slug, release_id)
    secret, bucket, now = _guest_key(connection, address)
    take_rate(connection, "guest-challenge", bucket, 60)
    take_rate(connection, "guest-challenge", "platform", 3000)
    challenge = f"{secrets.token_hex(16)}.{now + LIFETIME_SECONDS}"
    token = f"{challenge}.{_signature(secret, str(release['id']), bucket, challenge)}"
    return {"challenge": token, "difficulty": DIFFICULTY}


def redeem_challenge(connection, slug: str, address: str, release_id: str, challenge: str, answer: int) -> dict:
    release, limit = _public_release(connection, slug, release_id)
    secret, bucket, now = _guest_key(connection, address)
    nonce, _, tail = challenge.partition(".")
    expires, _, signature = tail.partition(".")
    if (not NONCE.fullmatch(nonce) or not SIGNATURE.fullmatch(signature)
            or not re.fullmatch(r"[0-9]{1,12}", expires) or not now < int(expires) <= now + LIFETIME_SECONDS
            or not hmac.compare_digest(signature, _signature(secret, str(release["id"]), bucket, f"{nonce}.{expires}"))):
        raise ApplicationError("guest_challenge_expired", "Guest verification expired. Please try again.", 409)
    digest = hashlib.sha256(f"{challenge}:{answer}".encode()).digest()
    if int.from_bytes(digest[:2]) >> (16 - DIFFICULTY):
        raise ApplicationError("guest_challenge_invalid", "Guest verification did not complete. Please try again.", 422)

    token = "cope_form_" + hmac.new(secret, f"form-claim:v1:{challenge}".encode(), hashlib.sha256).hexdigest()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    challenge_id = str(uuid.UUID(nonce))
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"form-proof:{challenge_id}",))
    row = connection.execute("SELECT id, expires_at, created_at FROM form_claims WHERE challenge_id = %s", (challenge_id,)).fetchone()
    if row is None:
        take_rate(connection, "guest-claim", bucket, 24)
        take_rate(connection, f"guest-claim:{release['form_id']}", bucket, min(12, limit))
        take_rate(connection, "guest-claim", "platform", 600)
        row = connection.execute(
            """INSERT INTO form_claims(form_id, token_hash, expires_at, challenge_id, release_id, guest_bucket)
               VALUES (%s, %s, clock_timestamp() + interval '30 days', %s, %s, %s)
               RETURNING id, expires_at, created_at""",
            (release["form_id"], token_hash, challenge_id, release["id"], bucket),
        ).fetchone()
    return {
        "id": str(row["id"]), "form_id": str(release["form_id"]), "token": token,
        "expires_at": row["expires_at"].isoformat(),
        "submit_until": (row["created_at"] + timedelta(hours=2)).isoformat(),
    }


def guest_admission(connection, principal, release_id: str, *, lock: bool = False) -> dict:
    row = connection.execute(
        """SELECT guest_bucket, release_id, created_at > clock_timestamp() - interval '2 hours' AS recent
           FROM form_claims WHERE id = %s AND revoked_at IS NULL AND expires_at > clock_timestamp()"""
        + (" FOR UPDATE" if lock else ""),
        (principal.token_id,),
    ).fetchone()
    if row is None or row["guest_bucket"] is None or str(row["release_id"]) != str(release_id) or not row["recent"]:
        raise ApplicationError("guest_verification_required", "Verify again to start a response to this release.", 409)
    if connection.execute("SELECT 1 FROM form_responses WHERE owner_actor = %s LIMIT 1", (principal.actor,)).fetchone():
        raise ApplicationError("guest_claim_used", "This guest session has already submitted a response.", 409)
    return row


def reserve_guest_upload(connection, principal, form_id: str, release_id: str, size: int, response_id: str | None) -> None:
    if response_id is None:
        bucket = guest_admission(connection, principal, release_id, lock=True)["guest_bucket"]
    else:
        row = connection.execute("SELECT guest_bucket FROM form_claims WHERE id = %s", (principal.token_id,)).fetchone()
        bucket = row["guest_bucket"] if row and row["guest_bucket"] else principal.actor
    take_rate(connection, "guest-upload-files", principal.actor, 8)
    take_rate(connection, "guest-upload-bytes", bucket, 40 * 1024 * 1024, cost=size)
    take_rate(connection, f"guest-upload:{form_id}", "form", 128 * 1024 * 1024, cost=size)
    take_rate(connection, "guest-upload-bytes", "platform", 384 * 1024 * 1024, cost=size)


def charge_guest_transfer(connection, principal, size: int) -> None:
    row = connection.execute("SELECT guest_bucket FROM form_claims WHERE id = %s", (principal.token_id,)).fetchone()
    bucket = row["guest_bucket"] if row and row["guest_bucket"] else principal.actor
    take_rate(connection, "guest-transfer-bytes", bucket, 64 * 1024 * 1024, cost=size)
    take_rate(connection, "guest-transfer-bytes", "platform", 512 * 1024 * 1024, cost=size)
