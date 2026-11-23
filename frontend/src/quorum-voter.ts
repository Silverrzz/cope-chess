import { shallowRef } from 'vue'
import { Identity } from '@semaphore-protocol/identity'
import type { SemaphoreProof } from '@semaphore-protocol/proof'
import api from '@/api/client'
import type { BallotPackage } from '@/quorum'

export const credentialStorage = 'cope-quorum-credential-v1'
export type Credential = { secret: string; commitment: string }

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

export function loadQuorumBallot(path: string, receipt: string) {
  return api.get<BallotPackage>(path, receipt ? {
    credentials: 'omit', headers: { 'X-Cope-Quorum-Receipt': receipt },
  } : undefined)
}

const sessionKey = 'cope-quorum-unlocked-v1'

function restoreSession(): Credential | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(sessionKey) ?? 'null')
    if (typeof value?.secret === 'string' && typeof value.commitment === 'string') return value
  } catch {
    return null
  }
  return null
}

export const unlockedCredential = shallowRef<Credential | null>(restoreSession())

export function rememberCredential(credential: Credential | null) {
  unlockedCredential.value = credential
  try {
    if (credential) sessionStorage.setItem(sessionKey, JSON.stringify(credential))
    else sessionStorage.removeItem(sessionKey)
  } catch {
    return
  }
}

const encode = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes))
const decode = (value: string) => Uint8Array.from(atob(value), character => character.charCodeAt(0))

async function credentialKey(password: string, salt: Uint8Array<ArrayBuffer>) {
  const material = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveKey'])
  return crypto.subtle.deriveKey({ name: 'PBKDF2', salt, iterations: 600000, hash: 'SHA-256' }, material,
    { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt'])
}

export function createCredential(): Credential {
  const identity = new Identity(crypto.getRandomValues(new Uint8Array(32)))
  return { secret: identity.export(), commitment: identity.commitment.toString() }
}

export async function encryptCredential(credential: Credential, password: string) {
  const salt = crypto.getRandomValues(new Uint8Array(16))
  const iv = crypto.getRandomValues(new Uint8Array(12))
  const encrypted = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, await credentialKey(password, salt),
    new TextEncoder().encode(credential.secret))
  return JSON.stringify({ salt: encode(salt), iv: encode(iv), ciphertext: encode(new Uint8Array(encrypted)) })
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
