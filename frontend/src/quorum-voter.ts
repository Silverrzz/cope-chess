import { Identity } from '@semaphore-protocol/identity'
import type { SemaphoreProof } from '@semaphore-protocol/proof'

export const credentialStorage = 'cope-quorum-credential-v1'
export type Credential = { secret: string; commitment: string }

const encode = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes))
const decode = (value: string) => Uint8Array.from(atob(value), character => character.charCodeAt(0))

async function credentialKey(password: string, salt: Uint8Array<ArrayBuffer>) {
  const material = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveKey'])
  return crypto.subtle.deriveKey({ name: 'PBKDF2', salt, iterations: 600000, hash: 'SHA-256' }, material,
    { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt'])
}

export async function createCredential(password: string): Promise<Credential> {
  const identity = new Identity(crypto.getRandomValues(new Uint8Array(32)))
  const secret = identity.export()
  const salt = crypto.getRandomValues(new Uint8Array(16))
  const iv = crypto.getRandomValues(new Uint8Array(12))
  const encrypted = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, await credentialKey(password, salt),
    new TextEncoder().encode(secret))
  localStorage.setItem(credentialStorage, JSON.stringify({ salt: encode(salt), iv: encode(iv), ciphertext: encode(new Uint8Array(encrypted)) }))
  return { secret, commitment: identity.commitment.toString() }
}

export async function unlockCredential(password: string, encrypted: string): Promise<Credential> {
  try {
    const envelope = JSON.parse(encrypted)
    const salt = decode(envelope.salt)
    const iv = decode(envelope.iv)
    const ciphertext = decode(envelope.ciphertext)
    if (salt.length !== 16 || iv.length !== 12 || ciphertext.length > 256) throw new Error()
    const plaintext = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, await credentialKey(password, salt), ciphertext)
    const secret = new TextDecoder().decode(plaintext)
    return { secret, commitment: Identity.import(secret).commitment.toString() }
  } catch {
    throw new Error('Could not unlock this credential. Check your password and backup.')
  }
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
    worker.postMessage({ secret: credential.secret, members, message, scope })
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
