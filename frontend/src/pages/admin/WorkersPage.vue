<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import StatusBadge from '@/components/admin/StatusBadge.vue'
import { errorText, formatDate } from '@/components/admin/format'

interface WorkerListItem {
  id: number
  label: string
  status: string
  last_seen?: string | null
  work?: { summary: string; detail?: string; meta?: string; href?: string | null; abnormal?: boolean }
  machine?: { status: string; label: string; detail?: string }
  hardware?: { reported: boolean; summary: string; detail: string; cores?: string; memory?: string }
}

interface BenchmarkerListItem {
  id: number
  label: string
  status: string
  last_seen?: string | null
  machine_id?: string | null
  app_commit?: string | null
  hardware?: { reported: boolean; summary: string; detail: string }
}

interface WorkerSnapshot {
  workers: WorkerListItem[]
  total_workers: number
  connected_workers: number
  benchmarkers?: BenchmarkerListItem[]
  total_benchmarkers?: number
  connected_benchmarkers?: number
}

const router = useRouter()
const toast = useToast()
const { confirm } = useConfirm()
const workers = ref<WorkerListItem[]>([])
const benchmarkers = ref<BenchmarkerListItem[]>([])
const page = ref(1)
const perPage = 100
const totalWorkers = ref(0)
const connectedWorkers = ref(0)
const totalBenchmarkers = ref(0)
const connectedBenchmarkers = ref(0)
const loading = ref(true)
const error = ref('')
const streamConnected = ref(false)
const creating = ref(false)
const showCreate = ref(false)
const label = ref('')
const deleting = ref<number | null>(null)
const revoking = ref<number | null>(null)
const forgettingBenchmarker = ref<number | null>(null)
let source: EventSource | null = null
let refreshTimer: number | undefined
let refreshDelay: number | undefined
let fallbackTimer: number | undefined
let loadPromise: Promise<void> | null = null

const totalPages = computed(() => Math.max(1, Math.ceil(totalWorkers.value / perPage)))

function applySnapshot(snapshot: WorkerSnapshot): void {
  workers.value = snapshot.workers
  totalWorkers.value = snapshot.total_workers ?? snapshot.workers.length
  connectedWorkers.value = snapshot.connected_workers ?? 0
  benchmarkers.value = snapshot.benchmarkers ?? []
  totalBenchmarkers.value = snapshot.total_benchmarkers ?? benchmarkers.value.length
  connectedBenchmarkers.value = snapshot.connected_benchmarkers ?? 0
}

function load(silent = false): Promise<void> {
  if (loadPromise) return loadPromise
  if (!silent) loading.value = true
  loadPromise = (async () => {
    try {
      applySnapshot(await api.get<WorkerSnapshot>(`/api/admin/workers?page=${page.value}&per_page=${perPage}`))
      error.value = ''
    } catch (cause) {
      error.value = errorText(cause)
    } finally {
      if (!silent) loading.value = false
      loadPromise = null
    }
  })()
  return loadPromise
}

function scheduleLoad(): void {
  if (refreshDelay !== undefined) window.clearTimeout(refreshDelay)
  refreshDelay = window.setTimeout(() => {
    refreshDelay = undefined
    void load(true)
  }, 100)
}

function connectStream(): void {
  source?.close()
  source = new EventSource(`/admin/workers/events?page=${page.value}`)
  source.addEventListener('open', () => { streamConnected.value = true })
  source.addEventListener('error', () => {
    streamConnected.value = false
    if (loading.value) void load()
  })
  source.addEventListener('workers.snapshot', (event) => {
    try {
      const envelope = JSON.parse((event as MessageEvent).data)
      if (Array.isArray(envelope.data?.workers)) {
        applySnapshot(envelope.data)
        loading.value = false
        error.value = ''
      }
    } catch {
      return
    }
  })
  source.addEventListener('workers.changed', scheduleLoad)
}

async function changePage(nextPage: number): Promise<void> {
  page.value = Math.min(Math.max(nextPage, 1), totalPages.value)
  await load()
  connectStream()
}

async function createWorker(): Promise<void> {
  creating.value = true
  try {
    const response = await api.post<{ id: number; message: string }>('/api/admin/workers', {
      body: { label: label.value.trim() || 'worker' },
    })
    toast.success(response.message)
    await router.push(`/admin/workers/${response.id}`)
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    creating.value = false
  }
}

async function remove(worker: WorkerListItem): Promise<void> {
  const accepted = await confirm({ title: 'Delete worker?', message: `Delete “${worker.label}”? Any active games will return to the scheduler.`, confirmLabel: 'Delete worker', tone: 'danger' })
  if (!accepted) return
  deleting.value = worker.id
  try {
    const response = await api.delete<{ message: string }>(`/api/admin/workers/${worker.id}`)
    toast.success(response.message)
    await load()
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    deleting.value = null
  }
}

async function revoke(worker: WorkerListItem): Promise<void> {
  const accepted = await confirm({ title: 'Revoke worker?', message: `Revoke “${worker.label}”? It will be removed and cannot reconnect with its current credentials.`, confirmLabel: 'Revoke worker', tone: 'danger' })
  if (!accepted) return
  revoking.value = worker.id
  try {
    const response = await api.post<{ message: string }>(`/api/admin/workers/${worker.id}/revoke`)
    toast.success(response.message)
    await load()
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    revoking.value = null
  }
}

async function forgetBenchmarker(benchmarker: BenchmarkerListItem): Promise<void> {
  const accepted = await confirm({ title: 'Forget benchmarker?', message: `Forget “${benchmarker.label}”? It will be disconnected, its credentials will stop working, and any active benchmark will return to the queue.`, confirmLabel: 'Forget benchmarker', tone: 'danger' })
  if (!accepted) return
  forgettingBenchmarker.value = benchmarker.id
  try {
    const response = await api.delete<{ message: string }>(`/api/admin/benchmarkers/${benchmarker.id}`)
    toast.success(response.message)
    await load(true)
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    forgettingBenchmarker.value = null
  }
}

function shortVersion(value?: string | null): string {
  if (!value) return '-'
  return /^[0-9a-f]{40}$/.test(value) ? value.slice(0, 12) : value
}

onMounted(() => {
  connectStream()
  fallbackTimer = window.setTimeout(() => {
    fallbackTimer = undefined
    if (loading.value) void load()
  }, 1_500)
  refreshTimer = window.setInterval(() => {
    if (!streamConnected.value) void load(true)
  }, 30_000)
})
onBeforeUnmount(() => {
  source?.close()
  if (refreshTimer !== undefined) window.clearInterval(refreshTimer)
  if (refreshDelay !== undefined) window.clearTimeout(refreshDelay)
  if (fallbackTimer !== undefined) window.clearTimeout(fallbackTimer)
})
</script>

<template>
  <div class="admin-page page-stack">
    <AdminPageHeader title="Workers" description="Manage tournament workers and benchmark clients from one place.">
      <template #actions><button class="button button--primary" type="button" @click="showCreate = !showCreate">New machine worker</button></template>
    </AdminPageHeader>
    <InlineFeedback :message="error" />

    <form v-if="showCreate" class="panel create-worker" @submit.prevent="createWorker">
      <div><h2>Register a machine</h2><p>CPU and memory capacity are detected when the worker connects.</p></div>
      <label><span>Worker label</span><input v-model="label" class="input" maxlength="80" autofocus></label>
      <div class="button-row"><button class="button button--ghost" type="button" @click="showCreate = false">Cancel</button><button class="button button--primary" type="submit" :disabled="creating">{{ creating ? 'Creating…' : 'Create worker' }}</button></div>
    </form>

    <section class="worker-summary" aria-label="Worker summary">
      <div><strong>{{ totalWorkers }}</strong><span>Workers</span></div>
      <div><strong>{{ connectedWorkers }}</strong><span>Workers online</span></div>
      <div><strong>{{ totalBenchmarkers }}</strong><span>Benchmarkers</span></div>
      <div><strong>{{ connectedBenchmarkers }}</strong><span>Benchmarkers online</span></div>
      <div><span class="stream-dot" :class="{ connected: streamConnected }" aria-hidden="true" /><strong>{{ streamConnected ? 'Live' : 'Reconnecting' }}</strong><span>Status stream</span></div>
    </section>

    <section class="panel worker-panel">
      <div v-if="loading" class="index-loading" role="status">Loading workers…</div>
      <div v-else-if="workers.length" class="table-scroll">
        <table class="data-table">
          <thead><tr><th>Machine worker</th><th>Status</th><th>Current work</th><th>Capacity</th><th>Machine</th><th>Last seen</th><th><span class="sr-only">Actions</span></th></tr></thead>
          <tbody>
            <tr v-for="worker in workers" :key="worker.id" :class="{ 'worker-row--warning': worker.work?.abnormal }">
              <td><RouterLink :to="`/admin/workers/${worker.id}`"><strong>{{ worker.label }}</strong><small>#{{ worker.id }}</small></RouterLink></td>
              <td><StatusBadge :status="worker.status" /></td>
              <td class="work-cell"><strong>{{ worker.work?.summary ?? 'No active assignment' }}</strong><small>{{ worker.work?.detail }}</small><small v-if="worker.work?.meta">{{ worker.work.meta }}</small></td>
              <td><span>{{ worker.hardware?.summary ?? 'Not reported' }}</span><small>{{ worker.hardware?.detail }}</small></td>
              <td><span class="state-label">{{ worker.machine?.label ?? 'Unknown' }}</span></td>
              <td>{{ formatDate(worker.last_seen) }}</td>
              <td class="row-actions"><RouterLink class="button button--ghost button--small" :to="`/admin/workers/${worker.id}`">Open</RouterLink><button class="button button--danger button--small" type="button" :disabled="revoking === worker.id" @click="revoke(worker)">{{ revoking === worker.id ? 'Revoking…' : 'Revoke' }}</button><button class="icon-button icon-button--danger" type="button" :disabled="deleting === worker.id" :aria-label="`Delete ${worker.label}`" @click="remove(worker)"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M5 7h14M9 7V4h6v3m2 0-1 13H8L7 7m3 4v5m4-5v5" /></svg></button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <AdminEmptyState v-else title="No machine workers registered"><button class="button button--primary button--small" type="button" @click="showCreate = true">New machine worker</button></AdminEmptyState>
      <nav v-if="totalPages > 1" class="worker-pagination" aria-label="Worker pages">
        <button class="button button--ghost button--small" type="button" :disabled="page <= 1" @click="changePage(page - 1)">Previous</button>
        <span>Page {{ page }} of {{ totalPages }}</span>
        <button class="button button--ghost button--small" type="button" :disabled="page >= totalPages" @click="changePage(page + 1)">Next</button>
      </nav>
    </section>

    <section class="panel worker-panel">
      <header class="panel-heading"><div><h2>Benchmarkers</h2><p>Dedicated clients that measure engine performance for build activation and worker calibration.</p></div><span>{{ totalBenchmarkers }}</span></header>
      <div v-if="loading" class="index-loading" role="status">Loading benchmarkers…</div>
      <div v-else-if="benchmarkers.length" class="table-scroll">
        <table class="data-table benchmarker-table">
          <thead><tr><th>Benchmarker</th><th>Status</th><th>Hardware</th><th>Machine</th><th>Release</th><th>Last seen</th><th><span class="sr-only">Actions</span></th></tr></thead>
          <tbody>
            <tr v-for="benchmarker in benchmarkers" :key="benchmarker.id">
              <td class="identity-cell"><strong>{{ benchmarker.label }}</strong><small>#{{ benchmarker.id }}</small></td>
              <td><StatusBadge :status="benchmarker.status" /></td>
              <td><span>{{ benchmarker.hardware?.summary ?? 'Not reported' }}</span><small>{{ benchmarker.hardware?.detail }}</small></td>
              <td><code>{{ benchmarker.machine_id?.slice(0, 12) ?? '-' }}</code></td>
              <td><code>{{ shortVersion(benchmarker.app_commit) }}</code></td>
              <td>{{ formatDate(benchmarker.last_seen) }}</td>
              <td class="row-actions"><button class="button button--danger button--small" type="button" :disabled="forgettingBenchmarker === benchmarker.id" @click="forgetBenchmarker(benchmarker)">{{ forgettingBenchmarker === benchmarker.id ? 'Forgetting…' : 'Forget' }}</button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <AdminEmptyState v-else title="No benchmarkers registered" description="Registered benchmark clients will appear here." />
    </section>
  </div>
</template>

<style scoped>
.create-worker { align-items: end; display: grid; gap: 1rem; grid-template-columns: minmax(16rem, 1.5fr) minmax(14rem, 1fr) auto; padding: 1rem; }
.create-worker h2 { font-size: .9rem; margin: 0; }.create-worker p { color: var(--color-text-muted, #64748b); font-size: .72rem; margin: .2rem 0 0; }.create-worker label { display: grid; font-size: .76rem; font-weight: 650; gap: .35rem; }
.worker-summary { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr)); }.worker-summary > div { align-items: center; background: var(--color-surface, #fff); border: 1px solid var(--color-border, #d9e0ea); border-radius: var(--radius-md, .6rem); display: flex; gap: .5rem; padding: .7rem .8rem; }.worker-summary strong { font-size: .9rem; }.worker-summary span:last-child { color: var(--color-text-muted, #64748b); font-size: .7rem; margin-left: auto; }.stream-dot { background: var(--color-danger, #b42318); border-radius: 50%; height: .5rem; width: .5rem; }.stream-dot.connected { background: var(--color-success, #15803d); box-shadow: 0 0 0 .2rem color-mix(in srgb, var(--color-success, #15803d) 15%, transparent); }
.worker-panel { overflow: hidden; padding: 0; }.panel-heading { align-items: center; border-bottom: 1px solid var(--color-border, #d9e0ea); display: flex; gap: 1rem; justify-content: space-between; padding: .8rem .9rem; }.panel-heading h2 { font-size: .88rem; margin: 0; }.panel-heading p { color: var(--color-text-muted, #64748b); font-size: .68rem; margin: .2rem 0 0; }.panel-heading > span { background: var(--color-surface-subtle, #f1f5f9); border-radius: 999px; font-size: .68rem; padding: .25rem .5rem; }.table-scroll { overflow-x: auto; }.data-table { border-collapse: collapse; min-width: 72rem; width: 100%; }.benchmarker-table { min-width: 62rem; }.data-table th { color: var(--color-text-muted, #64748b); font-size: .65rem; letter-spacing: .04em; padding: .65rem .75rem; text-align: left; text-transform: uppercase; }.data-table td { border-top: 1px solid var(--color-border, #d9e0ea); font-size: .74rem; padding: .7rem .75rem; vertical-align: middle; }.worker-row--warning { background: color-mix(in srgb, var(--color-warning, #b7791f) 6%, transparent); }.data-table td:first-child a,.identity-cell { color: inherit; display: grid; text-decoration: none; }.data-table small { color: var(--color-text-muted, #64748b); display: block; font-size: .64rem; margin-top: .15rem; }.data-table code { font-size: .68rem; }.work-cell { max-width: 20rem; }.work-cell strong, .work-cell small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.state-label { background: var(--color-surface-subtle, #f1f5f9); border-radius: 999px; font-size: .66rem; padding: .28rem .45rem; }.row-actions { align-items: center; display: flex; gap: .3rem; justify-content: flex-end; }.row-actions svg { fill: none; height: 1rem; stroke: currentColor; stroke-linecap: round; stroke-linejoin: round; stroke-width: 1.7; width: 1rem; }.index-loading { color: var(--color-text-muted, #64748b); min-height: 14rem; padding: 2rem; }.worker-pagination { align-items: center; border-top: 1px solid var(--color-border, #d9e0ea); display: flex; gap: .75rem; justify-content: flex-end; padding: .65rem .75rem; }.worker-pagination span { color: var(--color-text-muted, #64748b); font-size: .7rem; }
@media (max-width: 50rem) { .create-worker { align-items: stretch; grid-template-columns: 1fr; }.worker-summary { grid-template-columns: 1fr; } }
</style>
