export interface ApplicationInputs {
  'agents.set_drain.v1': { draining: boolean }
  'artifacts.abandon_upload.v1': Record<string, never>
  'artifacts.begin_upload.v1': { purpose?: string; release_id: string; subject: string; field_path: Array<string>; response_id?: string | null; filename: string; size: number; digest: string; media_type?: "text/plain" | "application/octet-stream" }
  'artifacts.finalize_upload.v1': Record<string, never>
  'competitions.rename.v1': { name: string }
  'engines.update_metadata.v1': { name: string; author: string }
  'forms.amend.v1': { set?: Record<string, unknown>; clear?: Array<string> }
  'forms.close.v1': { closed: boolean }
  'forms.create.v1': { slug: string; label: string; document?: Record<string, unknown> }
  'forms.drafts.archive.v1': { archived: boolean }
  'forms.drafts.create.v1': { document?: Record<string, unknown> | null }
  'forms.drafts.save.v1': { document: Record<string, unknown> }
  'forms.permissions.set.v1': { actor: string; permissions: Array<"read" | "edit" | "review" | "responses"> }
  'forms.publish.v1': { copy_id: string; copy_revision: number; keep_previous_hours?: number; state_version: number }
  'forms.review.v1': { state_version: number; subject_digest: string; decision: "accepted" | "declined" | "changes_requested"; reason?: string }
  'forms.submit.v1': { release_id: string; answers: Record<string, unknown>; attachment_subject?: string | null }
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
  'agents.set_drain.v1': { draining: boolean; active_games: string }
  'artifacts.abandon_upload.v1': { upload_id: string; state: string; expires_at: string; transfer_url: string; artifact_id: string | null }
  'artifacts.begin_upload.v1': { upload_id: string; state: string; expires_at: string; transfer_url: string; artifact_id: string | null }
  'artifacts.finalize_upload.v1': { artifact_id: string; descriptor_revision: number; digest: string; size: number; media_type: string; validation_policy: string }
  'competitions.rename.v1': { name: string }
  'engines.update_metadata.v1': { name: string; author: string }
  'forms.amend.v1': { response_id: string; form_id: string; revision: number }
  'forms.close.v1': { form_id: string; closed: boolean }
  'forms.create.v1': { form_id: string; copy_id: string; copy_revision: number; form_version: number }
  'forms.drafts.archive.v1': { copy_id: string; archived: boolean }
  'forms.drafts.create.v1': { form_id: string; copy_id: string; copy_revision: number; form_version: number }
  'forms.drafts.save.v1': { form_id: string; copy_id: string; copy_revision: number; form_version: number }
  'forms.permissions.set.v1': { form_id: string; actor: string; permissions: Array<string> }
  'forms.publish.v1': { form_id: string; release_id: string; release_number: number; slug: string }
  'forms.review.v1': { response_id: string; review_id: string; revision: number; decision: string }
  'forms.submit.v1': { response_id: string; form_id: string; revision: number }
  'forms.withdraw.v1': { response_id: string; withdrawn: boolean }
}

export interface ApplicationQueryInputs {
  'events.status.v1': Record<string, never>
  'forms.bindings.v1': Record<string, never>
  'forms.datetime_options.v1': { local_date: string; local_time: string; timezone: string }
  'forms.definition.check.v1': { document: Record<string, unknown> }
  'forms.detail.v1': Record<string, never>
  'forms.drafts.detail.v1': Record<string, never>
  'forms.list.v1': { limit?: number; cursor?: string | null; form_id?: string | null }
  'forms.permissions.v1': Record<string, never>
  'forms.public.v1': { slug: string }
  'forms.responses.detail.v1': Record<string, never>
  'forms.responses.list.v1': { limit?: number; cursor?: string | null; form_id?: string | null }
}

export type ApplicationQuery = keyof ApplicationQueryInputs
