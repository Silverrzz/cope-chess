export type FormKind = 'text' | 'long_text' | 'email' | 'integer' | 'decimal' | 'date' | 'time' | 'datetime' | 'choice' | 'multiple_choice' | 'checkbox' | 'group' | 'attachment' | 'reference'

export interface FormChoice { id: string; label: string }
export interface FormField {
  id: string
  kind: FormKind
  label: string
  help?: string
  required?: boolean
  nullable?: boolean
  min_length?: number
  max_length?: number
  minimum?: number | string | null
  maximum?: number | string | null
  precision?: number
  unit?: string
  timezone?: string | null
  choices?: FormChoice[]
  when?: { field: string; equals: string | boolean } | null
  fields?: FormField[]
  min_items?: number
  max_items?: number
  reference_type?: string | null
  accepted_content_types?: string[]
  max_bytes?: number
}

export interface FormDefinition {
  schema_version?: 1
  title: string
  description?: string
  fields: FormField[]
  binding?: string
  mapping?: Record<string, string>
  audience: 'public' | 'authenticated' | 'invited'
  allow_amendments?: boolean
  max_payload_bytes?: number
  max_rows?: number
  submissions_per_hour?: number
}

export type FormAnswers = Record<string, unknown>
export type FormErrors = Record<string, string[]>

export interface PublicForm {
  id: string
  slug: string
  release_id: string
  release_number: number
  definition: FormDefinition
  attachment_policy: { max_file_bytes: number; media_types: string[] }
}

export interface FormResponse {
  id: string
  form_id: string
  slug: string
  release_id: string
  revision: number
  state_version: number
  state: 'awaiting_review' | 'accepted' | 'declined' | 'changes_requested' | 'withdrawn'
  next_actor: 'submitter' | 'reviewer' | null
  answers: FormAnswers
  subject_digest: string
  definition: FormDefinition
  can_amend: boolean
  can_review: boolean
  can_withdraw: boolean
  attachment_subject: string | null
  attachment_policy: { max_file_bytes: number; media_types: string[] }
  review_context: { binding: string; policy_revision: string; proposal: Record<string, unknown> } | null
  reviews: { id: string; decision: string; reason: string; response_revision: number; created_at: string }[]
  created_at: string
}

export interface FormWorkingCopy {
  object_id: string
  version: number
  form_id: string
  document: Partial<FormDefinition>
  basis_version: number
  archived: boolean
}

export interface FormDetail {
  id: string
  slug: string
  label: string
  closed: boolean
  published_release: string | null
  content_revision: number
  state_version: number
  releases: { id: string; number: number; definition: FormDefinition; created_at: string; accepting: boolean; valid_until: string | null }[]
  copies: { id: string; current_revision: number; archived: boolean; updated_at: string; state_version: number }[]
}

export interface Page<T> {
  items: T[]
  total: number
  next_cursor: string | null
  basis: { created_before: string; membership: string; order: string }
}

export function fieldActive(field: FormField, answers: FormAnswers): boolean {
  if (!field.when) return true
  const control = answers[field.when.field]
  return typeof control === typeof field.when.equals && control === field.when.equals
}

export function canonicalAnswers(fields: FormField[], answers: FormAnswers): FormAnswers {
  const result: FormAnswers = {}
  for (const field of fields) {
    if (!fieldActive(field, answers) || !(field.id in answers)) continue
    const value = answers[field.id]
    if (field.kind === 'group' && Array.isArray(value)) {
      result[field.id] = value.map((row) => {
        const item = row as FormAnswers
        return { _row_id: item._row_id, ...canonicalAnswers(field.fields ?? [], item) }
      })
    } else result[field.id] = value
  }
  return result
}

export function initialAnswers(fields: FormField[], existing: FormAnswers = {}): FormAnswers {
  const result = { ...existing }
  for (const field of fields) if (field.kind === 'checkbox' && !(field.id in result)) result[field.id] = false
  return result
}
