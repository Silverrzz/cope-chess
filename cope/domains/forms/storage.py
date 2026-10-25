from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid

from pydantic import ValidationError

from cope.application.contracts import Target
from cope.application.errors import ApplicationError
from cope.application.identity import Principal, current_principal

from .bindings import compile_binding, proposal_inputs, review_subject
from .definition import FormDefinition
from .responses import ResponseContext, validate_response


def identity(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ApplicationError("invalid_target", "Supply a resource UUID.", 422) from exc


def new_object(connection, type_key: str) -> str:
    return str(connection.execute("INSERT INTO application_objects(type_key) VALUES (%s) RETURNING id", (type_key,)).fetchone()["id"])


def form_permission(connection, principal: Principal, form: dict, permission: str) -> None:
    if principal.credential_kind == "form_claim":
        raise ApplicationError("permission_denied", "This form action is not permitted.", 403)
    if "*" in principal.scopes or principal.actor == form["owner_actor"]:
        return
    allowed = ("read", "edit", "review") if permission == "read" else ("responses", "review") if permission == "responses" else (permission,)
    row = connection.execute("SELECT 1 FROM form_permissions WHERE form_id = %s AND actor = %s AND permission = ANY(%s)",
                             (form["id"], principal.actor, list(allowed))).fetchone()
    if row is None:
        raise ApplicationError("permission_denied", "This form action is not permitted.", 403)


def get_form(connection, target: str, *, lock: bool = False) -> dict:
    row = connection.execute("SELECT * FROM form_definitions WHERE id = %s" + (" FOR UPDATE" if lock else " FOR SHARE"), (identity(target),)).fetchone()
    if row is None:
        raise ApplicationError("not_found", "The form does not exist.", 404)
    return dict(row)


def object_version(connection, target: str, channel: str, *, lock: bool) -> int:
    if channel not in {"content_revision", "state_version"}:
        raise RuntimeError("Unsupported Object revision channel")
    row = connection.execute(f"SELECT {channel} AS version FROM application_objects WHERE id = %s" + (" FOR UPDATE" if lock else " FOR SHARE"), (target,)).fetchone()
    if row is None:
        raise ApplicationError("identity_mismatch", "The resource identity is inconsistent.")
    return row["version"]


def form_resolver(permission: str, channel: str = "content_revision"):
    def resolve(connection, principal, target, *, lock):
        form = get_form(connection, target, lock=lock)
        form_permission(connection, principal, form, permission)
        version = object_version(connection, str(form["id"]), channel, lock=lock)
        return Target(str(form["id"]), version, data=form)
    return resolve


def resolve_copy(connection, principal, target, *, lock):
    row = connection.execute("SELECT form_id FROM form_working_copies WHERE id = %s", (identity(target),)).fetchone()
    if row is None:
        raise ApplicationError("not_found", "The private working copy is not available.", 404)
    form = get_form(connection, str(row["form_id"]))
    row = connection.execute("SELECT * FROM form_working_copies WHERE id = %s" + (" FOR UPDATE" if lock else " FOR SHARE"), (identity(target),)).fetchone()
    if row is None or row["owner_actor"] != principal.actor:
        raise ApplicationError("not_found", "The private working copy is not available.", 404)
    form_permission(connection, principal, form, "edit")
    return Target(str(row["id"]), object_version(connection, str(row["id"]), "content_revision", lock=lock), data=dict(row))


def response_access(connection, principal, response: dict, permission: str = "responses") -> dict:
    form = get_form(connection, str(response["form_id"]))
    if principal.actor == response["owner_actor"]:
        if principal.credential_kind == "form_claim" and principal.resource_scope != str(form["id"]):
            raise ApplicationError("permission_denied", "This response is outside the claim's scope.", 403)
        return form
    form_permission(connection, principal, form, permission)
    return form


def response_resolver(permission: str, channel: str = "content_revision", *, owner_only: bool = False):
    def resolve(connection, principal, target, *, lock):
        basis = connection.execute("SELECT form_id FROM form_responses WHERE id = %s", (identity(target),)).fetchone()
        if basis is None:
            raise ApplicationError("not_found", "The response does not exist.", 404)
        form = get_form(connection, str(basis["form_id"]))
        row = connection.execute("SELECT * FROM form_responses WHERE id = %s" + (" FOR UPDATE" if lock else " FOR SHARE"), (identity(target),)).fetchone()
        if row is None:
            raise ApplicationError("not_found", "The response does not exist.", 404)
        if owner_only and row["owner_actor"] != principal.actor:
            raise ApplicationError("permission_denied", "Only the submitter can amend these answers.", 403)
        if permission == "review":
            form_permission(connection, principal, form, "review")
        else:
            response_access(connection, principal, dict(row))
        return Target(str(row["id"]), object_version(connection, str(row["id"]), channel, lock=lock), data=dict(row))
    return resolve


def definition_from_json(document: dict) -> FormDefinition:
    try:
        definition = FormDefinition.model_validate(document)
    except ValidationError as exc:
        fields = [{"path": list(error["loc"]), "code": error["type"], "message": error["msg"]}
                  for error in exc.errors(include_input=False, include_context=False, include_url=False)]
        raise ApplicationError("invalid_form_definition", "Resolve the form definition errors before publication.", 422, fields=fields) from exc
    compile_binding(definition)
    return definition


def checkpoint_document(document: dict) -> None:
    try:
        encoded = json.dumps(document, ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError) as exc:
        raise ApplicationError("invalid_draft", "The draft must contain supported JSON values.", 422) from exc
    if len(encoded) > 1048576:
        raise ApplicationError("draft_too_large", "The draft exceeds the one MiB limit.", 413)


def create_copy(connection, principal, form_id: str, basis_version: int, document: dict) -> str:
    checkpoint_document(document)
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("form-drafts:" + principal.actor,))
    count = connection.execute("SELECT count(*) AS count FROM form_working_copies WHERE owner_actor = %s AND NOT archived", (principal.actor,)).fetchone()["count"]
    if count >= 100:
        raise ApplicationError("draft_limit", "This actor has reached the private working-copy limit.")
    copy_id = new_object(connection, "form_working_copy")
    connection.execute("INSERT INTO form_working_copies(id, form_id, owner_actor) VALUES (%s, %s, %s)", (copy_id, form_id, principal.actor))
    connection.execute(
        "INSERT INTO form_working_copy_revisions(copy_id, revision, basis_version, document, actor) VALUES (%s, 1, %s, %s::jsonb, %s)",
        (copy_id, basis_version, json.dumps(document), principal.actor),
    )
    return copy_id


def pinned_release(connection, form_id: str, release_id: str, *, accepting: bool) -> dict:
    row = connection.execute(
        """SELECT r.*, a.accepting, a.valid_until,
           (a.accepting AND (a.valid_until IS NULL OR a.valid_until > clock_timestamp())) AS eligible
           FROM form_releases r JOIN form_release_acceptance a ON a.release_id = r.id
           WHERE r.form_id = %s AND r.id = %s FOR SHARE OF a""", (form_id, identity(release_id)),
    ).fetchone()
    if row is None:
        raise ApplicationError("release_not_found", "The form release is not available.", 404)
    if accepting and not row["eligible"]:
        raise ApplicationError("form_release_closed", "This form release has closed. Keep your draft and review the current form.")
    return dict(row)


def take_rate(connection, scope: str, subject: str, limit: int) -> None:
    row = connection.execute(
        """INSERT INTO form_rate_limits(scope, subject, hour, count)
           VALUES (%s, %s, date_trunc('hour', clock_timestamp()), 1)
           ON CONFLICT(scope, subject, hour) DO UPDATE SET count = form_rate_limits.count + 1
           WHERE form_rate_limits.count < %s RETURNING count""", (scope, subject, limit),
    ).fetchone()
    if row is None:
        raise ApplicationError("form_rate_limit", "This form's hourly limit has been reached. Try again in the next hour.", 429)


def issue_claim(connection, slug: str, remote_address: str) -> dict:
    form = connection.execute("SELECT * FROM form_definitions WHERE slug = %s FOR SHARE", (slug,)).fetchone()
    if form is None or form["closed"] or form["published_release"] is None:
        raise ApplicationError("form_unavailable", "The form is not accepting submissions.", 404)
    release = pinned_release(connection, str(form["id"]), str(form["published_release"]), accepting=True)
    definition = definition_from_json(release["definition"])
    if definition.audience != "public":
        raise ApplicationError("authentication_required", "This form requires an authenticated invitation or account.", 401)
    secret_key = connection.execute("SELECT secret FROM form_private_keys WHERE purpose = 'anonymous_rate_key'").fetchone()["secret"]
    subject = hmac.new(secret_key.encode(), remote_address.encode(), hashlib.sha256).hexdigest()
    take_rate(connection, "claim:" + str(form["id"]), subject, 10)
    token = "cope_form_" + secrets.token_urlsafe(40)
    row = connection.execute(
        """INSERT INTO form_claims(form_id, token_hash, expires_at)
           VALUES (%s, %s, clock_timestamp() + interval '30 days') RETURNING id, expires_at""",
        (form["id"], hashlib.sha256(token.encode()).hexdigest()),
    ).fetchone()
    return {"id": str(row["id"]), "form_id": str(form["id"]), "token": token, "expires_at": row["expires_at"].isoformat()}


def authenticate_claim(connection, token: str) -> Principal:
    if not token.startswith("cope_form_") or not 40 <= len(token) <= 100:
        raise ApplicationError("invalid_form_claim", "A valid form follow-up claim is required.", 401)
    row = connection.execute(
        "SELECT id, form_id FROM form_claims WHERE token_hash = %s AND revoked_at IS NULL AND expires_at > clock_timestamp()",
        (hashlib.sha256(token.encode()).hexdigest(),),
    ).fetchone()
    if row is None:
        raise ApplicationError("invalid_form_claim", "This form follow-up claim expired or was revoked.", 401)
    return Principal(f"form-claim:{row['id']}", ("forms:submit", "forms:amend", "forms:responses:read"), str(row["id"]), "form_claim", str(row["form_id"]))


def validate_answers(connection, principal, form: dict, release: dict, answers: dict, *, attachment_subject: str | None = None,
                     attachments: list | None = None, response_id: str | None = None) -> tuple[dict, dict, dict]:
    definition = definition_from_json(release["definition"])
    if release["policy_revision"] != compile_binding(definition).policy_revision:
        raise ApplicationError("binding_policy_changed", "This release needs a new approved binding policy.")

    def resolve_reference(field, resource_id):
        if "*" not in principal.scopes:
            raise PermissionError()
        row = connection.execute("SELECT id FROM application_objects WHERE id = %s AND type_key = %s", (resource_id, field.reference_type)).fetchone()
        if row is None:
            raise LookupError()
        return str(row["id"])

    def resolve_attachment(field, artifact_id, path):
        from cope.domains.artifacts.retention import resolve_attachment as resolve

        selected, retention = resolve(connection,principal,form,release,attachment_subject,field,artifact_id,path,response_id=response_id)
        if attachments is not None:
            attachments.append(retention)
        return selected

    canonical = validate_response(definition, answers, ResponseContext(resolve_reference=resolve_reference, resolve_scoped_attachment=resolve_attachment))
    proposal = proposal_inputs(definition, canonical).model_dump(mode="json")
    subject = review_subject(definition, canonical, form_id=str(form["id"]), release_id=str(release["id"]), owner_actor=form["owner_actor"])
    return canonical, proposal, subject


def insert_response_revision(connection, response_id: str, revision: int, principal, answers: dict, proposal: dict, subject: dict) -> None:
    connection.execute(
        """INSERT INTO form_response_revisions(response_id, revision, answers, proposal, subject, subject_digest, actor)
           VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s)""",
        (response_id, revision, json.dumps(answers), json.dumps(proposal), json.dumps(subject["subject"]), subject["digest"], principal.actor),
    )


def response_view(connection, principal, response_id: str, *, require_scope: bool = True) -> dict:
    principal = current_principal(connection, principal)
    if require_scope:
        principal.require("forms:responses:read")
    target = response_resolver("read")(connection, principal, response_id, lock=False)
    response = target.data
    form = response_access(connection, principal, dict(response))
    revision = connection.execute("SELECT * FROM form_response_revisions WHERE response_id = %s AND revision = %s", (response["id"], response["current_revision"])).fetchone()
    reviews = connection.execute(
        "SELECT id, response_revision, subject_digest, decision, reason, created_at FROM form_reviews WHERE response_id = %s ORDER BY created_at DESC, id DESC LIMIT 100",
        (response["id"],),
    ).fetchall()
    latest = next((review for review in reviews if review["response_revision"] == response["current_revision"] and review["subject_digest"] == revision["subject_digest"]), None)
    state = "withdrawn" if response["withdrawn_at"] else latest["decision"] if latest else "awaiting_review"
    actor = "submitter" if state == "changes_requested" else "reviewer" if state == "awaiting_review" else None
    release = pinned_release(connection, str(form["id"]), str(response["release_id"]), accepting=False)
    definition = FormDefinition.model_validate(release["definition"])
    can_review = False
    if "*" in principal.scopes or "forms:review" in principal.scopes:
        try:
            form_permission(connection, principal, form, "review")
            binding = compile_binding(definition)
            can_review = bool(response["withdrawn_at"] is None and binding.policy_revision == revision["subject"]["policy_revision"]
                              and form["owner_actor"] == revision["subject"]["owner_actor"])
        except ApplicationError:
            pass
    return {"id": str(response["id"]), "form_id": str(form["id"]), "slug": form["slug"], "release_id": str(response["release_id"]),
            "revision": response["current_revision"], "state_version": object_version(connection, response_id, "state_version", lock=False),
            "state": state, "next_actor": actor, "answers": revision["answers"], "subject_digest": revision["subject_digest"],
            "definition": public_definition(definition), "can_amend": bool(release["eligible"] and definition.allow_amendments
                and ("*" in principal.scopes or "forms:amend" in principal.scopes)
                and response["owner_actor"] == principal.actor and response["withdrawn_at"] is None), "can_review": can_review,
            "reviews": [{**dict(review), "id": str(review["id"]), "created_at": review["created_at"].isoformat()} for review in reviews],
            "can_withdraw": bool(response["owner_actor"] == principal.actor and response["withdrawn_at"] is None
                                 and ("*" in principal.scopes or "forms:amend" in principal.scopes)),
            "review_context": {"binding": revision["subject"]["binding"], "policy_revision": revision["subject"]["policy_revision"],
                               "proposal": revision["proposal"]} if can_review else None,
            "attachment_subject": str(response["attachment_subject"]) if response["owner_actor"] == principal.actor else None,
            "attachment_policy": attachment_policy(),
            "created_at": response["created_at"].isoformat()}


def public_definition(definition: FormDefinition) -> dict:
    keys = {"schema_version", "title", "description", "fields", "audience", "allow_amendments", "max_payload_bytes", "max_rows"}
    return {key: value for key, value in definition.model_dump(mode="json").items() if key in keys}


def attachment_policy() -> dict:
    import os
    from cope.domains.artifacts.policy import MEDIA_POLICIES

    return {"max_file_bytes": int(os.environ.get("COPE_ARTIFACT_UPLOAD_MAX_BYTES","10485760")),"media_types":list(MEDIA_POLICIES)}


def public_form_view(connection, slug: str, principal: Principal | None = None) -> dict:
    form = connection.execute("SELECT id FROM form_definitions WHERE slug = %s", (slug,)).fetchone()
    if form is None:
        raise ApplicationError("form_unavailable", "The form is not available.", 404)
    form = get_form(connection, str(form["id"]))
    if form["closed"] or form["published_release"] is None:
        raise ApplicationError("form_unavailable", "The form is not accepting submissions.", 404)
    release = pinned_release(connection, str(form["id"]), str(form["published_release"]), accepting=True)
    definition = definition_from_json(release["definition"])
    if definition.audience != "public":
        if principal is None or principal.credential_kind == "form_claim":
            raise ApplicationError("authentication_required", "Sign in to open this form.", 401)
        principal = current_principal(connection, principal)
        principal.require("forms:submit")
        if definition.audience == "invited":
            form_permission(connection, principal, form, "read")
    return {"id": str(form["id"]), "slug": form["slug"], "release_id": str(release["id"]), "release_number": release["number"],
            "definition": public_definition(definition), "attachment_policy": attachment_policy()}
