import { api } from './client'
import type { FormResponse, Page, PublicForm } from '@/types/forms'

export interface FormClaim { id: string; form_id: string; token: string; expires_at: string }
export function claimHeaders(token = ''): HeadersInit | undefined { return token ? { Authorization: `Bearer ${token}` } : undefined }
export function storedClaim(formId: string): FormClaim | null {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(`cope:form-claim:${formId}`) ?? 'null')
    if (!value || typeof value !== 'object') return null
    const claim = value as FormClaim
    return claim.form_id === formId && typeof claim.token === 'string' && claim.token.startsWith('cope_form_') && Date.parse(claim.expires_at) > Date.now() ? claim : null
  } catch { return null }
}
export function rememberClaim(claim: FormClaim): void { sessionStorage.setItem(`cope:form-claim:${claim.form_id}`, JSON.stringify(claim)) }
export async function publicForm(slug: string): Promise<PublicForm> {
  const result = await api.get<{ data: PublicForm }>(`/api/forms/${encodeURIComponent(slug)}`)
  return result.data
}
export async function formResponse(id: string, token = ''): Promise<FormResponse> {
  const result = await api.get<{ data: FormResponse }>(`/api/forms/responses/${encodeURIComponent(id)}`, { headers: claimHeaders(token) })
  return result.data
}
export async function formResponses(formId?: string, cursor?: string | null): Promise<Page<FormResponse & { form_title: string }>> {
  const result = await api.get<{ data: Page<FormResponse & { form_title: string }> }>('/api/forms/responses', { query: { form_id: formId, cursor, limit: 30 } })
  return result.data
}
