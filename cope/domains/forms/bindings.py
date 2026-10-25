from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from cope.application.errors import ApplicationError

from .definition import FormDefinition, compile_definition


class FeedbackProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=65536)
    reply: StrictBool = False
    contact: str | None = None
    attachment: str | None = None


@dataclass(frozen=True)
class BindingArgument:
    name: str
    kinds: tuple[str, ...]
    required: bool = False


@dataclass(frozen=True)
class FormBinding:
    key: str
    policy_revision: str
    arguments: tuple[BindingArgument, ...]
    proposal_model: type[BaseModel]
    permits_anonymous: bool


FEEDBACK = FormBinding(
    key="feedback.v1",
    policy_revision="feedback-review.v1",
    arguments=(BindingArgument("message", ("text", "long_text"), True),
               BindingArgument("reply", ("checkbox",)), BindingArgument("contact", ("email",)),
               BindingArgument("attachment", ("attachment",))),
    proposal_model=FeedbackProposal,
    permits_anonymous=True,
)

BINDINGS = {FEEDBACK.key: FEEDBACK}


def compile_binding(definition: FormDefinition) -> FormBinding:
    compile_definition(definition)
    binding = BINDINGS.get(definition.binding)
    if binding is None:
        raise ApplicationError("binding_unavailable", "This form binding has no installed implementation.", 422)
    if definition.audience == "public" and not binding.permits_anonymous:
        raise ApplicationError("anonymous_binding_denied", "This binding requires an authenticated response.", 422)
    fields = {field.id: field for field in definition.fields}
    arguments = {argument.name: argument for argument in binding.arguments}
    errors = []
    for name, field_id in definition.mapping.items():
        argument, field = arguments.get(name), fields.get(field_id)
        if argument is None:
            errors.append({"path": ["mapping", name], "code": "protected_or_unknown_argument", "message": "This binding does not accept that argument."})
        elif field is None or field.kind not in argument.kinds:
            errors.append({"path": ["mapping", name], "code": "mapping_type", "message": "Choose a field with a compatible type."})
        elif argument.required and (not field.required or field.when is not None or field.nullable):
            errors.append({"path": ["mapping", name], "code": "required_mapping", "message": "Required binding inputs need unconditional required answers."})
    for argument in binding.arguments:
        if argument.required and argument.name not in definition.mapping:
            errors.append({"path": ["mapping", argument.name], "code": "missing_mapping", "message": "Map this required binding input."})
    if errors:
        raise ApplicationError("invalid_binding_mapping", "Resolve the form binding mappings before publication.", 422, fields=errors)
    return binding


def proposal_inputs(definition: FormDefinition, canonical: dict[str, Any]) -> BaseModel:
    binding = compile_binding(definition)
    arguments = {name: canonical[field_id] for name, field_id in definition.mapping.items() if field_id in canonical}
    result = binding.proposal_model.model_validate(arguments)
    if isinstance(result, FeedbackProposal) and result.reply and not result.contact:
        raise ApplicationError("reply_contact_required", "Provide contact details when requesting a reply.", 422)
    return result


def review_subject(definition: FormDefinition, canonical: dict[str, Any], *, form_id: str, release_id: str, owner_actor: str) -> dict:
    binding = compile_binding(definition)
    proposal = proposal_inputs(definition, canonical)
    subject = {"binding": binding.key, "policy_revision": binding.policy_revision,
               "form_id": form_id, "release_id": release_id, "owner_actor": owner_actor,
               "proposal": proposal.model_dump(mode="json"), "review_evidence": canonical}
    digest = hashlib.sha256(json.dumps(subject, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return {"digest": digest, "subject": subject}
