import { api } from '@/api/client'
import type { FormClaim } from '@/api/forms'

function solve(challenge: string, difficulty: number, signal: AbortSignal): Promise<number> {
  const worker = new Worker(new URL('./form-proof.worker.ts', import.meta.url), { type: 'module' })
  return new Promise<number>((resolve, reject) => {
    const cleanup = () => {
      worker.terminate()
      window.clearTimeout(timeout)
      signal.removeEventListener('abort', cancel)
    }
    const cancel = () => { cleanup(); reject(signal.reason) }
    const fail = () => {
      cleanup()
      reject(new Error('Guest verification could not finish. Please try again.'))
    }
    const timeout = window.setTimeout(fail, 90000)
    signal.addEventListener('abort', cancel, { once: true })
    if (signal.aborted) { cancel(); return }
    worker.onerror = fail
    worker.onmessage = ({ data }) => {
      if (typeof data !== 'number') { fail(); return }
      cleanup()
      resolve(data)
    }
    worker.postMessage({ challenge, difficulty })
  })
}

export async function verifyGuest(slug: string, release_id: string, signal: AbortSignal): Promise<FormClaim> {
  const path = `/api/forms/${encodeURIComponent(slug)}`
  const { challenge, difficulty } = await api.post<{ challenge: string; difficulty: number }>(`${path}/challenge`, { body: { release_id }, signal })
  const answer = await solve(challenge, difficulty, signal)
  return api.post<FormClaim>(`${path}/claims`, { body: { release_id, challenge, answer }, signal })
}
