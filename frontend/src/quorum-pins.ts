import { verifyPolicyChain } from './quorum-history.ts'

const legacyKey = 'cope-quorum-pinned-witness-policy/1'
const storage = 'cope-quorum-policies'
const storeName = 'pins'
export const maxPolicyBytes = 12 * 1024 * 1024

async function openPins(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(storage, 1)
    request.onupgradeneeded = () => request.result.createObjectStore(storeName)
    request.onerror = () => reject(request.error)
    request.onsuccess = () => resolve(request.result)
  })
}

async function transaction<T>(
  mode: IDBTransactionMode,
  action: (store: IDBObjectStore, setResult: (value: T) => void) => void,
): Promise<T> {
  const database = await openPins()
  try {
    return await new Promise((resolve, reject) => {
      let result: T
      const tx = database.transaction(storeName, mode)
      tx.onabort = () => reject(tx.error ?? new Error('Could not access the pinned witness policy.'))
      tx.oncomplete = () => resolve(result)
      action(tx.objectStore(storeName), value => { result = value })
    })
  } finally {
    database.close()
  }
}

const readRawPin = (origin: string) => transaction<string | null>('readonly', (store, done) => {
  const request = store.get(origin)
  request.onsuccess = () => done(request.result ?? null)
})

const initialPin = (origin: string) => origin === location.origin ? localStorage.getItem(legacyKey) : null

export async function loadPinnedPolicy(origin: string) {
  const raw = await readRawPin(origin) ?? initialPin(origin)
  return raw ? verifyPolicyChain(JSON.parse(raw), origin) : undefined
}

export async function readPolicyFile(file: File, origin: string) {
  if (file.size > maxPolicyBytes) throw new Error('Choose a witness policy file up to 12 MiB.')
  return verifyPolicyChain(JSON.parse(await file.text()), origin)
}

export async function extendPinnedPolicy(file: File, origin: string, persist = false) {
  const candidate = await readPolicyFile(file, origin)
  for (let retries = 0; retries < 5; retries++) {
    const expected = await readRawPin(origin)
    const raw = expected ?? initialPin(origin)
    const pinned = raw ? await verifyPolicyChain(JSON.parse(raw), origin) : undefined
    if (pinned && !candidate.heads.includes(pinned.head)) {
      throw new Error('This file does not extend your approved witness policy.')
    }
    if (!persist) return candidate

    // The read and write run in one serialized transaction across tabs.
    // Signature verification may await crypto, so compare the verified bytes
    // against the live record before publishing the replacement.
    const saved = await transaction<boolean>('readwrite', (store, done) => {
      const request = store.get(origin)
      request.onsuccess = () => {
        if ((request.result ?? null) !== expected) return done(false)
        store.put(JSON.stringify(candidate.bundle), origin)
        done(true)
      }
    })
    if (saved) {
      if (typeof BroadcastChannel !== 'undefined') {
        const channel = new BroadcastChannel(storage)
        channel.postMessage(origin)
        channel.close()
      }
      return candidate
    }
  }
  throw new Error('The pinned witness policy changed. Try importing again.')
}

export function onPinChange(origin: string, callback: () => void) {
  if (typeof BroadcastChannel === 'undefined') return () => {}
  const channel = new BroadcastChannel(storage)
  channel.onmessage = event => { if (event.data === origin) callback() }
  return () => channel.close()
}
