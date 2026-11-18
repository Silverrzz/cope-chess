import { api } from './client'
import { discordHeaders } from '@/stores/discord'
import type { FormAnswers, FormResponse, Page, PublicForm, Respondent, ResponseVisibility } from '@/types/forms'

export interface FormClaim { id: string; form_id: string; token: string; expires_at: string; submit_until?: string }
export function claimHeaders(token = ''): HeadersInit | undefined { return token ? { Authorization: `Bearer ${token}` } : undefined }
export function storedClaim(formId: string, responseId?: string): FormClaim | null {
  try {
    const value: unknown = JSON.parse(((responseId && sessionStorage.getItem(`cope:response-claim:${responseId}`)) || sessionStorage.getItem(`cope:form-claim:${formId}`)) ?? 'null')
    if (!value || typeof value !== 'object') return null
    const claim = value as FormClaim
    return claim.form_id === formId && typeof claim.token === 'string' && claim.token.startsWith('cope_form_') && Date.parse(claim.expires_at) > Date.now() ? claim : null
  } catch { return null }
}
export function rememberClaim(claim: FormClaim, responseId: string): void {
  try { sessionStorage.setItem(`cope:response-claim:${responseId}`, JSON.stringify(claim)) }
  catch { }
}
export async function publicForm(slug: string, signal?: AbortSignal): Promise<PublicForm> {
  const result = await api.get<{ data: PublicForm }>(`/api/forms/${encodeURIComponent(slug)}`, { signal, headers: discordHeaders })
  return result.data
}
export async function formResponse(id: string, token = '', discord = false): Promise<FormResponse> {
  const result = await api.get<{ data: FormResponse }>(`/api/forms/responses/${encodeURIComponent(id)}`, {
    headers: token ? claimHeaders(token) : discord ? discordHeaders : undefined,
  })
  return result.data
}
export type FormResponseRow = Pick<FormResponse, 'id' | 'form_id' | 'state' | 'state_version' | 'revision' | 'created_at'> & {
  form_title: string
  release_number: number
  can_close: boolean
  respondent?: Respondent | null
  anonymous?: boolean
}

export interface ResponseCollection {
  id: string
  label: string
  response_count: number
  pending_response_count: number
}

export type MySubmission = Pick<FormResponseRow, 'id' | 'form_id' | 'form_title' | 'state' | 'created_at'> & {
  updated_at: string
}

export async function mySubmissions(showClosed: boolean, formId?: string, cursor?: string | null, signal?: AbortSignal): Promise<Page<MySubmission>> {
  const result = await api.get<{ data: Page<MySubmission> }>('/api/forms/responses/mine', {
    headers: discordHeaders,
    query: { form_id: formId, cursor, show_closed: showClosed },
    signal,
  })
  return result.data
}

export async function formResponses(formId: string, showClosed: boolean, cursor?: string | null): Promise<Page<FormResponseRow>> {
  const result = await api.get<{ data: Page<FormResponseRow> }>('/api/forms/responses', {
    query: { form_id: formId, cursor, show_closed: showClosed },
  })
  return result.data
}

export async function responseCollections(showClosed: boolean, cursor?: string | null): Promise<Page<ResponseCollection>> {
  const result = await api.get<{ data: Page<ResponseCollection> }>('/api/forms/responses/collections', {
    query: { cursor, show_closed: showClosed },
  })
  return result.data
}

export interface ListedForm {
  slug: string
  title: string
  description: string
  closed: boolean
  response_visibility: ResponseVisibility
}

export type PublicResults =
  | { published: false; mode: 'summary' | 'individual' }
  | { published: true; mode: 'summary'; total: number; fields: { id: string; label: string; answered: number; average: string | null; options: { label: string; count: number }[] }[] }
  | { published: true; mode: 'individual'; items: { id: string; answers: FormAnswers }[]; next_cursor: string | null }

export function formDirectory(showClosed: boolean, offset = 0) {
  return api.get<{ items: ListedForm[]; more: boolean }>('/api/forms/directory/list', { query: { offset, show_closed: showClosed } })
}

export function formResults(slug: string, cursor?: string | null, signal?: AbortSignal) {
  return api.get<PublicResults>(`/api/forms/${encodeURIComponent(slug)}/results`, { query: { cursor }, signal })
}

export function submitForm(slug: string, releaseId: string, answers: FormAnswers, attachmentSubject: string, attemptId: string) {
  return api.post<{ submitted: boolean; already_submitted: boolean; response_id: string | null }>(
    `/api/forms/${encodeURIComponent(slug)}/submit`,
    { headers: discordHeaders, body: { release_id: releaseId, answers, attachment_subject: attachmentSubject, attempt_id: attemptId } },
  )
}
