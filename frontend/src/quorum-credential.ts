import { Identity } from '@semaphore-protocol/identity'

export type Credential = { secret: string; commitment: string }

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
