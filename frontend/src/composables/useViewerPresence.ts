import { onBeforeUnmount, onMounted } from 'vue'
import { api } from '@/api/client'

const NAME_KEY = 'cope.chat.displayName'
const HISTORY_KEY = 'cope.chat.previousDisplayName'
const VIEWER_KEY = 'cope.viewer.id'
let viewerId = ''
let currentName = ''
let previousName = ''
let updateTimer: number | undefined

function readIdentity(): { viewer_id: string; name: string; previous_name: string } {
  try {
    viewerId = localStorage.getItem(VIEWER_KEY) || viewerId
    currentName = (localStorage.getItem(NAME_KEY) || '').trim().slice(0, 40)
    previousName = (localStorage.getItem(HISTORY_KEY) || previousName).trim().slice(0, 40)
    if (!previousName && currentName && currentName.toLowerCase() !== 'system') {
      previousName = currentName
      localStorage.setItem(HISTORY_KEY, previousName)
    }
  } catch {}
  if (!viewerId) {
    const bytes = crypto.getRandomValues(new Uint8Array(16))
    viewerId = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  }
  try { localStorage.setItem(VIEWER_KEY, viewerId) } catch {}
  return { viewer_id: viewerId, name: currentName, previous_name: previousName }
}

export async function syncViewerIdentity(): Promise<void> {
  try { await api.post('/api/viewer-identity', { body: readIdentity() }) } catch {}
}

export function saveViewerName(value: string): void {
  currentName = value.trim().slice(0, 40)
  try {
    if (currentName) localStorage.setItem(NAME_KEY, currentName)
    else localStorage.removeItem(NAME_KEY)
  } catch {}
  window.clearTimeout(updateTimer)
  updateTimer = window.setTimeout(() => {
    if (currentName && currentName.toLowerCase() !== 'system') {
      previousName = currentName
      try { localStorage.setItem(HISTORY_KEY, previousName) } catch {}
    }
    void syncViewerIdentity()
  }, 400)
}

export function savedViewerName(): string {
  return readIdentity().name
}

export function useViewerPresence(): { viewerStreamUrl: (path: string) => string; syncViewerIdentity: () => Promise<void> } {
  let timer: number | undefined
  function storageChanged(event: StorageEvent): void {
    if (event.key === NAME_KEY || event.key === null) void syncViewerIdentity()
  }
  onMounted(() => {
    timer = window.setInterval(() => { void syncViewerIdentity() }, 15_000)
    window.addEventListener('storage', storageChanged)
  })
  onBeforeUnmount(() => {
    window.clearInterval(timer)
    window.removeEventListener('storage', storageChanged)
  })
  function viewerStreamUrl(path: string): string {
    const params = new URLSearchParams({ viewer_id: readIdentity().viewer_id })
    return `${path}${path.includes('?') ? '&' : '?'}${params}`
  }
  return { viewerStreamUrl, syncViewerIdentity }
}
