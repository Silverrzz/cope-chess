export interface ApplicationInputs {
  'agents.set_drain.v1': { "draining": boolean }
  'artifacts.abandon_upload.v1': Record<string, never>
  'artifacts.begin_upload.v1': { "purpose"?: "form_attachment"; "release_id": string; "subject": string; "field_path": Array<string>; "response_id"?: string | null; "filename": string; "size": number; "digest": string; "media_type"?: "text/plain" | "application/octet-stream" }
  'artifacts.finalize_upload.v1': Record<string, never>
  'competitions.rename.v1': { "name": string }
  'engines.update_metadata.v1': { "name": string; "author": string }
  'forms.amend.v1': { "set"?: Record<string, unknown>; "clear"?: Array<string> }
  'forms.close.v1': { "closed": boolean }
  'forms.create.v1': { "slug": string; "label": string; "document"?: Record<string, unknown> }
  'forms.drafts.archive.v1': { "archived": boolean }
  'forms.drafts.create.v1': { "document"?: Record<string, unknown> | null }
  'forms.drafts.save.v1': { "document": Record<string, unknown> }
  'forms.permissions.set.v1': { "actor": string; "permissions": Array<"read" | "edit" | "review" | "responses"> }
  'forms.publish.v1': { "copy_id": string; "copy_revision": number; "keep_previous_hours"?: number; "state_version": number }
  'forms.review.v1': { "state_version": number; "subject_digest": string; "decision": "accepted" | "declined" | "changes_requested"; "reason"?: string }
  'forms.submit.v1': { "release_id": string; "answers": Record<string, unknown>; "attachment_subject"?: string | null }
  'forms.withdraw.v1': Record<string, never>
}

export type ApplicationCommand = keyof ApplicationInputs

export const applicationCommandContracts = {
  'agents.set_drain.v1': { requiresTarget: true, requiresVersion: true },
  'artifacts.abandon_upload.v1': { requiresTarget: true, requiresVersion: false },
  'artifacts.begin_upload.v1': { requiresTarget: true, requiresVersion: false },
  'artifacts.finalize_upload.v1': { requiresTarget: true, requiresVersion: false },
  'competitions.rename.v1': { requiresTarget: true, requiresVersion: true },
  'engines.update_metadata.v1': { requiresTarget: true, requiresVersion: true },
  'forms.amend.v1': { requiresTarget: true, requiresVersion: true },
  'forms.close.v1': { requiresTarget: true, requiresVersion: true },
  'forms.create.v1': { requiresTarget: false, requiresVersion: false },
  'forms.drafts.archive.v1': { requiresTarget: true, requiresVersion: true },
  'forms.drafts.create.v1': { requiresTarget: true, requiresVersion: true },
  'forms.drafts.save.v1': { requiresTarget: true, requiresVersion: true },
  'forms.permissions.set.v1': { requiresTarget: true, requiresVersion: true },
  'forms.publish.v1': { requiresTarget: true, requiresVersion: true },
  'forms.review.v1': { requiresTarget: true, requiresVersion: true },
  'forms.submit.v1': { requiresTarget: true, requiresVersion: false },
  'forms.withdraw.v1': { requiresTarget: true, requiresVersion: true },
} as const

export interface ApplicationOutputs {
  'agents.set_drain.v1': { "draining": boolean; "active_games": "continue" }
  'artifacts.abandon_upload.v1': { "upload_id": string; "state": string; "expires_at": string; "transfer_url": string; "artifact_id": string | null }
  'artifacts.begin_upload.v1': { "upload_id": string; "state": string; "expires_at": string; "transfer_url": string; "artifact_id": string | null }
  'artifacts.finalize_upload.v1': { "artifact_id": string; "descriptor_revision": number; "digest": string; "size": number; "media_type": string; "validation_policy": string }
  'competitions.rename.v1': { "name": string }
  'engines.update_metadata.v1': { "name": string; "author": string }
  'forms.amend.v1': { "response_id": string; "form_id": string; "revision": number }
  'forms.close.v1': { "form_id": string; "closed": boolean }
  'forms.create.v1': { "form_id": string; "copy_id": string; "copy_revision": number; "form_version": number }
  'forms.drafts.archive.v1': { "copy_id": string; "archived": boolean }
  'forms.drafts.create.v1': { "form_id": string; "copy_id": string; "copy_revision": number; "form_version": number }
  'forms.drafts.save.v1': { "form_id": string; "copy_id": string; "copy_revision": number; "form_version": number }
  'forms.permissions.set.v1': { "form_id": string; "actor": string; "permissions": Array<string> }
  'forms.publish.v1': { "form_id": string; "release_id": string; "release_number": number; "slug": string }
  'forms.review.v1': { "response_id": string; "review_id": string; "revision": number; "decision": string }
  'forms.submit.v1': { "response_id": string; "form_id": string; "revision": number }
  'forms.withdraw.v1': { "response_id": string; "withdrawn": boolean }
}

export interface ApplicationQueryInputs {
  'activity.detail.v1': Record<string, never>
  'activity.legacy.detail.v1': Record<string, never>
  'activity.legacy.inspect.v1': { "kind"?: "operation" | "job" | null; "source"?: string | null; "state"?: "planned" | "blocked" | "queued" | "leased" | "exported" | "running" | "waiting" | "cancelling" | "succeeded" | "failed" | "cancelled" | "unknown" | null; "limit"?: number; "cursor"?: string | null }
  'activity.list.v1': { "kind"?: "operation" | "job" | null; "source"?: string | null; "state"?: "planned" | "blocked" | "queued" | "leased" | "exported" | "running" | "waiting" | "cancelling" | "succeeded" | "failed" | "cancelled" | "unknown" | null; "limit"?: number; "cursor"?: string | null }
  'activity.sources.v1': Record<string, never>
  'events.status.v1': Record<string, never>
  'forms.bindings.v1': Record<string, never>
  'forms.datetime_options.v1': { "local_date": string; "local_time": string; "timezone": string }
  'forms.definition.check.v1': { "document": Record<string, unknown> }
  'forms.detail.v1': Record<string, never>
  'forms.drafts.detail.v1': Record<string, never>
  'forms.list.v1': { "limit"?: number; "cursor"?: string | null; "form_id"?: string | null }
  'forms.permissions.v1': Record<string, never>
  'forms.public.v1': { "slug": string }
  'forms.responses.detail.v1': Record<string, never>
  'forms.responses.list.v1': { "limit"?: number; "cursor"?: string | null; "form_id"?: string | null }
}

export interface ApplicationQueryOutputs {
  'activity.detail.v1': { "id": string; "kind": "operation" | "job"; "source": string; "source_id": string; "title": string; "state": "planned" | "blocked" | "queued" | "leased" | "exported" | "running" | "waiting" | "cancelling" | "succeeded" | "failed" | "cancelled" | "unknown"; "source_state": string; "owner_actor": string | null; "execution_owner": string; "inspect_url": string; "ownership_mode": "legacy_observed"; "mapping_quality": "status_only"; "cleanup_state": "not_recorded"; "observed_at": string; "source_created_at": string | null; "content_revision": number; "state_version": number }
  'activity.legacy.detail.v1': { "id": string; "kind": "operation" | "job"; "source": string; "source_id": string; "title": string; "state": "planned" | "blocked" | "queued" | "leased" | "exported" | "running" | "waiting" | "cancelling" | "succeeded" | "failed" | "cancelled" | "unknown"; "source_state": string; "owner_actor": string | null; "execution_owner": string; "inspect_url": string; "ownership_mode": "legacy_observed"; "mapping_quality": "status_only"; "cleanup_state": "not_recorded"; "observed_at": string; "source_created_at": string | null; "content_revision": number; "state_version": number }
  'activity.legacy.inspect.v1': { "items": Array<ApplicationSchema_query_output_activity_legacy_inspect_v1_ActivityItem>; "count": ApplicationSchema_query_output_activity_legacy_inspect_v1_Count; "next_cursor": string | null; "basis": ApplicationSchema_query_output_activity_legacy_inspect_v1_ActivityBasis }
  'activity.list.v1': { "items": Array<ApplicationSchema_query_output_activity_list_v1_ActivityItem>; "count": ApplicationSchema_query_output_activity_list_v1_Count; "next_cursor": string | null; "basis": ApplicationSchema_query_output_activity_list_v1_ActivityBasis }
  'activity.sources.v1': { "items": Array<ApplicationSchema_query_output_activity_sources_v1_SourceItem> }
  'events.status.v1': { "incarnation": string; "schema_version": number; "retained_after": string; "last_delivery_position": string; "last_delivered_at": string | null; "pending_source": ApplicationSchema_query_output_events_status_v1_BoundedCount; "consumers": Array<ApplicationSchema_query_output_events_status_v1_ConsumerStatus>; "consumers_truncated": boolean; "basis"?: "single_statement_inspector" }
  'forms.bindings.v1': { "items": Array<ApplicationSchema_query_output_forms_bindings_v1_BindingOutput> }
  'forms.datetime_options.v1': { "timezone": string; "options": Array<ApplicationSchema_query_output_forms_datetime_options_v1_DateTimeChoice> }
  'forms.definition.check.v1': { "definition": Record<string, unknown> }
  'forms.detail.v1': Record<string, unknown>
  'forms.drafts.detail.v1': Record<string, unknown>
  'forms.list.v1': { "items": Array<Record<string, unknown>>; "total": number; "next_cursor": string | null; "basis": Record<string, unknown> }
  'forms.permissions.v1': { "state_version": number; "owner_actor": string; "grants": Array<Record<string, unknown>>; "identities": Array<Record<string, unknown>> }
  'forms.public.v1': Record<string, unknown>
  'forms.responses.detail.v1': Record<string, unknown>
  'forms.responses.list.v1': { "items": Array<Record<string, unknown>>; "total": number; "next_cursor": string | null; "basis": Record<string, unknown> }
}

export type ApplicationQuery = keyof ApplicationQueryInputs

export type ApplicationSchema_query_output_activity_legacy_inspect_v1_ActivityBasis = { "observed_before": string; "membership": "current_at_request"; "order": "observed_at_desc_id_desc" }
export type ApplicationSchema_query_output_activity_legacy_inspect_v1_ActivityItem = { "id": string; "kind": "operation" | "job"; "source": string; "source_id": string; "title": string; "state": "planned" | "blocked" | "queued" | "leased" | "exported" | "running" | "waiting" | "cancelling" | "succeeded" | "failed" | "cancelled" | "unknown"; "source_state": string; "owner_actor": string | null; "execution_owner": string; "inspect_url": string; "ownership_mode": "legacy_observed"; "mapping_quality": "status_only"; "cleanup_state": "not_recorded"; "observed_at": string; "source_created_at": string | null; "content_revision": number; "state_version": number }
export type ApplicationSchema_query_output_activity_legacy_inspect_v1_Count = { "count": number; "more": boolean; "limit"?: number }
export type ApplicationSchema_query_output_activity_list_v1_ActivityBasis = { "observed_before": string; "membership": "current_at_request"; "order": "observed_at_desc_id_desc" }
export type ApplicationSchema_query_output_activity_list_v1_ActivityItem = { "id": string; "kind": "operation" | "job"; "source": string; "source_id": string; "title": string; "state": "planned" | "blocked" | "queued" | "leased" | "exported" | "running" | "waiting" | "cancelling" | "succeeded" | "failed" | "cancelled" | "unknown"; "source_state": string; "owner_actor": string | null; "execution_owner": string; "inspect_url": string; "ownership_mode": "legacy_observed"; "mapping_quality": "status_only"; "cleanup_state": "not_recorded"; "observed_at": string; "source_created_at": string | null; "content_revision": number; "state_version": number }
export type ApplicationSchema_query_output_activity_list_v1_Count = { "count": number; "more": boolean; "limit"?: number }
export type ApplicationSchema_query_output_activity_sources_v1_SourceItem = { "key": string; "label": string; "kind": string; "execution_owner": string; "inspect_url": string; "ownership_mode": "legacy_observed" }
export type ApplicationSchema_query_output_events_status_v1_BoundedCount = { "count": number; "more": boolean; "limit"?: number }
export type ApplicationSchema_query_output_events_status_v1_ConsumerStatus = { "key": string; "registered": boolean; "state": "current" | "pending" | "backoff" | "recovery_required" | "unavailable" | "not_started"; "applied_position": string; "pending": ApplicationSchema_query_output_events_status_v1_BoundedCount; "failure_count": number; "error": string | null; "retry_at": string | null; "updated_at": string | null }
export type ApplicationSchema_query_output_forms_bindings_v1_BindingArgumentOutput = { "name": string; "label": string; "kinds": Array<string>; "required": boolean }
export type ApplicationSchema_query_output_forms_bindings_v1_BindingOutput = { "key": string; "label": string; "policy_revision": string; "permits_anonymous": boolean; "arguments": Array<ApplicationSchema_query_output_forms_bindings_v1_BindingArgumentOutput> }
export type ApplicationSchema_query_output_forms_datetime_options_v1_DateTimeChoice = { "value": string; "label": string; "utc": string }
