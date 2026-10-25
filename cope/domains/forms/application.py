from __future__ import annotations

import hashlib
import json
import uuid
from typing import Literal

from pydantic import Field, StrictBool

from cope.application.contracts import Command, CommandContext, Input, Outcome
from cope.application.errors import ApplicationError

from .bindings import compile_binding
from .storage import (
    checkpoint_document, create_copy, definition_from_json, form_permission, form_resolver, get_form,
    identity, insert_response_revision, new_object, object_version, pinned_release, resolve_copy,
    response_resolver, take_rate, validate_answers,
)


class CreateForm(Input):
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    label: str = Field(min_length=1, max_length=300)
    document: dict = Field(default_factory=dict)


class NewDraft(Input):
    document: dict | None = None


class SaveDraft(Input):
    document: dict


class PublishForm(Input):
    copy_id: str
    copy_revision: int = Field(strict=True, gt=0)
    keep_previous_hours: int = Field(default=0, strict=True, ge=0, le=168)
    state_version: int = Field(strict=True, gt=0)


class CloseForm(Input):
    closed: StrictBool


class ArchiveDraft(Input):
    archived: StrictBool


class ArchiveOutput(Input):
    copy_id: str
    archived: bool


class SubmitResponse(Input):
    release_id: str
    answers: dict


class AmendResponse(Input):
    set: dict = Field(default_factory=dict)
    clear: list[str] = Field(default_factory=list, max_length=100)


class ReviewResponse(Input):
    state_version: int = Field(strict=True, gt=0)
    subject_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    decision: Literal["accepted", "declined", "changes_requested"]
    reason: str = Field(default="", max_length=8000)


class WithdrawResponse(Input):
    pass


class WithdrawOutput(Input):
    response_id: str
    withdrawn: bool


class DraftOutput(Input):
    form_id: str
    copy_id: str
    copy_revision: int
    form_version: int


class ReleaseOutput(Input):
    form_id: str
    release_id: str
    release_number: int
    slug: str


class ClosedOutput(Input):
    form_id: str
    closed: bool


class ResponseOutput(Input):
    response_id: str
    form_id: str
    revision: int


class ReviewOutput(Input):
    response_id: str
    review_id: str
    revision: int
    decision: str


def create_form(context: CommandContext, payload: CreateForm) -> Outcome:
    checkpoint_document(payload.document)
    form_id = new_object(context.connection, "form")
    context.connection.execute("INSERT INTO form_definitions(id, slug, label, owner_actor) VALUES (%s, %s, %s, %s)",
                               (form_id, payload.slug, payload.label, context.principal.actor))
    copy_id = create_copy(context.connection, context.principal, form_id, 1, payload.document)
    return Outcome(form_id, 1, {"form_id": form_id, "copy_id": copy_id, "copy_revision": 1, "form_version": 1})


def new_draft(context: CommandContext, payload: NewDraft) -> Outcome:
    document = payload.document
    if document is None:
        release_id = context.target.data["published_release"]
        document = {} if release_id is None else pinned_release(context.connection, context.target.object_id, str(release_id), accepting=False)["definition"]
    copy_id = create_copy(context.connection, context.principal, context.target.object_id, context.target.version, document)
    return Outcome(context.target.object_id, context.target.version,
                   {"form_id": context.target.object_id, "copy_id": copy_id, "copy_revision": 1, "form_version": context.target.version})


def save_draft(context: CommandContext, payload: SaveDraft) -> Outcome:
    if context.target.data["archived"]:
        raise ApplicationError("draft_archived", "Restore this private working copy before saving changes.")
    checkpoint_document(payload.document)
    row = context.connection.execute("SELECT basis_version FROM form_working_copy_revisions WHERE copy_id = %s AND revision = %s",
                                     (context.target.object_id, context.target.data["current_revision"])).fetchone()
    revision = context.target.version + 1
    context.connection.execute(
        "INSERT INTO form_working_copy_revisions(copy_id, revision, basis_version, document, actor) VALUES (%s, %s, %s, %s::jsonb, %s)",
        (context.target.object_id, revision, row["basis_version"], json.dumps(payload.document), context.principal.actor),
    )
    context.connection.execute("UPDATE form_working_copies SET current_revision = %s, updated_at = clock_timestamp() WHERE id = %s", (revision, context.target.object_id))
    context.connection.execute("UPDATE application_objects SET content_revision = %s WHERE id = %s", (revision, context.target.object_id))
    return Outcome(context.target.object_id, revision, {"form_id": str(context.target.data["form_id"]), "copy_id": context.target.object_id,
                   "copy_revision": revision, "form_version": row["basis_version"]})


def publish_form(context: CommandContext, payload: PublishForm) -> Outcome:
    if object_version(context.connection, context.target.object_id, "state_version", lock=True) != payload.state_version:
        raise ApplicationError("publication_state_changed", "The form's admission state changed. Refresh before publishing.")
    copy_id = identity(payload.copy_id)
    draft = context.connection.execute(
        """SELECT r.document, r.basis_version FROM form_working_copies c
           JOIN form_working_copy_revisions r ON r.copy_id = c.id
           WHERE c.id = %s AND c.form_id = %s AND c.owner_actor = %s AND NOT c.archived AND r.revision = %s FOR SHARE OF c""",
        (copy_id, context.target.object_id, context.principal.actor, payload.copy_revision),
    ).fetchone()
    if draft is None:
        raise ApplicationError("draft_not_found", "The selected saved revision of your private draft is not available.", 404)
    if draft["basis_version"] != context.target.version:
        raise ApplicationError("draft_basis_changed", "The form changed after this draft began. Preserve your work and reconcile it with a new draft.")
    definition = definition_from_json(draft["document"])
    binding = compile_binding(definition)
    document = definition.model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    number = context.connection.execute("SELECT COALESCE(max(number), 0) + 1 AS number FROM form_releases WHERE form_id = %s", (context.target.object_id,)).fetchone()["number"]
    context.connection.execute(
        """UPDATE form_release_acceptance SET accepting = %s,
           valid_until = CASE WHEN %s > 0 THEN LEAST(COALESCE(valid_until, 'infinity'::timestamptz), clock_timestamp() + (%s * interval '1 hour')) ELSE valid_until END,
           changed_by = %s, changed_at = clock_timestamp()
           WHERE release_id IN (SELECT id FROM form_releases WHERE form_id = %s) AND accepting""",
        (payload.keep_previous_hours > 0, payload.keep_previous_hours, payload.keep_previous_hours, context.principal.actor, context.target.object_id),
    )
    release = context.connection.execute(
        """INSERT INTO form_releases(form_id, number, copy_id, copy_revision, definition, definition_digest, binding_key, policy_revision, publisher_actor)
           VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s) RETURNING id""",
        (context.target.object_id, number, copy_id, payload.copy_revision, json.dumps(document), digest, binding.key, binding.policy_revision, context.principal.actor),
    ).fetchone()
    context.connection.execute("INSERT INTO form_release_acceptance(release_id, changed_by) VALUES (%s, %s)", (release["id"], context.principal.actor))
    context.connection.execute("UPDATE form_definitions SET published_release = %s, closed = FALSE WHERE id = %s", (release["id"], context.target.object_id))
    context.connection.execute("UPDATE application_objects SET content_revision = content_revision + 1, state_version = state_version + 1 WHERE id = %s", (context.target.object_id,))
    return Outcome(context.target.object_id, context.target.version + 1,
                   {"form_id": context.target.object_id, "release_id": str(release["id"]), "release_number": number, "slug": context.target.data["slug"]})


def close_form(context: CommandContext, payload: CloseForm) -> Outcome:
    version = context.target.version
    if context.target.data["closed"] != payload.closed:
        context.connection.execute("UPDATE form_definitions SET closed = %s WHERE id = %s", (payload.closed, context.target.object_id))
        context.connection.execute("UPDATE application_objects SET state_version = state_version + 1 WHERE id = %s", (context.target.object_id,))
        version += 1
    return Outcome(context.target.object_id, version, {"form_id": context.target.object_id, "closed": payload.closed})


def resolve_archive(connection, principal, target, *, lock):
    resolved = resolve_copy(connection, principal, target, lock=lock)
    from cope.application.contracts import Target

    return Target(resolved.object_id, object_version(connection, resolved.object_id, "state_version", lock=lock), data=resolved.data)


def archive_draft(context: CommandContext, payload: ArchiveDraft) -> Outcome:
    version = context.target.version
    if context.target.data["archived"] != payload.archived:
        if not payload.archived:
            context.connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("form-drafts:" + context.principal.actor,))
            count = context.connection.execute("SELECT count(*) AS count FROM form_working_copies WHERE owner_actor = %s AND NOT archived", (context.principal.actor,)).fetchone()["count"]
            if count >= 100:
                raise ApplicationError("draft_limit", "Archive another working copy before restoring this one.")
        context.connection.execute("UPDATE form_working_copies SET archived = %s WHERE id = %s", (payload.archived, context.target.object_id))
        context.connection.execute("UPDATE application_objects SET state_version = state_version + 1 WHERE id = %s", (context.target.object_id,))
        version += 1
    return Outcome(context.target.object_id, version, {"copy_id": context.target.object_id, "archived": payload.archived})


def resolve_submission(connection, principal, target, *, lock):
    form = get_form(connection, target, lock=lock)
    if principal.credential_kind == "form_claim" and principal.resource_scope != str(form["id"]):
        raise ApplicationError("permission_denied", "This form is outside the claim's scope.", 403)
    from cope.application.contracts import Target

    return Target(str(form["id"]), None, data=form)


def submit_response(context: CommandContext, payload: SubmitResponse) -> Outcome:
    form = context.target.data
    if form["closed"]:
        raise ApplicationError("form_closed", "This form is closed to new submissions.")
    release = pinned_release(context.connection, context.target.object_id, payload.release_id, accepting=True)
    definition = definition_from_json(release["definition"])
    if definition.audience != "public" and context.principal.credential_kind == "form_claim":
        raise ApplicationError("authentication_required", "This release requires an authenticated response.", 403)
    if definition.audience == "invited":
        form_permission(context.connection, context.principal, form, "read")
    canonical, proposal, subject = validate_answers(context.connection, context.principal, form, release, payload.answers)
    take_rate(context.connection, "response:" + context.target.object_id, context.principal.actor, definition.submissions_per_hour)
    response_id = new_object(context.connection, "form_response")
    context.connection.execute("INSERT INTO form_responses(id, form_id, release_id, owner_actor) VALUES (%s, %s, %s, %s)",
                               (response_id, context.target.object_id, release["id"], context.principal.actor))
    insert_response_revision(context.connection, response_id, 1, context.principal, canonical, proposal, subject)
    return Outcome(response_id, 1, {"response_id": response_id, "form_id": context.target.object_id, "revision": 1})


def amend_response(context: CommandContext, payload: AmendResponse) -> Outcome:
    response = context.target.data
    if response["withdrawn_at"]:
        raise ApplicationError("response_withdrawn", "A withdrawn response cannot be amended.")
    form = get_form(context.connection, str(response["form_id"]))
    release = pinned_release(context.connection, str(form["id"]), str(response["release_id"]), accepting=True)
    definition = definition_from_json(release["definition"])
    if not definition.allow_amendments:
        raise ApplicationError("amendments_closed", "This form does not accept response amendments.")
    if len(set(payload.clear)) != len(payload.clear) or set(payload.clear).intersection(payload.set):
        raise ApplicationError("invalid_patch", "Each field can be set or cleared once.", 422)
    fields = {field.id for field in definition.fields}
    if set(payload.clear) - fields:
        raise ApplicationError("unknown_field", "A clear operation names an unknown form field.", 422)
    previous = context.connection.execute("SELECT answers FROM form_response_revisions WHERE response_id = %s AND revision = %s",
                                         (context.target.object_id, context.target.version)).fetchone()["answers"]
    values = {key: value for key, value in previous.items() if key not in payload.clear}
    values.update(payload.set)
    canonical, proposal, subject = validate_answers(context.connection, context.principal, form, release, values)
    revision = context.target.version
    if canonical != previous:
        revision += 1
        insert_response_revision(context.connection, context.target.object_id, revision, context.principal, canonical, proposal, subject)
        context.connection.execute("UPDATE form_responses SET current_revision = %s WHERE id = %s", (revision, context.target.object_id))
        context.connection.execute("UPDATE application_objects SET content_revision = %s WHERE id = %s", (revision, context.target.object_id))
    return Outcome(context.target.object_id, revision, {"response_id": context.target.object_id, "form_id": str(form["id"]), "revision": revision})


def review_response(context: CommandContext, payload: ReviewResponse) -> Outcome:
    response = context.target.data
    if response["withdrawn_at"]:
        raise ApplicationError("response_withdrawn", "A withdrawn response cannot be admitted.")
    state = object_version(context.connection, context.target.object_id, "state_version", lock=True)
    if state != payload.state_version:
        raise ApplicationError("review_conflict", "Another review changed this response. Refresh before deciding.", current_version=state)
    row = context.connection.execute("SELECT subject, subject_digest FROM form_response_revisions WHERE response_id = %s AND revision = %s",
                                    (context.target.object_id, context.target.version)).fetchone()
    if row["subject_digest"] != payload.subject_digest:
        raise ApplicationError("review_subject_changed", "Review the exact current response before deciding.")
    form = get_form(context.connection, str(response["form_id"]))
    release = pinned_release(context.connection, str(form["id"]), str(response["release_id"]), accepting=False)
    definition = definition_from_json(release["definition"])
    binding = compile_binding(definition)
    if binding.policy_revision != row["subject"]["policy_revision"] or form["owner_actor"] != row["subject"]["owner_actor"]:
        raise ApplicationError("review_policy_changed", "The response needs a new review subject under current ownership and policy.")
    review_id = str(uuid.uuid4())
    context.connection.execute(
        """INSERT INTO form_reviews(id, response_id, response_revision, subject_digest, policy_revision, decision, reason, actor)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (review_id, context.target.object_id, context.target.version, payload.subject_digest, binding.policy_revision, payload.decision, payload.reason, context.principal.actor),
    )
    context.connection.execute("UPDATE application_objects SET state_version = state_version + 1 WHERE id = %s", (context.target.object_id,))
    return Outcome(context.target.object_id, context.target.version,
                   {"response_id": context.target.object_id, "review_id": review_id, "revision": context.target.version, "decision": payload.decision})


def withdraw_response(context: CommandContext, payload: WithdrawResponse) -> Outcome:
    version = context.target.version
    if context.target.data["withdrawn_at"] is None:
        context.connection.execute("UPDATE form_responses SET withdrawn_at = clock_timestamp() WHERE id = %s", (context.target.object_id,))
        context.connection.execute("UPDATE application_objects SET state_version = state_version + 1 WHERE id = %s", (context.target.object_id,))
        version += 1
    return Outcome(context.target.object_id, version, {"response_id": context.target.object_id, "withdrawn": True})


def describe_form(connection, principal, target) -> dict:
    return {"object_id": target.object_id, "version": target.version, "form": {"id": target.object_id, "slug": target.data["slug"], "label": target.data["label"], "closed": target.data["closed"]}}


def describe_copy(connection, principal, target) -> dict:
    revision = connection.execute("SELECT document, basis_version FROM form_working_copy_revisions WHERE copy_id = %s AND revision = %s",
                                  (target.object_id, target.version)).fetchone()
    return {"object_id": target.object_id, "version": target.version, "form_id": str(target.data["form_id"]), **dict(revision)}


def describe_archive(connection, principal, target) -> dict:
    return {"object_id": target.object_id, "version": target.version, "archived": target.data["archived"]}


def describe_response(connection, principal, target) -> dict:
    from .storage import response_view

    return response_view(connection, principal, target.object_id, require_scope=False)


def describe_submission(connection, principal, target) -> dict:
    from .storage import public_form_view

    return public_form_view(connection, target.data["slug"], principal)


def project_form(connection, principal, result):
    form_permission(connection, principal, get_form(connection, result["object_id"]), "read")
    return result


def project_copy(connection, principal, result):
    resolve_copy(connection, principal, result["object_id"], lock=False)
    return result


def project_response(connection, principal, result):
    response_resolver("read")(connection, principal, result["object_id"], lock=False)
    return result


def no_target(*args, **kwargs):
    raise ApplicationError("target_not_applicable", "This creation command has no existing target.", 422)


def commands() -> tuple[Command, ...]:
    return (
        Command("forms.create.v1", "form", "forms:write", None, CreateForm, DraftOutput,
                no_target, create_form, no_target, project_form, False, False),
        Command("forms.drafts.create.v1", "form", "forms:write", "content_revision", NewDraft, DraftOutput,
                form_resolver("edit"), new_draft, describe_form, project_form),
        Command("forms.drafts.save.v1", "form_working_copy", "forms:write", "content_revision", SaveDraft, DraftOutput,
                resolve_copy, save_draft, describe_copy, project_copy),
        Command("forms.drafts.archive.v1", "form_working_copy", "forms:write", "state_version", ArchiveDraft, ArchiveOutput,
                resolve_archive, archive_draft, describe_archive, project_copy),
        Command("forms.publish.v1", "form", "forms:write", "content_revision", PublishForm, ReleaseOutput,
                form_resolver("edit"), publish_form, describe_form, project_form),
        Command("forms.close.v1", "form", "forms:write", "state_version", CloseForm, ClosedOutput,
                form_resolver("edit", "state_version"), close_form, describe_form, project_form),
        Command("forms.submit.v1", "form_response", "forms:submit", None, SubmitResponse, ResponseOutput,
                resolve_submission, submit_response, describe_submission, project_response, True, False),
        Command("forms.amend.v1", "form_response", "forms:amend", "content_revision", AmendResponse, ResponseOutput,
                response_resolver("read", owner_only=True), amend_response, describe_response, project_response),
        Command("forms.review.v1", "form_response", "forms:review", "content_revision", ReviewResponse, ReviewOutput,
                response_resolver("review"), review_response, describe_response, project_response),
        Command("forms.withdraw.v1", "form_response", "forms:amend", "state_version", WithdrawResponse, WithdrawOutput,
                response_resolver("read", "state_version", owner_only=True), withdraw_response, describe_response, project_response),
    )
