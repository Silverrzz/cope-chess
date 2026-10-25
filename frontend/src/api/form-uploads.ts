import { ApiError, api } from './client'
import type { CommandReceipt } from './application'
import type { FormField } from '@/types/forms'

export interface UploadContext { form_id: string; release_id: string; subject: string; response_id?: string; max_bytes: number }
interface PendingUpload { signature: string; beginKey: string; finalizeKey: string; upload_id?: string; artifact_id?: string }

export function createFormUploader(context: () => UploadContext, authorize: () => Promise<void>, headers: () => HeadersInit | undefined) {
  const pending = new Map<string, PendingUpload>()
  return async (file: File, field: FormField, path: string[]): Promise<string> => {
    const current = context()
    if (!file.size || file.size > Math.min(current.max_bytes, field.max_bytes ?? 10485760)) throw new Error('Choose a nonempty attachment within the displayed file-size limit.')
    await authorize()
    const bytes = await file.arrayBuffer()
    const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), (byte) => byte.toString(16).padStart(2, '0')).join('')
    const mediaType = file.type === 'text/plain' ? 'text/plain' : 'application/octet-stream'
    const key = JSON.stringify([current.form_id, current.release_id, current.subject, path])
    const signature = JSON.stringify([digest, file.name, file.size, mediaType])
    let request = pending.get(key)
    if (request && request.signature !== signature) {
      if (!request.upload_id) throw new Error('Retry the original file to resolve its unconfirmed upload reservation before choosing another file.')
      if (!request.artifact_id) await api.post('/api/artifacts/commands', { headers: headers(), body: { command: 'artifacts.abandon_upload.v1', target: request.upload_id, idempotency_key: crypto.randomUUID(), input: {} } })
      pending.delete(key); request = undefined
    }
    request ??= { signature, beginKey: crypto.randomUUID(), finalizeKey: crypto.randomUUID() }
    pending.set(key, request)
    if (request.artifact_id) return request.artifact_id
    if (!request.upload_id) {
      try {
        const receipt = await api.post<CommandReceipt<'artifacts.begin_upload.v1'>>('/api/artifacts/commands', { headers: headers(), body: {
          command: 'artifacts.begin_upload.v1', target: current.form_id, idempotency_key: request.beginKey,
          input: { release_id: current.release_id, subject: current.subject, field_path: path, filename: file.name, size: file.size, digest, media_type: mediaType, ...(current.response_id ? { response_id: current.response_id } : {}) },
        } })
        request.upload_id = receipt.output.upload_id
      } catch (error) {
        if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408) pending.delete(key)
        throw error
      }
    }
    await api.put(`/api/artifacts/uploads/${request.upload_id}/bytes`, { headers: { ...Object.fromEntries(new Headers(headers()).entries()), 'Content-Type': 'application/octet-stream' }, body: bytes })
    const receipt = await api.post<CommandReceipt<'artifacts.finalize_upload.v1'>>('/api/artifacts/commands', { headers: headers(), body: { command: 'artifacts.finalize_upload.v1', target: request.upload_id, idempotency_key: request.finalizeKey, input: {} } })
    request.artifact_id = receipt.output.artifact_id
    return receipt.output.artifact_id
  }
}

export async function downloadAttachment(id: string, headers?: HeadersInit): Promise<void> {
  const response = await fetch(`/api/artifacts/${encodeURIComponent(id)}/download`, { headers: new Headers(headers), credentials: 'same-origin' })
  if (!response.ok) throw new Error('The attachment is unavailable to your current credential or needs storage repair.')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a'); anchor.href = url
  const encoded = response.headers.get('Content-Disposition')?.match(/filename\*=UTF-8''(.+)$/)?.[1]
  try { anchor.download = encoded ? decodeURIComponent(encoded) : 'attachment' } catch { anchor.download = 'attachment' }
  anchor.click(); URL.revokeObjectURL(url)
}
