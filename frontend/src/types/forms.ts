export type FormKind =
  | 'text' | 'long_text' | 'email' | 'url' | 'phone' | 'integer' | 'decimal'
  | 'date' | 'time' | 'datetime' | 'choice' | 'dropdown' | 'multiple_choice' | 'ranking'
  | 'matrix' | 'checkbox' | 'yes_no' | 'consent' | 'rating' | 'scale' | 'group'
  | 'attachment' | 'reference' | 'chess_game' | 'chess_position' | 'engine' | 'rating_list' | 'section' | 'markdown'

export const contentKinds: ReadonlySet<FormKind> = new Set(['section', 'markdown'])
export const booleanKinds: ReadonlySet<FormKind> = new Set(['checkbox', 'yes_no', 'consent'])

export interface AttachmentPolicy {
  max_file_bytes: number
  media_types: string[]
  guest_max_file_bytes?: number
  guest_extensions?: Record<string, string[]>
}

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
  rows?: FormChoice[]
  content?: string
  when?: FormCondition | null
  conditions?: FormCondition[]
  condition_match?: 'all' | 'any'
  fields?: FormField[]
  min_items?: number
  max_items?: number
  reference_type?: string | null
  accepted_content_types?: string[]
  max_bytes?: number
}

export interface FormCondition {
  field: string
  equals: string | boolean
  operator?: 'is' | 'is_not' | 'includes'
}

export interface ChessPositionAnswer { fen: string; pgn?: string }
export type ResponseVisibility = 'private' | 'summary' | 'individual'
export interface Respondent { username: string; display_name: string }

export interface FormDefinition {
  schema_version?: 1
  title: string
  description?: string
  fields: FormField[]
  binding?: string
  mapping?: Record<string, string>
  audience: 'public' | 'authenticated' | 'invited'
  allow_amendments?: boolean
  anonymous?: boolean
  listed?: boolean
  response_visibility?: ResponseVisibility
  discord_role_id?: string | null
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
  attachment_policy: AttachmentPolicy
  closed: boolean
  submitted?: boolean
  my_response_id?: string | null
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
  attachment_policy: AttachmentPolicy
  review_context: { binding: string; policy_revision: string; proposal: Record<string, unknown> } | null
  reviews: { id: string; decision: string; reason: string; response_revision: number; created_at: string }[]
  created_at: string
  anonymous?: boolean
  respondent?: Respondent | null
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
  copies: { id: string; basis_version: number; current_revision: number; archived: boolean; updated_at: string; state_version: number }[]
}

export interface Page<T> {
  items: T[]
  total: number
  next_cursor: string | null
  basis: { created_before: string; membership: string; order: string }
}

export function fieldConditions(field: FormField): FormCondition[] {
  return field.conditions?.length ? field.conditions : field.when ? [field.when] : []
}

export function fieldActive(field: FormField, answers: FormAnswers, fields: FormField[]): boolean {
  if (!fieldConditions(field).length) return true
  const byId = new Map(fields.map(candidate => [candidate.id, candidate]))
  const memo = new Map<string, boolean>()
  const visiting = new Set<string>()

  function matches(rule: FormCondition): boolean {
    const parent = byId.get(rule.field)
    if (!parent || !visible(parent)) return false
    const value = answers[rule.field]
    if (rule.operator === 'includes') return Array.isArray(value) && value.includes(rule.equals)
    if (typeof value !== typeof rule.equals) return false
    return rule.operator === 'is_not' ? value !== rule.equals : value === rule.equals
  }

  function visible(candidate: FormField): boolean {
    const known = memo.get(candidate.id)
    if (known !== undefined) return known
    if (visiting.has(candidate.id)) return false
    visiting.add(candidate.id)
    const rules = fieldConditions(candidate)
    const shown = !rules.length || (candidate.condition_match === 'any' ? rules.some(matches) : rules.every(matches))
    visiting.delete(candidate.id)
    memo.set(candidate.id, shown)
    return shown
  }

  return visible(field)
}

export function canonicalAnswers(fields: FormField[], answers: FormAnswers): FormAnswers {
  const result: FormAnswers = {}
  for (const field of fields) {
    if (contentKinds.has(field.kind) || !fieldActive(field, answers, fields) || !Object.hasOwn(answers, field.id)) continue
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
  for (const field of fields) if (field.kind === 'checkbox' && !Object.hasOwn(result, field.id)) result[field.id] = false
  return result
}
