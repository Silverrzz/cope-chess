import { shallowRef } from 'vue'
import type { Credential } from './quorum-credential.ts'
export { createCredential, encryptCredential, unlockCredential } from './quorum-credential.ts'
import type { SemaphoreProof } from '@semaphore-protocol/proof'

export const credentialStorage = 'cope-quorum-credential-v1'

export function quorumReceiptKey(ballotId: string) {
  return 'cope-quorum-receipt:' + ballotId
}

export function readQuorumReceipt(ballotId: string) {
  try {
    return localStorage.getItem(quorumReceiptKey(ballotId)) ?? ''
  } catch {
    return ''
  }
}

const sessionKey = 'cope-quorum-unlocked-v1'

// Discard credentials persisted by older releases. Keep the unlocked secret
// in memory only; a reload requires the encrypted backup or local vault.
try { sessionStorage.removeItem(sessionKey) } catch { /* Storage can be unavailable. */ }
export const unlockedCredential = shallowRef<Credential | null>(null)

export function rememberCredential(credential: Credential | null) {
  unlockedCredential.value = credential
}

export function generateQuorumProof(credential: Credential, members: string[], message: string, scope: string) {
  const worker = new Worker(new URL('./quorum-proof.worker.ts', import.meta.url), { type: 'module' })
  return new Promise<SemaphoreProof>((resolve, reject) => {
    const timeout = setTimeout(() => {
      worker.terminate()
      reject(new Error('Proof generation timed out. Try again on this device or restore your backup on another device.'))
    }, 180000)
    worker.onmessage = ({ data }) => {
      clearTimeout(timeout)
      worker.terminate()
      if (data.proof) resolve(data.proof)
      else reject(new Error(data.error))
    }
    worker.onerror = () => {
      clearTimeout(timeout)
      worker.terminate()
      reject(new Error('Could not start the local proof generator.'))
    }
    try {
      worker.postMessage({ secret: credential.secret, members: Array.from(members), message, scope })
    } catch {
      clearTimeout(timeout)
      worker.terminate()
      reject(new Error('Could not send the ballot to the local proof generator.'))
    }
  })
}

export function downloadQuorumFile(filename: string, content: string) {
  const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
