export interface ApplicationInputs {
  'agents.set_drain.v1': { "draining": boolean }
  'artifacts.abandon_upload.v1': Record<string, never>
  'artifacts.begin_upload.v1': { "purpose"?: "form_attachment"; "release_id": string; "subject": string; "field_path": Array<string>; "response_id"?: string | null; "filename": string; "size": number; "digest": string; "media_type"?: "text/plain" | "application/octet-stream" }
  'artifacts.finalize_upload.v1': Record<string, never>
  'competitions.rename.v1': { "name": string }
  'engines.prepare_artifact.v1': { "engine_version_id": number; "expected_build_hash": string; "expected_archive_sha256": string; "runtime_id": string }
  'engines.update_metadata.v1': { "name": string; "author": string }
  'execution.runtimes.register.v1': { "image_digest": string; "source_revision": string }
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
  'jobs.legacy_hold.v1': { "held": boolean; "reason": string }
  'operations.cancel.v1': { "reason": string }
  'operations.legacy_hold.v1': { "held": boolean; "reason": string }
}

export type ApplicationCommand = keyof ApplicationInputs

export const applicationCommandContracts = {
  'agents.set_drain.v1': { requiresTarget: true, requiresVersion: true },
  'artifacts.abandon_upload.v1': { requiresTarget: true, requiresVersion: false },
  'artifacts.begin_upload.v1': { requiresTarget: true, requiresVersion: false },
  'artifacts.finalize_upload.v1': { requiresTarget: true, requiresVersion: false },
  'competitions.rename.v1': { requiresTarget: true, requiresVersion: true },
  'engines.prepare_artifact.v1': { requiresTarget: true, requiresVersion: false },
  'engines.update_metadata.v1': { requiresTarget: true, requiresVersion: true },
  'execution.runtimes.register.v1': { requiresTarget: false, requiresVersion: false },
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
  'jobs.legacy_hold.v1': { requiresTarget: true, requiresVersion: true },
  'operations.cancel.v1': { requiresTarget: true, requiresVersion: true },
  'operations.legacy_hold.v1': { requiresTarget: true, requiresVersion: true },
} as const

export interface ApplicationOutputs {
  'agents.set_drain.v1': { "draining": boolean; "active_games": "continue" }
  'artifacts.abandon_upload.v1': { "upload_id": string; "state": string; "expires_at": string; "transfer_url": string; "artifact_id": string | null }
  'artifacts.begin_upload.v1': { "upload_id": string; "state": string; "expires_at": string; "transfer_url": string; "artifact_id": string | null }
  'artifacts.finalize_upload.v1': { "artifact_id": string; "descriptor_revision": number; "digest": string; "size": number; "media_type": string; "validation_policy": string }
  'competitions.rename.v1': { "name": string }
  'engines.prepare_artifact.v1': { "operation_id": string; "job_id": string; "admission"?: "accepted"; "initial_job_state"?: "blocked" }
  'engines.update_metadata.v1': { "name": string; "author": string }
  'execution.runtimes.register.v1': { "image_digest": string; "source_revision": string; "runtime_id": string; "activation"?: "pending" }
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
  'jobs.legacy_hold.v1': { "id": string; "kind": "operation" | "job"; "source": string; "source_id": string; "backend": "legacy"; "held": boolean; "ownership_generation": number; "source_present": boolean; "source_state": string | null; "cleanup_state"?: "not_recorded"; "canonical_transfer_supported"?: false }
  'operations.cancel.v1': { "operation_id": string; "status": "cancelled"; "cancelled_jobs": number; "continuing_jobs": number; "cleanup"?: "no_attempt_started" }
  'operations.legacy_hold.v1': { "id": string; "kind": "operation" | "job"; "source": string; "source_id": string; "backend": "legacy"; "held": boolean; "ownership_generation": number; "source_present": boolean; "source_state": string | null; "cleanup_state"?: "not_recorded"; "canonical_transfer_supported"?: false }
}

export interface ApplicationQueryInputs {
  'activity.detail.v1': Record<string, never>
  'activity.legacy.detail.v1': Record<string, never>
  'activity.legacy.inspect.v1': { "kind"?: "operation" | "job" | null; "source"?: string | null; "state"?: "planned" | "blocked" | "queued" | "leased" | "exported" | "running" | "waiting" | "cancelling" | "succeeded" | "failed" | "cancelled" | "unknown" | null; "limit"?: number; "cursor"?: string | null }
  'activity.list.v1': { "kind"?: "operation" | "job" | null; "source"?: string | null; "state"?: "planned" | "blocked" | "queued" | "leased" | "exported" | "running" | "waiting" | "cancelling" | "succeeded" | "failed" | "cancelled" | "unknown" | null; "limit"?: number; "cursor"?: string | null }
  'activity.sources.v1': Record<string, never>
  'events.status.v1': Record<string, never>
  'execution.jobs.detail.v1': Record<string, never>
  'execution.legacy_route.v1': { "limit"?: number; "before_generation"?: number | null }
  'execution.operations.detail.v1': Record<string, never>
  'execution.runtimes.v1': Record<string, never>
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
  'objects.removal.v1': Record<string, never>
}

export interface ApplicationQueryOutputs {
  'activity.detail.v1': { "id": string; "kind": "operation" | "job"; "source": string; "source_id": string; "title": string; "state": "planned" | "blocked" | "queued" | "leased" | "exported" | "running" | "waiting" | "cancelling" | "succeeded" | "failed" | "cancelled" | "unknown"; "source_state": string; "owner_actor": string | null; "execution_owner": string; "execution_route": "legacy" | "held" | "canonical"; "ownership_generation": number; "inspect_url": string; "ownership_mode": "legacy_observed" | "canonical"; "mapping_quality": "status_only" | "authoritative"; "cleanup_state": "not_recorded" | "not_started"; "observed_at": string; "source_created_at": string | null; "content_revision": number; "state_version": number }
  'activity.legacy.detail.v1': { "id": string; "kind": "operation" | "job"; "source": string; "source_id": string; "title": string; "state": "planned" | "blocked" | "queued" | "leased" | "exported" | "running" | "waiting" | "cancelling" | "succeeded" | "failed" | "cancelled" | "unknown"; "source_state": string; "owner_actor": string | null; "execution_owner": string; "execution_route": "legacy" | "held" | "canonical"; "ownership_generation": number; "inspect_url": string; "ownership_mode": "legacy_observed" | "canonical"; "mapping_quality": "status_only" | "authoritative"; "cleanup_state": "not_recorded" | "not_started"; "observed_at": string; "source_created_at": string | null; "content_revision": number; "state_version": number }
  'activity.legacy.inspect.v1': { "items": Array<ApplicationSchema_query_output_activity_legacy_inspect_v1_ActivityItem>; "count": ApplicationSchema_query_output_activity_legacy_inspect_v1_Count; "next_cursor": string | null; "basis": ApplicationSchema_query_output_activity_legacy_inspect_v1_ActivityBasis }
  'activity.list.v1': { "items": Array<ApplicationSchema_query_output_activity_list_v1_ActivityItem>; "count": ApplicationSchema_query_output_activity_list_v1_Count; "next_cursor": string | null; "basis": ApplicationSchema_query_output_activity_list_v1_ActivityBasis }
  'activity.sources.v1': { "items": Array<ApplicationSchema_query_output_activity_sources_v1_SourceItem> }
  'events.status.v1': { "incarnation": string; "schema_version": number; "retained_after": string; "last_delivery_position": string; "last_delivered_at": string | null; "pending_source": ApplicationSchema_query_output_events_status_v1_BoundedCount; "consumers": Array<ApplicationSchema_query_output_events_status_v1_ConsumerStatus>; "consumers_truncated": boolean; "basis"?: "single_statement_inspector" }
  'execution.jobs.detail.v1': { "id": string; "workload": string; "status": "blocked" | "cancelled"; "state_version": number; "readiness": ApplicationSchema_query_output_execution_jobs_detail_v1_BlockReason | null; "cleanup"?: "no_attempt_started"; "kind": "job"; "operation_id": string; "created_at": string; "not_after": string; "input": ApplicationSchema_query_output_execution_jobs_detail_v1_JobInputView | null; "authority_mode"?: "on_behalf_of_caller"; "completion_policy"?: "finish_admitted_pure_work" }
  'execution.legacy_route.v1': { "id": string; "kind": "operation" | "job"; "source": string; "source_id": string; "backend": "legacy"; "held": boolean; "ownership_generation": number; "source_present": boolean; "source_state": string | null; "cleanup_state"?: "not_recorded"; "canonical_transfer_supported"?: false; "history": Array<ApplicationSchema_query_output_execution_legacy_route_v1_RouteHistory>; "next_generation": number | null }
  'execution.operations.detail.v1': { "kind": "operation"; "id": string; "command": string; "status": "waiting" | "cancelled"; "state_version": number; "created_at": string; "jobs": Array<ApplicationSchema_query_output_execution_operations_detail_v1_JobSummary>; "may_cancel": boolean }
  'execution.runtimes.v1': { "items": Array<ApplicationSchema_query_output_execution_runtimes_v1_RuntimeItem>; "truncated": boolean }
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
  'objects.removal.v1': { "id": string; "type_key": string; "source": string; "source_id": string; "reason": "source_deleted" | "source_missing"; "observed_at": string; "state_version": number; "cleanup_state": "not_recorded"; "restore_supported": false }
}

export type ApplicationQuery = keyof ApplicationQueryInputs

export interface ApplicationViewInputs {
  'activity.list.v1': { "kind"?: "operation" | "job" | null; "source"?: string | null; "state"?: "planned" | "blocked" | "queued" | "leased" | "exported" | "running" | "waiting" | "cancelling" | "succeeded" | "failed" | "cancelled" | "unknown" | null; "limit"?: number; "cursor"?: string | null }
  'legacy.activity.detail.v1': Record<string, never>
  'legacy.activity.list.v1': { "kind"?: "operation" | "job" | null; "source"?: string | null; "state"?: "planned" | "blocked" | "queued" | "leased" | "exported" | "running" | "waiting" | "cancelling" | "succeeded" | "failed" | "cancelled" | "unknown" | null; "limit"?: number; "cursor"?: string | null }
}

export const applicationViewContracts = {
  'activity.list.v1': { requiresTarget: false },
  'legacy.activity.detail.v1': { requiresTarget: true },
  'legacy.activity.list.v1': { requiresTarget: false },
} as const

export interface ApplicationViewOutputs {
  'activity.list.v1': { "items": Array<ApplicationSchema_view_output_activity_list_v1_ActivityItem>; "count": ApplicationSchema_view_output_activity_list_v1_Count; "next_cursor": string | null; "basis": ApplicationSchema_view_output_activity_list_v1_ActivityBasis }
  'legacy.activity.detail.v1': { "id": string; "kind": "operation" | "job"; "source": string; "source_id": string; "title": string; "state": "planned" | "blocked" | "queued" | "leased" | "exported" | "running" | "waiting" | "cancelling" | "succeeded" | "failed" | "cancelled" | "unknown"; "source_state": string; "owner_actor": string | null; "execution_owner": string; "execution_route": "legacy" | "held" | "canonical"; "ownership_generation": number; "inspect_url": string; "ownership_mode": "legacy_observed" | "canonical"; "mapping_quality": "status_only" | "authoritative"; "cleanup_state": "not_recorded" | "not_started"; "observed_at": string; "source_created_at": string | null; "content_revision": number; "state_version": number }
  'legacy.activity.list.v1': { "items": Array<ApplicationSchema_view_output_legacy_activity_list_v1_ActivityItem>; "count": ApplicationSchema_view_output_legacy_activity_list_v1_Count; "next_cursor": string | null; "basis": ApplicationSchema_view_output_legacy_activity_list_v1_ActivityBasis }
}

export type ApplicationView = keyof ApplicationViewInputs

export type ApplicationViewSnapshot = { "view": string; "context": string; "schema_version": 1; "refresh_mode": "invalidation"; "data": Record<string, unknown>; "checkpoint": string; "expires_at": string }

export type ApplicationViewBatch = { "view": string; "context": string; "schema_version": 1; "previous_checkpoint": string; "checkpoint": string; "invalidated": boolean; "has_more": boolean; "expires_at": string }

export type ApplicationSchema_query_output_activity_legacy_inspect_v1_ActivityBasis = { "observed_before": string; "membership": "current_at_request"; "order": "observed_at_desc_id_desc" }
export type ApplicationSchema_query_output_activity_legacy_inspect_v1_ActivityItem = { "id": string; "kind": "operation" | "job"; "source": string; "source_id": string; "title": string; "state": "planned" | "blocked" | "queued" | "leased" | "exported" | "running" | "waiting" | "cancelling" | "succeeded" | "failed" | "cancelled" | "unknown"; "source_state": string; "owner_actor": string | null; "execution_owner": string; "execution_route": "legacy" | "held" | "canonical"; "ownership_generation": number; "inspect_url": string; "ownership_mode": "legacy_observed" | "canonical"; "mapping_quality": "status_only" | "authoritative"; "cleanup_state": "not_recorded" | "not_started"; "observed_at": string; "source_created_at": string | null; "content_revision": number; "state_version": number }
export type ApplicationSchema_query_output_activity_legacy_inspect_v1_Count = { "count": number; "more": boolean; "limit"?: number }
export type ApplicationSchema_query_output_activity_list_v1_ActivityBasis = { "observed_before": string; "membership": "current_at_request"; "order": "observed_at_desc_id_desc" }
export type ApplicationSchema_query_output_activity_list_v1_ActivityItem = { "id": string; "kind": "operation" | "job"; "source": string; "source_id": string; "title": string; "state": "planned" | "blocked" | "queued" | "leased" | "exported" | "running" | "waiting" | "cancelling" | "succeeded" | "failed" | "cancelled" | "unknown"; "source_state": string; "owner_actor": string | null; "execution_owner": string; "execution_route": "legacy" | "held" | "canonical"; "ownership_generation": number; "inspect_url": string; "ownership_mode": "legacy_observed" | "canonical"; "mapping_quality": "status_only" | "authoritative"; "cleanup_state": "not_recorded" | "not_started"; "observed_at": string; "source_created_at": string | null; "content_revision": number; "state_version": number }
export type ApplicationSchema_query_output_activity_list_v1_Count = { "count": number; "more": boolean; "limit"?: number }
export type ApplicationSchema_query_output_activity_sources_v1_SourceItem = { "key": string; "label": string; "kind": string; "execution_owner": string; "inspect_url": string; "ownership_mode": "legacy_observed" | "canonical" }
export type ApplicationSchema_query_output_events_status_v1_BoundedCount = { "count": number; "more": boolean; "limit"?: number }
export type ApplicationSchema_query_output_events_status_v1_ConsumerStatus = { "key": string; "registered": boolean; "state": "current" | "pending" | "backoff" | "recovery_required" | "unavailable" | "not_started"; "applied_position": string; "pending": ApplicationSchema_query_output_events_status_v1_BoundedCount; "failure_count": number; "error": string | null; "retry_at": string | null; "updated_at": string | null }
export type ApplicationSchema_query_output_execution_jobs_detail_v1_BlockReason = { "code": "control_plane_unavailable"; "message": string }
export type ApplicationSchema_query_output_execution_jobs_detail_v1_JobInputView = { "engine_id": string; "engine_version_id": number; "build_hash": string; "archive_sha256": string; "archive_bytes": number; "profile": string; "runtime_id": string; "runtime_image": string; "runtime_source": string; "specification_digest": string; "cpu_millis": number; "memory_bytes": number; "scratch_bytes": number; "timeout_seconds": number }
export type ApplicationSchema_query_output_execution_legacy_route_v1_RouteHistory = { "generation": number; "backend": string; "held": boolean; "changed_by": string; "reason": string; "changed_at": string }
export type ApplicationSchema_query_output_execution_operations_detail_v1_BlockReason = { "code": "control_plane_unavailable"; "message": string }
export type ApplicationSchema_query_output_execution_operations_detail_v1_JobSummary = { "id": string; "workload": string; "status": "blocked" | "cancelled"; "state_version": number; "readiness": ApplicationSchema_query_output_execution_operations_detail_v1_BlockReason | null; "cleanup"?: "no_attempt_started" }
export type ApplicationSchema_query_output_execution_runtimes_v1_RuntimeItem = { "id": string; "image_digest": string; "source_revision": string; "workload": string; "profile": string; "activation"?: "pending" }
export type ApplicationSchema_query_output_forms_bindings_v1_BindingArgumentOutput = { "name": string; "label": string; "kinds": Array<string>; "required": boolean }
export type ApplicationSchema_query_output_forms_bindings_v1_BindingOutput = { "key": string; "label": string; "policy_revision": string; "permits_anonymous": boolean; "arguments": Array<ApplicationSchema_query_output_forms_bindings_v1_BindingArgumentOutput> }
export type ApplicationSchema_query_output_forms_datetime_options_v1_DateTimeChoice = { "value": string; "label": string; "utc": string }
export type ApplicationSchema_view_output_activity_list_v1_ActivityBasis = { "observed_before": string; "membership": "current_at_request"; "order": "observed_at_desc_id_desc" }
export type ApplicationSchema_view_output_activity_list_v1_ActivityItem = { "id": string; "kind": "operation" | "job"; "source": string; "source_id": string; "title": string; "state": "planned" | "blocked" | "queued" | "leased" | "exported" | "running" | "waiting" | "cancelling" | "succeeded" | "failed" | "cancelled" | "unknown"; "source_state": string; "owner_actor": string | null; "execution_owner": string; "execution_route": "legacy" | "held" | "canonical"; "ownership_generation": number; "inspect_url": string; "ownership_mode": "legacy_observed" | "canonical"; "mapping_quality": "status_only" | "authoritative"; "cleanup_state": "not_recorded" | "not_started"; "observed_at": string; "source_created_at": string | null; "content_revision": number; "state_version": number }
export type ApplicationSchema_view_output_activity_list_v1_Count = { "count": number; "more": boolean; "limit"?: number }
export type ApplicationSchema_view_output_legacy_activity_list_v1_ActivityBasis = { "observed_before": string; "membership": "current_at_request"; "order": "observed_at_desc_id_desc" }
export type ApplicationSchema_view_output_legacy_activity_list_v1_ActivityItem = { "id": string; "kind": "operation" | "job"; "source": string; "source_id": string; "title": string; "state": "planned" | "blocked" | "queued" | "leased" | "exported" | "running" | "waiting" | "cancelling" | "succeeded" | "failed" | "cancelled" | "unknown"; "source_state": string; "owner_actor": string | null; "execution_owner": string; "execution_route": "legacy" | "held" | "canonical"; "ownership_generation": number; "inspect_url": string; "ownership_mode": "legacy_observed" | "canonical"; "mapping_quality": "status_only" | "authoritative"; "cleanup_state": "not_recorded" | "not_started"; "observed_at": string; "source_created_at": string | null; "content_revision": number; "state_version": number }
export type ApplicationSchema_view_output_legacy_activity_list_v1_Count = { "count": number; "more": boolean; "limit"?: number }
