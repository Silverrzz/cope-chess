<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import StatusBadge from '@/components/admin/StatusBadge.vue'
import WorkerTokenPanel from '@/components/admin/WorkerTokenPanel.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import { errorText, formatDate, formatNumber } from '@/components/admin/format'
import type { Worker, WorkerRow } from '@/components/admin/types'

interface WorkerFailure { id: number; worker_id: number | null; worker_label: string; machine_id: string | null; assignment_id: number | null; game_id: number | null; engine_id: number | null; engine_name: string; stage: string; error: string; occurred_at: string }
interface WorkerTournament { id: number; name: string; status: string }
interface WorkerEvent { id: number; title: string; status: string; scheduled_start_at: string | null }
interface EventClaim { event_id: number; event_title: string; tournament_id: number; fixture_title: string; status: string; scheduled_start_at: string | null; assignment_id: number | null; assignment_status: string | null }
interface WorkerSettings { core_limit: number | null; effective_cores: number | null; effective_memory_mb: number | null; tournament_scope: 'all' | 'selected'; tournament_ids: number[]; tournaments: WorkerTournament[]; event_ids: number[]; events: WorkerEvent[]; event_claim: EventClaim | null }
interface ResourceSample { sampled_at: string; cpu_percent: number; memory_used_mb: number; memory_total_mb: number; memory_available_mb: number; coordinator_cpu_cores: number; coordinator_memory_mb: number; engine_cpu_cores: number; engine_memory_mb: number; disk_used_mb: number; disk_free_mb: number; disk_total_mb: number }
interface ResourceAllocation { assignment_id: number; game_id: number; status: string; tournament_id: number; tournament_name: string; white_engine: string; black_engine: string; threads: number; engine_hash_mb: number; process_memory_mb: number; memory_mb: number }
interface WorkerResources { latest: ResourceSample | null; samples: ResourceSample[]; allocations: ResourceAllocation[] }
interface LocalEngine { local_key: string; discovered_at: string; engine_version_id: number | null; engine_name: string | null; engine_version: string | null }
interface Response { row: WorkerRow; worker: Worker; settings: WorkerSettings; resources: WorkerResources; local_engines: LocalEngine[]; worker_launch_command?: string | null; failures: WorkerFailure[] }
interface Minted { token: string; expires_at: string; start_command?: string; message: string }
interface WorkerTokenBindings { token: string; expiresAt: string; startCommand?: string }
type WorkerView = 'overview' | 'resources' | 'system'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const { confirm } = useConfirm()
const id = computed(() => Number(route.params.id))
const data = ref<Response | null>(null)
const minted = ref<Minted | null>(null)
const loading = ref(true)
const error = ref('')
const pending = ref('')
const label = ref('')
const coreLimit = ref<number | ''>('')
const tournamentScope = ref<'all' | 'selected'>('all')
const selectedTournamentIds = ref<number[]>([])
const selectedEventIds = ref<number[]>([])
const savedSettingsKey = ref('')
const settingsInitialized = ref(false)
const copied = ref(false)
const streamConnected = ref(false)
const activeView = ref<WorkerView>('overview')
let source: EventSource | null = null

const latest = computed(() => data.value?.resources.latest ?? null)
const samples = computed(() => data.value?.resources.samples ?? [])
const allocations = computed(() => data.value?.resources.allocations ?? [])
const eventClaim = computed(() => data.value?.settings.event_claim ?? null)
const detectedCoreCount = computed(() => data.value?.worker.hw?.logical_cores ?? null)
const effectiveCoreCount = computed(() => data.value?.settings.effective_cores ?? detectedCoreCount.value ?? 0)
const detectedMemoryMb = computed(() => {
  const hardware = data.value?.worker.hw
  return hardware ? hardware.ram_mb ?? hardware.ram_gb * 1024 : 0
})
const totalMemoryMb = computed(() => latest.value?.memory_total_mb ?? detectedMemoryMb.value)
const cpuPercent = computed(() => latest.value?.cpu_percent ?? 0)
const memoryPercent = computed(() => percentage(latest.value?.memory_used_mb ?? 0, totalMemoryMb.value))
const diskPercent = computed(() => percentage(latest.value?.disk_used_mb ?? 0, latest.value?.disk_total_mb ?? 0))
const workerCpuCores = computed(() => (latest.value?.engine_cpu_cores ?? 0) + (latest.value?.coordinator_cpu_cores ?? 0))
const workerMemoryMb = computed(() => (latest.value?.engine_memory_mb ?? 0) + (latest.value?.coordinator_memory_mb ?? 0))
const reservedThreads = computed(() => allocations.value.reduce((sum, item) => sum + item.threads, 0))
const reservedMemoryMb = computed(() => allocations.value.reduce((sum, item) => sum + item.memory_mb, 0))
const cpuLine = computed(() => chartLine(samples.value.map((sample) => sample.cpu_percent), 100))
const cpuArea = computed(() => chartArea(cpuLine.value))
const memoryLine = computed(() => chartLine(samples.value.map((sample) => percentage(sample.memory_used_mb, sample.memory_total_mb)), 100))
const memoryArea = computed(() => chartArea(memoryLine.value))
const chartWindow = computed(() => {
  if (samples.value.length < 2) return 'Collecting live history'
  const seconds = Math.max(0, (Date.parse(samples.value.at(-1)?.sampled_at ?? '') - Date.parse(samples.value[0]?.sampled_at ?? '')) / 1000)
  if (seconds < 60) return `Last ${Math.round(seconds)} seconds`
  return `Last ${Math.round(seconds / 60)} minutes`
})
const telemetryFresh = computed(() => {
  const sampledAt = latest.value?.sampled_at
  return !!sampledAt && Date.now() - Date.parse(sampledAt) < 12_000
})
const missionState = computed(() => {
  if (!streamConnected.value) return 'Reconnecting to live control'
  if (!latest.value) return 'Waiting for first telemetry sample'
  if (!telemetryFresh.value) return 'Telemetry is catching up'
  if (cpuPercent.value >= 92 || memoryPercent.value >= 92) return 'Capacity pressure detected'
  if (eventClaim.value?.status === 'scheduled') return `Ready for ${eventClaim.value.event_title}`
  if (eventClaim.value) return `Dedicated to ${eventClaim.value.event_title}`
  if (allocations.value.length) return `${allocations.value.length} active ${allocations.value.length === 1 ? 'game' : 'games'} under control`
  return 'Online and ready for work'
})
const cpuBreakdown = computed(() => {
  const total = Math.max(effectiveCoreCount.value, workerCpuCores.value, 1)
  const engine = Math.min(latest.value?.engine_cpu_cores ?? 0, total)
  const coordinator = Math.min(latest.value?.coordinator_cpu_cores ?? 0, Math.max(total - engine, 0))
  const hostUsed = Math.min(total, cpuPercent.value / 100 * total)
  const system = Math.max(0, hostUsed - engine - coordinator)
  return { total, engine, coordinator, system, free: Math.max(0, total - engine - coordinator - system) }
})
const memoryBreakdown = computed(() => {
  const total = Math.max(totalMemoryMb.value, workerMemoryMb.value, 1)
  const engine = Math.min(latest.value?.engine_memory_mb ?? 0, total)
  const coordinator = Math.min(latest.value?.coordinator_memory_mb ?? 0, Math.max(total - engine, 0))
  const system = Math.max(0, (latest.value?.memory_used_mb ?? 0) - engine - coordinator)
  return { total, engine, coordinator, system, free: Math.max(0, total - engine - coordinator - system) }
})
const settingsKey = computed(() => JSON.stringify({
  core_limit: coreLimit.value === '' ? null : Number(coreLimit.value),
  tournament_scope: tournamentScope.value,
  tournament_ids: [...selectedTournamentIds.value].sort((left, right) => left - right),
  event_ids: [...selectedEventIds.value].sort((left, right) => left - right),
}))
const settingsDirty = computed(() => settingsInitialized.value && settingsKey.value !== savedSettingsKey.value)

function percentage(value: number, total: number): number {
  return total > 0 ? Math.max(0, Math.min(100, value / total * 100)) : 0
}

function chartLine(values: number[], ceiling: number): string {
  if (!values.length) return ''
  if (values.length === 1) {
    const y = 34 - Math.max(0, Math.min(1, (values[0] ?? 0) / ceiling)) * 30
    return `0,${y.toFixed(2)} 100,${y.toFixed(2)}`
  }
  return values.map((value, index) => {
    const x = index / (values.length - 1) * 100
    const y = 34 - Math.max(0, Math.min(1, value / ceiling)) * 30
    return `${x.toFixed(2)},${y.toFixed(2)}`
  }).join(' ')
}

function chartArea(line: string): string {
  return line ? `0,36 ${line} 100,36` : ''
}

function meterStyle(value: number): Record<string, string> {
  return { '--meter-value': `${Math.max(0, Math.min(100, value)) * 3.6}deg` }
}

function segmentStyle(value: number, total: number): Record<string, string> {
  return { width: `${percentage(value, total)}%` }
}

function formatResource(value: number): string {
  if (!Number.isFinite(value)) return '—'
  if (value >= 1024) return `${(value / 1024).toLocaleString(undefined, { maximumFractionDigits: value >= 10240 ? 0 : 1 })} GB`
  return `${value.toLocaleString(undefined, { maximumFractionDigits: value >= 100 ? 0 : 1 })} MB`
}

function formatCores(value: number): string {
  return value.toLocaleString(undefined, { minimumFractionDigits: value > 0 && value < 1 ? 2 : 1, maximumFractionDigits: 2 })
}

function workerTokenBindings(value: Minted): WorkerTokenBindings {
  const bindings: WorkerTokenBindings = { token: value.token, expiresAt: value.expires_at }
  if (value.start_command !== undefined) bindings.startCommand = value.start_command
  return bindings
}

function syncSettings(response: Response): void {
  coreLimit.value = response.settings.core_limit ?? ''
  tournamentScope.value = response.settings.tournament_scope
  selectedTournamentIds.value = response.settings.tournament_scope === 'all'
    ? response.settings.tournaments.map((tournament) => tournament.id)
    : [...response.settings.tournament_ids]
  selectedEventIds.value = [...response.settings.event_ids]
  settingsInitialized.value = true
  savedSettingsKey.value = settingsKey.value
}

async function load(background = false, replaceSettings = false): Promise<void> {
  if (!background) loading.value = true
  try {
    const response = await api.get<Response>(`/api/admin/workers/${id.value}`)
    const preserveSettings = settingsDirty.value && !replaceSettings
    data.value = response
    label.value = response.worker.label
    if (!preserveSettings) syncSettings(response)
    error.value = ''
  } catch (cause) { error.value = errorText(cause) }
  finally { if (!background) loading.value = false }
}

function connectStream(): void {
  source?.close()
  source = new EventSource(`/admin/workers/${id.value}/events`)
  source.addEventListener('open', () => { streamConnected.value = true })
  source.addEventListener('error', () => { streamConnected.value = false })
  source.addEventListener('worker.snapshot', (event) => {
    try {
      const envelope = JSON.parse((event as MessageEvent<string>).data) as { data?: Response & { deleted?: boolean } }
      if (envelope.data?.deleted) { void router.replace('/admin/workers'); return }
      if (!envelope.data?.worker || !envelope.data.row) return
      const preserveSettings = settingsDirty.value || pending.value === 'settings'
      data.value = envelope.data
      if (pending.value !== 'label') label.value = envelope.data.worker.label
      if (!preserveSettings) syncSettings(envelope.data)
    } catch {}
  })
}

function setCoreMode(mode: 'automatic' | 'custom'): void {
  coreLimit.value = mode === 'automatic' ? '' : Math.max(1, data.value?.settings.effective_cores ?? detectedCoreCount.value ?? 1)
}

function setTournamentScope(scope: 'all' | 'selected'): void {
  tournamentScope.value = scope
  if (scope === 'all' || selectedTournamentIds.value.length === 0) selectAllTournaments()
}

function selectAllTournaments(): void {
  selectedTournamentIds.value = data.value?.settings.tournaments.map((tournament) => tournament.id) ?? []
}

function clearTournaments(): void {
  selectedTournamentIds.value = []
}

function selectAllEvents(): void {
  selectedEventIds.value = data.value?.settings.events.map((event) => event.id) ?? []
}

function clearEvents(): void {
  selectedEventIds.value = []
}

async function saveSettings(): Promise<void> {
  const parsedCoreLimit = coreLimit.value === '' ? null : Number(coreLimit.value)
  if (parsedCoreLimit !== null && (!Number.isInteger(parsedCoreLimit) || parsedCoreLimit < 1)) { error.value = 'CPU thread limit must be a positive whole number.'; return }
  if (parsedCoreLimit !== null && detectedCoreCount.value !== null && parsedCoreLimit > detectedCoreCount.value) { error.value = `CPU thread limit cannot exceed the detected ${detectedCoreCount.value} threads.`; return }
  const availableTournamentIds = new Set(data.value?.settings.tournaments.map((tournament) => tournament.id) ?? [])
  const availableEventIds = new Set(data.value?.settings.events.map((event) => event.id) ?? [])
  pending.value = 'settings'
  try {
    const response = await api.put<{ message: string }>(`/api/admin/workers/${id.value}/settings`, {
      body: {
        core_limit: parsedCoreLimit,
        tournament_scope: tournamentScope.value,
        tournament_ids: tournamentScope.value === 'selected' ? selectedTournamentIds.value.filter((tournamentId) => availableTournamentIds.has(tournamentId)) : [],
        event_ids: selectedEventIds.value.filter((eventId) => availableEventIds.has(eventId)),
      },
    })
    toast.success(response.message)
    await load(true, true)
  } catch (cause) { error.value = errorText(cause); toast.error(cause) }
  finally { pending.value = '' }
}

async function rename(): Promise<void> {
  if (!label.value.trim()) { error.value = 'Enter a worker label.'; return }
  pending.value = 'label'
  try { const response = await api.put<{ message: string }>(`/api/admin/workers/${id.value}/label`, { body: { label: label.value.trim() } }); toast.success(response.message); await load(true) }
  catch (cause) { error.value = errorText(cause); toast.error(cause) }
  finally { pending.value = '' }
}

async function generateToken(): Promise<void> {
  const accepted = minted.value ? await confirm({ title: 'Replace one-time token?', message: 'The token currently visible on this page will stop working. The replacement will also be shown only once.', confirmLabel: 'Generate replacement' }) : true
  if (!accepted) return
  pending.value = 'token'
  try { const response = await api.post<Minted>(`/api/admin/workers/${id.value}/token`, { body: { ttl_seconds: 7200 } }); minted.value = response; toast.success(response.message); await load(true) }
  catch (cause) { error.value = errorText(cause); toast.error(cause) }
  finally { pending.value = '' }
}

async function revoke(): Promise<void> {
  const accepted = await confirm({ title: 'Revoke worker?', message: `Revoke “${data.value?.worker.label}”? It will be removed and cannot reconnect with its current credentials.`, confirmLabel: 'Revoke worker', tone: 'danger' })
  if (!accepted) return
  pending.value = 'revoke'
  try { const response = await api.post<{ message: string }>(`/api/admin/workers/${id.value}/revoke`); toast.success(response.message); minted.value = null; await router.push('/admin/workers') }
  catch (cause) { error.value = errorText(cause); toast.error(cause) }
  finally { pending.value = '' }
}

async function remove(): Promise<void> {
  const accepted = await confirm({ title: 'Delete worker?', message: `Permanently delete “${data.value?.worker.label}”?`, confirmLabel: 'Delete worker', tone: 'danger' })
  if (!accepted) return
  pending.value = 'delete'
  try { const response = await api.delete<{ message: string }>(`/api/admin/workers/${id.value}`); toast.success(response.message); await router.push('/admin/workers') }
  catch (cause) { error.value = errorText(cause); toast.error(cause) }
  finally { pending.value = '' }
}

async function copy(value: string): Promise<void> {
  await navigator.clipboard.writeText(value)
  copied.value = true
  window.setTimeout(() => { copied.value = false }, 1600)
}

onMounted(async () => { await load(); connectStream() })
onBeforeUnmount(() => source?.close())
</script>

<template>
  <div class="admin-page worker-control-page">
    <InlineFeedback :message="error" />
    <div v-if="loading" class="panel detail-loading" role="status"><span></span>Establishing worker control…</div>
    <template v-else-if="data">
      <AdminPageHeader :title="data.worker.label" :description="`Worker #${data.worker.id} · ${data.row.machine?.label ?? 'Machine identity pending'}`">
        <template #actions>
          <span class="live-state" :class="{ online: streamConnected && telemetryFresh }"><span aria-hidden="true"></span>{{ streamConnected ? telemetryFresh ? 'Live telemetry' : 'Stream connected' : 'Reconnecting' }}</span>
          <RouterLink class="button button--ghost" to="/admin/workers"><AppIcon name="arrow-left" :size="15" /> All workers</RouterLink>
        </template>
      </AdminPageHeader>

      <WorkerTokenPanel v-if="minted" v-bind="workerTokenBindings(minted)" />

      <section class="mission-deck" aria-labelledby="mission-state-title">
        <div class="mission-deck__glow" aria-hidden="true"></div>
        <div class="mission-deck__topline">
          <div class="mission-identity">
            <span class="mission-identity__icon"><AppIcon name="server" :size="22" /></span>
            <div><span>Worker mission control</span><h2 id="mission-state-title">{{ missionState }}</h2></div>
          </div>
          <StatusBadge :status="data.row.status" />
        </div>
        <div class="mission-work">
          <div class="mission-work__pulse" :class="{ active: allocations.length || eventClaim }"><AppIcon :name="allocations.length || eventClaim ? 'activity' : 'radio'" :size="18" /></div>
          <div v-if="eventClaim"><span>Event reservation</span><strong>{{ eventClaim.fixture_title }}</strong><small>{{ eventClaim.status === 'scheduled' ? eventClaim.assignment_status === 'acked' ? 'Every fixture engine is built and ready at the start barrier.' : 'Building and benchmarking the fixture engines now.' : 'All scheduler capacity is dedicated to this event fixture.' }}</small></div>
          <div v-else><span>{{ allocations.length ? 'Current operation' : 'Scheduler state' }}</span><strong>{{ data.row.work?.summary ?? 'No active assignment' }}</strong><small>{{ data.row.work?.detail || (allocations.length ? 'Work is progressing normally.' : 'Standing by for a compatible assignment.') }}</small></div>
          <div class="mission-work__meta"><span>{{ eventClaim ? 'Exclusive event' : `${allocations.length} active` }}</span><span>{{ eventClaim ? `${effectiveCoreCount || '—'}/${effectiveCoreCount || '—'} threads allocated` : `${reservedThreads}/${effectiveCoreCount || '—'} threads reserved` }}</span><span>{{ eventClaim?.scheduled_start_at ? `Starts ${formatDate(eventClaim.scheduled_start_at)}` : `Seen ${formatDate(data.worker.last_seen)}` }}</span></div>
        </div>
      </section>

      <nav class="control-tabs" aria-label="Worker control views">
        <button type="button" :class="{ active: activeView === 'overview' }" @click="activeView = 'overview'"><AppIcon name="activity" :size="16" /><span>Live overview</span></button>
        <button type="button" :class="{ active: activeView === 'resources' }" @click="activeView = 'resources'"><AppIcon name="gauge" :size="16" /><span>Resource policy</span><i v-if="settingsDirty" aria-label="Unsaved changes"></i></button>
        <button type="button" :class="{ active: activeView === 'system' }" @click="activeView = 'system'"><AppIcon name="settings" :size="16" /><span>System & access</span><em v-if="data.failures.length">{{ data.failures.length }}</em></button>
      </nav>

      <main v-if="activeView === 'overview'" class="view-stack">
        <section class="resource-kpis" aria-label="Live resource status">
          <article class="resource-kpi">
            <div class="radial-meter radial-meter--cpu" :style="meterStyle(cpuPercent)"><span><strong>{{ Math.round(cpuPercent) }}%</strong><small>host load</small></span></div>
            <div><span>CPU now</span><strong>{{ latest ? `${formatCores(workerCpuCores)} cores` : 'Awaiting data' }}</strong><small>{{ latest ? `${formatCores(latest.engine_cpu_cores)} workload · ${formatCores(latest.coordinator_cpu_cores)} control` : `${effectiveCoreCount || '—'} scheduler threads available` }}</small></div>
          </article>
          <article class="resource-kpi">
            <div class="radial-meter radial-meter--memory" :style="meterStyle(memoryPercent)"><span><strong>{{ Math.round(memoryPercent) }}%</strong><small>host used</small></span></div>
            <div><span>Memory now</span><strong>{{ latest ? formatResource(latest.memory_used_mb) : 'Awaiting data' }}</strong><small>{{ latest ? `${formatResource(workerMemoryMb)} attributed to worker` : `${formatResource(detectedMemoryMb)} detected` }}</small></div>
          </article>
          <article class="resource-kpi">
            <div class="capacity-orbit"><span>{{ allocations.length }}</span><i></i><i></i><i></i></div>
            <div><span>Scheduler allocation</span><strong>{{ allocations.length ? `${reservedThreads} threads reserved` : 'Capacity available' }}</strong><small>{{ allocations.length ? `${formatResource(reservedMemoryMb)} memory budget across active games` : 'No resources are reserved by active games' }}</small></div>
          </article>
        </section>

        <div class="overview-grid">
          <section class="panel telemetry-panel">
            <header class="section-heading">
              <div><span class="eyebrow">Live telemetry</span><h2>Resource trajectory</h2><p>Measured on the worker machine every two seconds.</p></div>
              <span class="time-window"><AppIcon name="clock" :size="14" />{{ chartWindow }}</span>
            </header>
            <div v-if="latest" class="chart-grid">
              <article class="telemetry-chart telemetry-chart--cpu">
                <div class="chart-heading"><div><span>CPU utilisation</span><strong>{{ cpuPercent.toFixed(1) }}%</strong></div><small>{{ formatCores(workerCpuCores) }} worker cores</small></div>
                <svg viewBox="0 0 100 36" preserveAspectRatio="none" role="img" aria-label="CPU utilisation history">
                  <defs><linearGradient id="worker-cpu-area" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="currentColor" stop-opacity=".3"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></linearGradient></defs>
                  <path class="chart-gridline" d="M0 4H100M0 19H100M0 34H100" />
                  <polygon :points="cpuArea" fill="url(#worker-cpu-area)" />
                  <polyline :points="cpuLine" />
                </svg>
                <div class="chart-scale"><span>100%</span><span>50%</span><span>0%</span></div>
              </article>
              <article class="telemetry-chart telemetry-chart--memory">
                <div class="chart-heading"><div><span>Memory utilisation</span><strong>{{ memoryPercent.toFixed(1) }}%</strong></div><small>{{ formatResource(latest.memory_available_mb) }} free</small></div>
                <svg viewBox="0 0 100 36" preserveAspectRatio="none" role="img" aria-label="Memory utilisation history">
                  <defs><linearGradient id="worker-memory-area" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="currentColor" stop-opacity=".3"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></linearGradient></defs>
                  <path class="chart-gridline" d="M0 4H100M0 19H100M0 34H100" />
                  <polygon :points="memoryArea" fill="url(#worker-memory-area)" />
                  <polyline :points="memoryLine" />
                </svg>
                <div class="chart-scale"><span>100%</span><span>50%</span><span>0%</span></div>
              </article>
            </div>
            <div v-else class="telemetry-empty"><span><AppIcon name="radio" :size="22" /></span><div><strong>Waiting for measured telemetry</strong><p>Capacity and assignment controls remain available while this worker sends its first resource sample.</p></div></div>
          </section>

          <section class="panel allocation-panel">
            <header class="section-heading"><div><span class="eyebrow">Active allocation</span><h2>Where capacity is going</h2><p>Exact scheduler reservations for every live game.</p></div><strong class="allocation-count">{{ allocations.length }}</strong></header>
            <div v-if="allocations.length" class="allocation-list">
              <RouterLink v-for="allocation in allocations" :key="allocation.assignment_id" class="allocation-card" :to="`/tournaments/${allocation.tournament_id}?game_id=${allocation.game_id}`">
                <span class="allocation-card__state"><i></i>{{ allocation.status }}</span>
                <div><strong>{{ allocation.white_engine }} <span>vs</span> {{ allocation.black_engine }}</strong><small>{{ allocation.tournament_name }} · Game #{{ allocation.game_id }}</small></div>
                <dl><div><dt>CPU</dt><dd>{{ allocation.threads }} {{ allocation.threads === 1 ? 'thread' : 'threads' }}</dd></div><div><dt>Hash</dt><dd>{{ formatResource(allocation.engine_hash_mb) }}</dd></div><div><dt>Headroom</dt><dd>{{ formatResource(allocation.process_memory_mb) }}</dd></div><div><dt>Total memory</dt><dd>{{ formatResource(allocation.memory_mb) }}</dd></div></dl>
                <AppIcon name="chevron-right" :size="17" />
              </RouterLink>
            </div>
            <div v-else class="allocation-empty"><span><AppIcon name="check-circle" :size="24" /></span><strong>Nothing is consuming scheduler capacity</strong><p>This worker is ready to accept a compatible game.</p></div>
          </section>
        </div>

        <section v-if="latest" class="panel resource-map">
          <header class="section-heading"><div><span class="eyebrow">Measured breakdown</span><h2>Host resource map</h2><p>Worker processes are measured directly; remaining host use is grouped as system activity.</p></div><span class="sample-age">Sampled {{ formatDate(latest.sampled_at) }}</span></header>
          <div class="resource-map__grid">
            <article>
              <div class="map-title"><span>CPU</span><strong>{{ formatCores(workerCpuCores) }} of {{ effectiveCoreCount }} cores attributed</strong></div>
              <div class="stacked-meter" aria-label="CPU allocation breakdown"><span class="segment engine" :style="segmentStyle(cpuBreakdown.engine, cpuBreakdown.total)"></span><span class="segment control" :style="segmentStyle(cpuBreakdown.coordinator, cpuBreakdown.total)"></span><span class="segment system" :style="segmentStyle(cpuBreakdown.system, cpuBreakdown.total)"></span><span class="segment free" :style="segmentStyle(cpuBreakdown.free, cpuBreakdown.total)"></span></div>
              <dl class="map-legend"><div><dt><i class="engine"></i>Workload processes</dt><dd>{{ formatCores(cpuBreakdown.engine) }} cores</dd></div><div><dt><i class="control"></i>Worker control</dt><dd>{{ formatCores(cpuBreakdown.coordinator) }} cores</dd></div><div><dt><i class="system"></i>Other system load</dt><dd>{{ formatCores(cpuBreakdown.system) }} cores</dd></div><div><dt><i class="free"></i>Available</dt><dd>{{ formatCores(cpuBreakdown.free) }} cores</dd></div></dl>
            </article>
            <article>
              <div class="map-title"><span>Memory</span><strong>{{ formatResource(workerMemoryMb) }} of {{ formatResource(memoryBreakdown.total) }} attributed</strong></div>
              <div class="stacked-meter" aria-label="Memory allocation breakdown"><span class="segment engine" :style="segmentStyle(memoryBreakdown.engine, memoryBreakdown.total)"></span><span class="segment control" :style="segmentStyle(memoryBreakdown.coordinator, memoryBreakdown.total)"></span><span class="segment system" :style="segmentStyle(memoryBreakdown.system, memoryBreakdown.total)"></span><span class="segment free" :style="segmentStyle(memoryBreakdown.free, memoryBreakdown.total)"></span></div>
              <dl class="map-legend"><div><dt><i class="engine"></i>Workload processes</dt><dd>{{ formatResource(memoryBreakdown.engine) }}</dd></div><div><dt><i class="control"></i>Worker control</dt><dd>{{ formatResource(memoryBreakdown.coordinator) }}</dd></div><div><dt><i class="system"></i>OS and other use</dt><dd>{{ formatResource(memoryBreakdown.system) }}</dd></div><div><dt><i class="free"></i>Available</dt><dd>{{ formatResource(memoryBreakdown.free) }}</dd></div></dl>
            </article>
            <article class="disk-map">
              <div class="map-title"><span>Worker volume</span><strong>{{ diskPercent.toFixed(1) }}% used</strong></div>
              <div class="disk-meter"><span :style="{ width: `${diskPercent}%` }"></span></div>
              <div class="disk-values"><span>{{ formatResource(latest.disk_used_mb) }} used</span><span>{{ formatResource(latest.disk_free_mb) }} free</span><strong>{{ formatResource(latest.disk_total_mb) }} total</strong></div>
            </article>
          </div>
        </section>
      </main>

      <main v-else-if="activeView === 'resources'" class="view-stack">
        <form class="panel resource-policy" @submit.prevent="saveSettings">
          <header class="resource-policy__heading"><div><span class="eyebrow">Resource policy</span><h2>Shape scheduler access without interrupting live work</h2><p>Changes apply to future assignments. The {{ allocations.length }} currently active {{ allocations.length === 1 ? 'game continues' : 'games continue' }} with the resources already reserved.</p></div><span :class="['save-state', { dirty: settingsDirty }]">{{ settingsDirty ? 'Unsaved changes' : 'Policy in sync' }}</span></header>
          <div class="policy-grid">
            <fieldset class="capacity-control">
              <legend>CPU capacity</legend>
              <p>Choose whether the scheduler can use every detected thread or a deliberate ceiling.</p>
              <div class="choice-switch" role="radiogroup" aria-label="CPU limit mode"><button type="button" role="radio" :aria-checked="coreLimit === ''" :class="{ active: coreLimit === '' }" @click="setCoreMode('automatic')"><AppIcon name="activity" :size="16" /><span><strong>Automatic</strong><small>Use all detected capacity</small></span></button><button type="button" role="radio" :aria-checked="coreLimit !== ''" :class="{ active: coreLimit !== '' }" @click="setCoreMode('custom')"><AppIcon name="gauge" :size="16" /><span><strong>Custom ceiling</strong><small>Hold capacity in reserve</small></span></button></div>
              <div class="capacity-dial">
                <div><span>Scheduler ceiling</span><strong>{{ coreLimit === '' ? detectedCoreCount ?? '—' : coreLimit }} <small>threads</small></strong></div>
                <input v-if="coreLimit !== ''" v-model.number="coreLimit" type="range" min="1" :max="detectedCoreCount ?? 1" step="1" aria-label="Maximum scheduler threads">
                <div v-else class="automatic-track"><span></span></div>
                <div class="range-labels"><span>1</span><span>{{ detectedCoreCount ?? 'Detected maximum' }}</span></div>
              </div>
              <div class="capacity-impact"><div><span>Reserved now</span><strong>{{ reservedThreads }}</strong></div><div><span>Future ceiling</span><strong>{{ coreLimit === '' ? detectedCoreCount ?? '—' : coreLimit }}</strong></div><div><span>Physical cores</span><strong>{{ data.worker.hw?.physical_cores ?? '—' }}</strong></div></div>
            </fieldset>

            <fieldset class="tournament-control">
              <legend>Tournament routing</legend>
              <p>Decide which queues can dispatch work here.</p>
              <div class="choice-switch" role="radiogroup" aria-label="Tournament routing mode"><button type="button" role="radio" :aria-checked="tournamentScope === 'all'" :class="{ active: tournamentScope === 'all' }" @click="setTournamentScope('all')"><AppIcon name="radio" :size="16" /><span><strong>All tournaments</strong><small>Include new queues automatically</small></span></button><button type="button" role="radio" :aria-checked="tournamentScope === 'selected'" :class="{ active: tournamentScope === 'selected' }" @click="setTournamentScope('selected')"><AppIcon name="filter" :size="16" /><span><strong>Selected only</strong><small>Route from an explicit allow-list</small></span></button></div>
              <div class="routing-header"><div><span>Eligible queues</span><strong>{{ tournamentScope === 'all' ? data.settings.tournaments.length : selectedTournamentIds.length }} of {{ data.settings.tournaments.length }}</strong></div><div v-if="tournamentScope === 'selected'"><button type="button" @click="selectAllTournaments">Select all</button><button type="button" @click="clearTournaments">Clear</button></div></div>
              <div v-if="data.settings.tournaments.length" class="route-list" :class="{ automatic: tournamentScope === 'all' }">
                <label v-for="tournament in data.settings.tournaments" :key="tournament.id"><input v-model="selectedTournamentIds" type="checkbox" :value="tournament.id" :disabled="tournamentScope === 'all'"><span class="route-check"><AppIcon name="check" :size="13" /></span><span><strong>{{ tournament.name }}</strong><small>{{ tournament.status }} · Queue #{{ tournament.id }}</small></span><StatusBadge :status="tournament.status" /></label>
              </div>
              <div v-else class="routing-empty"><AppIcon name="check-circle" :size="20" /><span><strong>No unfinished tournaments</strong><small>There are currently no queues to route.</small></span></div>
              <p v-if="tournamentScope === 'selected' && selectedTournamentIds.length === 0 && selectedEventIds.length === 0" class="routing-warning"><AppIcon name="alert-circle" :size="15" /> No tournament or event can dispatch to this worker, so it will remain idle.</p>
              <div class="event-routing">
                <div class="routing-header"><div><span>Event reservations</span><strong>{{ selectedEventIds.length }} assigned</strong></div><div v-if="data.settings.events.length"><button type="button" @click="selectAllEvents">Select all</button><button type="button" @click="clearEvents">Clear</button></div></div>
                <p>Assigned events may reserve the entire worker as soon as a fixture is scheduled. Engines are built and benchmarked before its start time, and unrelated queues remain blocked until the fixture finishes.</p>
                <div v-if="data.settings.events.length" class="route-list event-route-list">
                  <label v-for="event in data.settings.events" :key="event.id"><input v-model="selectedEventIds" type="checkbox" :value="event.id"><span class="route-check"><AppIcon name="check" :size="13" /></span><span><strong>{{ event.title }}</strong><small>{{ event.scheduled_start_at ? `Next start ${formatDate(event.scheduled_start_at)}` : `${event.status} · Awaiting a scheduled fixture` }}</small></span><StatusBadge :status="event.status" /></label>
                </div>
                <div v-else class="routing-empty"><AppIcon name="trophy" :size="20" /><span><strong>No current events</strong><small>Current events will become available for whole-worker assignment here.</small></span></div>
              </div>
            </fieldset>
          </div>
          <footer class="policy-actions"><div><AppIcon name="info" :size="16" /><span>Memory scheduling remains automatic at <strong>{{ formatResource(data.settings.effective_memory_mb ?? 0) }}</strong>, preserving OS and process headroom.</span></div><button class="button button--primary" type="submit" :disabled="!!pending || !settingsDirty">{{ pending === 'settings' ? 'Applying policy…' : 'Apply resource policy' }}</button></footer>
        </form>
      </main>

      <main v-else class="view-stack">
        <div class="system-grid">
          <section class="panel system-card">
            <header><span><AppIcon name="edit" :size="17" /></span><div><h2>Identity</h2><p>Make this machine easy to recognise.</p></div></header>
            <form class="identity-form" @submit.prevent="rename"><label><span>Worker label</span><input v-model="label" class="input" required maxlength="80"></label><button class="button button--primary button--small" type="submit" :disabled="pending === 'label'">{{ pending === 'label' ? 'Saving…' : 'Save label' }}</button></form>
            <dl class="compact-facts"><div><dt>Worker ID</dt><dd>#{{ data.worker.id }}</dd></div><div><dt>Machine ID</dt><dd><code>{{ data.worker.machine_id ?? 'Pending first connection' }}</code></dd></div><div><dt>Release</dt><dd><code>{{ data.worker.app_version?.slice(0, 12) ?? 'Not reported' }}</code></dd></div></dl>
          </section>
          <section class="panel system-card">
            <header><span><AppIcon name="server" :size="17" /></span><div><h2>Hardware profile</h2><p>Detected directly from the machine.</p></div></header>
            <dl v-if="data.worker.hw" class="compact-facts hardware-facts"><div><dt>Processor</dt><dd>{{ data.worker.hw.cpu_model }}</dd></div><div><dt>Topology</dt><dd>{{ data.worker.hw.physical_cores }} physical · {{ data.worker.hw.logical_cores }} logical</dd></div><div><dt>Memory</dt><dd>{{ formatResource(detectedMemoryMb) }}</dd></div><div><dt>Operating system</dt><dd>{{ data.worker.hw.os ?? 'Not reported' }}</dd></div><div v-if="data.worker.hw.gpu"><dt>GPU</dt><dd>{{ data.worker.hw.gpu }}</dd></div><div v-if="data.worker.hw.bench?.nps_probe"><dt>Benchmark</dt><dd>{{ formatNumber(data.worker.hw.bench.nps_probe) }} NPS</dd></div></dl>
            <div v-else class="system-empty">Hardware will appear after the first connection.</div>
          </section>
          <section class="panel system-card">
            <header><span><AppIcon name="server" :size="17" /></span><div><h2>Worker-local engines</h2><p>Private binaries discovered on the latest connection.</p></div></header>
            <dl v-if="data.local_engines.length" class="compact-facts"><div v-for="engine in data.local_engines" :key="engine.local_key"><dt><code>{{ engine.local_key }}</code></dt><dd>{{ engine.engine_name ? `${engine.engine_name} ${engine.engine_version}` : 'No registered version' }}</dd></div></dl>
            <div v-else class="system-empty">No executable binaries were discovered below the local engine directory.</div>
          </section>
          <section class="panel system-card">
            <header><span><AppIcon name="radio" :size="17" /></span><div><h2>Connection chain</h2><p>Every link required for live work.</p></div></header>
            <div class="connection-chain"><div><i :class="{ ok: streamConnected }"></i><span><strong>Control stream</strong><small>{{ streamConnected ? 'Events are arriving live' : 'Attempting to reconnect' }}</small></span></div><div><i :class="{ ok: data.row.machine?.status === 'connected' || data.row.status !== 'offline' }"></i><span><strong>{{ data.row.machine?.label ?? 'Machine' }}</strong><small>{{ data.row.machine?.detail || 'No machine detail' }}</small></span></div><div><i :class="{ ok: !!data.worker.session_id }"></i><span><strong>{{ data.row.session?.label ?? 'Worker session' }}</strong><small>{{ data.row.session?.detail || 'No active session' }}</small></span></div><div><i :class="{ ok: telemetryFresh }"></i><span><strong>Resource telemetry</strong><small>{{ latest ? `Last sample ${formatDate(latest.sampled_at)}` : 'Waiting for first sample' }}</small></span></div></div>
          </section>
          <section class="panel system-card">
            <header><span><AppIcon name="user" :size="17" /></span><div><h2>Credentials</h2><p>Connect or recover this worker securely.</p></div></header>
            <div class="credential-actions"><button v-if="data.worker.status !== 'revoked' && !data.worker.session_id" class="button button--primary button--small" type="button" :disabled="pending === 'token'" @click="generateToken"><AppIcon name="plus" :size="14" />{{ pending === 'token' ? 'Generating…' : minted ? 'Regenerate token' : 'Generate one-time token' }}</button><button v-if="data.worker_launch_command" class="button button--secondary button--small" type="button" @click="copy(data.worker_launch_command)"><AppIcon :name="copied ? 'check' : 'copy'" :size="14" />{{ copied ? 'Copied' : 'Copy start command' }}</button><p v-if="data.worker.session_id">This worker is registered. Copy its session command to restart it without exposing a new token.</p><p v-else>One-time registration tokens expire after two hours and are only displayed once.</p></div>
          </section>
        </div>

        <section v-if="data.failures.length" class="panel failure-panel" aria-labelledby="worker-failures-title">
          <header class="section-heading"><div><span class="eyebrow">Attention required</span><h2 id="worker-failures-title">Recent engine failures</h2><p>Open an incident to see the full worker-reported error.</p></div><span class="failure-count">{{ data.failures.length }}</span></header>
          <details v-for="(failure, index) in data.failures" :key="failure.id" class="failure-entry" :open="index === 0">
            <summary><span class="failure-icon"><AppIcon name="alert-circle" :size="17" /></span><span><strong>{{ failure.engine_name }}</strong><small>{{ failure.stage }} failed · Game #{{ failure.game_id ?? '—' }}</small></span><time :datetime="failure.occurred_at">{{ formatDate(failure.occurred_at) }}</time><AppIcon name="chevron-down" :size="16" /></summary>
            <div class="failure-entry__body"><dl><div><dt>Worker</dt><dd>{{ failure.worker_label }} (#{{ failure.worker_id ?? data.worker.id }})</dd></div><div><dt>Machine</dt><dd>{{ failure.machine_id ?? 'Unknown' }}</dd></div><div><dt>Assignment</dt><dd>#{{ failure.assignment_id ?? '—' }}</dd></div><div><dt>Engine</dt><dd>{{ failure.engine_name }} (#{{ failure.engine_id ?? '—' }})</dd></div></dl><pre>{{ failure.error }}</pre></div>
          </details>
        </section>

        <section class="panel danger-zone"><div><span><AppIcon name="alert-circle" :size="18" /></span><div><h2>Worker access</h2><p>Revocation disconnects the worker and returns active games to the scheduler. Deletion permanently removes its record.</p></div></div><div><button class="button button--secondary" type="button" :disabled="!!pending" @click="revoke">{{ pending === 'revoke' ? 'Revoking…' : 'Revoke access' }}</button><button class="button button--danger" type="button" :disabled="!!pending" @click="remove">{{ pending === 'delete' ? 'Deleting…' : 'Delete worker' }}</button></div></section>
      </main>
    </template>
  </div>
</template>

<style scoped>
.worker-control-page { display: grid; gap: 1rem; }
.detail-loading { align-items: center; color: var(--color-text-muted, #64748b); display: flex; gap: .7rem; min-height: 14rem; padding: 2rem; }
.detail-loading span { animation: pulse 1.2s infinite; background: var(--color-accent, #2f78c4); border-radius: 50%; height: .6rem; width: .6rem; }
.live-state { align-items: center; background: color-mix(in srgb, var(--color-warning, #b7791f) 8%, transparent); border: 1px solid color-mix(in srgb, var(--color-warning, #b7791f) 25%, transparent); border-radius: 999px; color: var(--color-warning, #8a5a12); display: inline-flex; font-size: .68rem; font-weight: 750; gap: .45rem; padding: .38rem .62rem; }
.live-state > span { background: currentColor; border-radius: 50%; height: .45rem; width: .45rem; }
.live-state.online { background: color-mix(in srgb, var(--color-success, #24865a) 8%, transparent); border-color: color-mix(in srgb, var(--color-success, #24865a) 25%, transparent); color: var(--color-success, #24865a); }
.live-state.online > span { animation: live-pulse 2s infinite; }
.mission-deck { background: radial-gradient(circle at 82% -30%, rgba(66, 194, 255, .35), transparent 43%), linear-gradient(135deg, #111c32 0%, #172842 55%, #14243b 100%); border: 1px solid rgba(130, 177, 222, .23); border-radius: 1rem; box-shadow: 0 1rem 2.5rem rgba(16, 32, 54, .16); color: #f7fbff; overflow: hidden; padding: 1.15rem; position: relative; }
.mission-deck__glow { background-image: linear-gradient(rgba(255,255,255,.028) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.028) 1px, transparent 1px); background-size: 24px 24px; inset: 0; mask-image: linear-gradient(90deg, transparent, #000 65%, transparent); pointer-events: none; position: absolute; }
.mission-deck__topline, .mission-work { position: relative; }
.mission-deck__topline { align-items: center; display: flex; justify-content: space-between; }
.mission-identity { align-items: center; display: flex; gap: .75rem; }
.mission-identity__icon { align-items: center; background: rgba(89, 192, 255, .12); border: 1px solid rgba(107, 202, 255, .27); border-radius: .65rem; color: #70d2ff; display: flex; height: 2.65rem; justify-content: center; width: 2.65rem; }
.mission-identity span, .mission-work > div:nth-child(2) > span { color: #8ea5c3; font-size: .61rem; font-weight: 800; letter-spacing: .11em; text-transform: uppercase; }
.mission-identity h2 { font-size: 1.03rem; margin: .18rem 0 0; }
.mission-work { align-items: center; background: rgba(3, 11, 23, .24); border: 1px solid rgba(154, 191, 226, .13); border-radius: .75rem; display: grid; gap: .8rem; grid-template-columns: auto minmax(0, 1fr) auto; margin-top: 1rem; padding: .85rem; }
.mission-work__pulse { align-items: center; background: rgba(132, 154, 181, .13); border-radius: 50%; color: #9aacc1; display: flex; height: 2.35rem; justify-content: center; width: 2.35rem; }
.mission-work__pulse.active { background: rgba(48, 206, 149, .14); color: #50e0a8; }
.mission-work strong { display: block; font-size: .86rem; margin-top: .16rem; }
.mission-work small { color: #91a5be; display: block; font-size: .68rem; margin-top: .18rem; }
.mission-work__meta { align-items: flex-end; display: grid; gap: .18rem; justify-items: end; }
.mission-work__meta span { color: #9cb0c9; font-size: .63rem; }
.mission-work__meta span:first-child { color: #f4f8fc; font-size: .7rem; font-weight: 750; }
.control-tabs { align-items: center; background: var(--color-surface, #fff); border: 1px solid var(--color-border, #d9e0ea); border-radius: .75rem; display: flex; gap: .25rem; padding: .3rem; position: sticky; top: .5rem; z-index: 4; }
.control-tabs button { align-items: center; background: transparent; border: 0; border-radius: .5rem; color: var(--color-text-muted, #64748b); cursor: pointer; display: flex; font-size: .72rem; font-weight: 700; gap: .42rem; padding: .55rem .72rem; position: relative; }
.control-tabs button.active { background: color-mix(in srgb, var(--color-accent, #2f78c4) 9%, var(--color-surface, #fff)); color: var(--color-accent, #2f78c4); }
.control-tabs i { background: var(--color-warning, #b7791f); border-radius: 50%; height: .42rem; position: absolute; right: .25rem; top: .25rem; width: .42rem; }
.control-tabs em { align-items: center; background: color-mix(in srgb, var(--color-danger, #b42318) 12%, transparent); border-radius: 999px; color: var(--color-danger, #b42318); display: inline-flex; font-size: .6rem; font-style: normal; height: 1.15rem; justify-content: center; min-width: 1.15rem; padding: 0 .25rem; }
.view-stack { display: grid; gap: 1rem; }
.resource-kpis { display: grid; gap: .75rem; grid-template-columns: repeat(3, minmax(0, 1fr)); }
.resource-kpi { align-items: center; background: var(--color-surface, #fff); border: 1px solid var(--color-border, #d9e0ea); border-radius: .8rem; display: flex; gap: .85rem; min-width: 0; padding: .85rem; }
.resource-kpi > div:last-child { min-width: 0; }
.resource-kpi > div:last-child > span, .eyebrow { color: var(--color-text-muted, #64748b); display: block; font-size: .59rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.resource-kpi > div:last-child > strong { display: block; font-size: .86rem; margin-top: .2rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.resource-kpi > div:last-child > small { color: var(--color-text-muted, #64748b); display: block; font-size: .64rem; line-height: 1.4; margin-top: .15rem; }
.radial-meter { --meter-value: 0deg; align-items: center; background: conic-gradient(var(--meter-color) var(--meter-value), color-mix(in srgb, var(--meter-color) 10%, var(--color-border, #d9e0ea)) 0); border-radius: 50%; display: flex; flex: 0 0 auto; height: 4.2rem; justify-content: center; position: relative; width: 4.2rem; }
.radial-meter::after { background: var(--color-surface, #fff); border-radius: 50%; content: ''; inset: .36rem; position: absolute; }
.radial-meter--cpu { --meter-color: #2f83d7; }
.radial-meter--memory { --meter-color: #735dd8; }
.radial-meter > span { display: grid; justify-items: center; position: relative; z-index: 1; }
.radial-meter strong { font-size: .84rem; }
.radial-meter small { color: var(--color-text-muted, #64748b); font-size: .5rem; }
.capacity-orbit { align-items: center; background: color-mix(in srgb, var(--color-success, #24865a) 8%, transparent); border: 1px solid color-mix(in srgb, var(--color-success, #24865a) 22%, transparent); border-radius: 50%; color: var(--color-success, #24865a); display: flex; flex: 0 0 auto; height: 4.2rem; justify-content: center; position: relative; width: 4.2rem; }
.capacity-orbit span { font-size: 1.15rem; font-weight: 800; }
.capacity-orbit i { background: currentColor; border-radius: 50%; height: .3rem; left: 50%; position: absolute; top: -.15rem; transform-origin: .15rem 2.25rem; width: .3rem; }
.capacity-orbit i:nth-of-type(2) { transform: rotate(120deg); }.capacity-orbit i:nth-of-type(3) { transform: rotate(240deg); }
.overview-grid { display: grid; gap: 1rem; grid-template-columns: minmax(0, 1.35fr) minmax(18rem, .65fr); }
.telemetry-panel, .allocation-panel, .resource-map, .resource-policy, .failure-panel { overflow: hidden; padding: 0; }
.section-heading { align-items: flex-start; border-bottom: 1px solid var(--color-border, #d9e0ea); display: flex; gap: 1rem; justify-content: space-between; padding: .85rem 1rem; }
.section-heading h2 { font-size: .92rem; margin: .18rem 0 0; }
.section-heading p { color: var(--color-text-muted, #64748b); font-size: .67rem; line-height: 1.45; margin: .18rem 0 0; }
.time-window, .sample-age { align-items: center; color: var(--color-text-muted, #64748b); display: inline-flex; font-size: .62rem; gap: .35rem; white-space: nowrap; }
.chart-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.telemetry-chart { color: #2f83d7; min-width: 0; padding: 1rem; position: relative; }
.telemetry-chart + .telemetry-chart { border-inline-start: 1px solid var(--color-border, #d9e0ea); }
.telemetry-chart--memory { color: #735dd8; }
.chart-heading { align-items: flex-start; color: var(--color-text, #182230); display: flex; justify-content: space-between; }
.chart-heading span { color: var(--color-text-muted, #64748b); display: block; font-size: .62rem; }
.chart-heading strong { display: block; font-size: 1.25rem; margin-top: .15rem; }
.chart-heading small { background: var(--color-surface-subtle, #f3f6fa); border-radius: 999px; color: var(--color-text-muted, #64748b); font-size: .58rem; padding: .28rem .45rem; }
.telemetry-chart svg { display: block; height: 8.5rem; margin-top: .65rem; overflow: visible; width: 100%; }
.telemetry-chart polyline { fill: none; stroke: currentColor; stroke-linecap: round; stroke-linejoin: round; stroke-width: .65; vector-effect: non-scaling-stroke; }
.chart-gridline { fill: none; stroke: color-mix(in srgb, var(--color-border, #d9e0ea) 75%, transparent); stroke-dasharray: 2 3; stroke-width: .35; vector-effect: non-scaling-stroke; }
.chart-scale { bottom: 1.05rem; color: var(--color-text-muted, #64748b); display: flex; flex-direction: column; font-size: .5rem; justify-content: space-between; pointer-events: none; position: absolute; right: 1.08rem; top: 4.45rem; }
.telemetry-empty { align-items: center; display: flex; gap: .75rem; min-height: 12rem; padding: 1.25rem; }
.telemetry-empty > span, .allocation-empty > span { align-items: center; background: var(--color-surface-subtle, #f1f5f9); border-radius: 50%; color: var(--color-accent, #2f78c4); display: flex; height: 2.8rem; justify-content: center; width: 2.8rem; }
.telemetry-empty strong, .allocation-empty strong { font-size: .78rem; }.telemetry-empty p, .allocation-empty p { color: var(--color-text-muted, #64748b); font-size: .66rem; line-height: 1.45; margin: .2rem 0 0; }
.allocation-count, .failure-count { align-items: center; background: var(--color-surface-subtle, #f1f5f9); border-radius: .5rem; display: inline-flex; font-size: .72rem; height: 1.8rem; justify-content: center; min-width: 1.8rem; }
.allocation-list { display: grid; max-height: 25rem; overflow: auto; }
.allocation-card { align-items: center; color: inherit; display: grid; gap: .6rem; grid-template-columns: minmax(0, 1fr) auto; padding: .8rem 1rem; text-decoration: none; }
.allocation-card + .allocation-card { border-top: 1px solid var(--color-border, #d9e0ea); }
.allocation-card:hover { background: color-mix(in srgb, var(--color-accent, #2f78c4) 4%, transparent); }
.allocation-card__state { align-items: center; color: var(--color-success, #24865a); display: flex; font-size: .56rem; font-weight: 800; gap: .32rem; letter-spacing: .06em; text-transform: uppercase; }
.allocation-card__state i { animation: live-pulse 2s infinite; background: currentColor; border-radius: 50%; height: .38rem; width: .38rem; }
.allocation-card > div strong { display: block; font-size: .7rem; line-height: 1.35; }.allocation-card > div strong span { color: var(--color-text-muted, #64748b); font-size: .6rem; font-weight: 500; }.allocation-card > div small { color: var(--color-text-muted, #64748b); display: block; font-size: .58rem; margin-top: .16rem; }
.allocation-card dl { display: grid; gap: .25rem .6rem; grid-column: 1 / -1; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: .2rem 0 0; }
.allocation-card dl > div { align-items: center; display: flex; justify-content: space-between; }.allocation-card dt { color: var(--color-text-muted, #64748b); font-size: .56rem; }.allocation-card dd { font-size: .6rem; font-weight: 700; margin: 0; }
.allocation-card > svg { grid-column: 2; grid-row: 1 / 3; }
.allocation-empty { align-items: center; display: flex; flex-direction: column; min-height: 14rem; padding: 1.5rem; text-align: center; }.allocation-empty > strong { margin-top: .65rem; }
.resource-map__grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)) minmax(13rem, .65fr); }
.resource-map article { min-width: 0; padding: 1rem; }.resource-map article + article { border-inline-start: 1px solid var(--color-border, #d9e0ea); }
.map-title { align-items: center; display: flex; justify-content: space-between; }.map-title span { color: var(--color-text-muted, #64748b); font-size: .63rem; }.map-title strong { font-size: .66rem; }
.stacked-meter, .disk-meter { background: var(--color-surface-subtle, #edf1f6); border-radius: 999px; display: flex; height: .55rem; margin-top: .75rem; overflow: hidden; }.segment { min-width: 0; }.segment.engine, .map-legend i.engine { background: #2f83d7; }.segment.control, .map-legend i.control { background: #735dd8; }.segment.system, .map-legend i.system { background: #e5a442; }.segment.free, .map-legend i.free { background: #dce4ed; }
.map-legend { display: grid; gap: .38rem; margin: .75rem 0 0; }.map-legend > div { align-items: center; display: flex; justify-content: space-between; }.map-legend dt { align-items: center; color: var(--color-text-muted, #64748b); display: flex; font-size: .58rem; gap: .4rem; }.map-legend i { border-radius: 50%; height: .42rem; width: .42rem; }.map-legend dd { font-size: .6rem; font-weight: 700; margin: 0; }
.disk-meter span { background: linear-gradient(90deg, #2f83d7, #4cb2da); border-radius: inherit; }.disk-values { display: grid; gap: .28rem; margin-top: .75rem; }.disk-values span { color: var(--color-text-muted, #64748b); font-size: .58rem; }.disk-values strong { font-size: .66rem; margin-top: .1rem; }
.resource-policy__heading { align-items: flex-start; background: linear-gradient(135deg, color-mix(in srgb, var(--color-accent, #2f78c4) 7%, var(--color-surface, #fff)), var(--color-surface, #fff)); border-bottom: 1px solid var(--color-border, #d9e0ea); display: flex; gap: 1rem; justify-content: space-between; padding: 1.15rem; }.resource-policy__heading h2 { font-size: 1rem; margin: .2rem 0 0; }.resource-policy__heading p { color: var(--color-text-muted, #64748b); font-size: .69rem; line-height: 1.5; margin: .25rem 0 0; max-width: 44rem; }
.save-state { background: color-mix(in srgb, var(--color-success, #24865a) 10%, transparent); border-radius: 999px; color: var(--color-success, #24865a); font-size: .62rem; font-weight: 750; padding: .35rem .55rem; white-space: nowrap; }.save-state.dirty { background: color-mix(in srgb, var(--color-warning, #b7791f) 10%, transparent); color: var(--color-warning, #8a5a12); }
.policy-grid { display: grid; grid-template-columns: minmax(15rem, .8fr) minmax(20rem, 1.2fr); }.policy-grid fieldset { border: 0; margin: 0; min-width: 0; padding: 1.15rem; }.policy-grid fieldset + fieldset { border-inline-start: 1px solid var(--color-border, #d9e0ea); }.policy-grid legend { font-size: .84rem; font-weight: 800; padding: 0; }.policy-grid fieldset > p { color: var(--color-text-muted, #64748b); font-size: .66rem; line-height: 1.45; margin: .25rem 0 0; }
.choice-switch { background: var(--color-surface-subtle, #f3f6f9); border: 1px solid var(--color-border, #d9e0ea); border-radius: .7rem; display: grid; gap: .3rem; grid-template-columns: repeat(2, minmax(0, 1fr)); margin-top: .85rem; padding: .3rem; }.choice-switch button { align-items: flex-start; background: transparent; border: 1px solid transparent; border-radius: .48rem; color: var(--color-text-muted, #64748b); cursor: pointer; display: flex; gap: .45rem; padding: .55rem; text-align: left; }.choice-switch button.active { background: var(--color-surface, #fff); border-color: color-mix(in srgb, var(--color-accent, #2f78c4) 24%, var(--color-border, #d9e0ea)); box-shadow: 0 .15rem .45rem rgba(33, 52, 73, .06); color: var(--color-accent, #2f78c4); }.choice-switch span { display: grid; gap: .12rem; }.choice-switch strong { color: var(--color-text, #182230); font-size: .68rem; }.choice-switch small { font-size: .57rem; line-height: 1.35; }
.capacity-dial { background: color-mix(in srgb, var(--color-accent, #2f78c4) 4%, var(--color-surface, #fff)); border: 1px solid var(--color-border, #d9e0ea); border-radius: .7rem; margin-top: .8rem; padding: .8rem; }.capacity-dial > div:first-child { align-items: flex-end; display: flex; justify-content: space-between; }.capacity-dial > div:first-child span { color: var(--color-text-muted, #64748b); font-size: .62rem; }.capacity-dial > div:first-child strong { font-size: 1.25rem; }.capacity-dial > div:first-child small { color: var(--color-text-muted, #64748b); font-size: .58rem; font-weight: 600; }.capacity-dial input { accent-color: var(--color-accent, #2f78c4); margin: .85rem 0 0; width: 100%; }.automatic-track { background: var(--color-surface-subtle, #e8eef5); border-radius: 999px; height: .38rem; margin-top: 1rem; overflow: hidden; }.automatic-track span { background: linear-gradient(90deg, #2f83d7, #55b8dc); display: block; height: 100%; width: 100%; }.range-labels { color: var(--color-text-muted, #64748b); display: flex; font-size: .55rem; justify-content: space-between; margin-top: .35rem; }
.capacity-impact { display: grid; gap: .4rem; grid-template-columns: repeat(3, minmax(0, 1fr)); margin-top: .75rem; }.capacity-impact > div { background: var(--color-surface-subtle, #f3f6f9); border-radius: .5rem; display: grid; gap: .18rem; padding: .55rem; }.capacity-impact span { color: var(--color-text-muted, #64748b); font-size: .55rem; }.capacity-impact strong { font-size: .75rem; }
.routing-header { align-items: center; display: flex; justify-content: space-between; margin-top: .9rem; }.routing-header > div:first-child { align-items: baseline; display: flex; gap: .4rem; }.routing-header span { color: var(--color-text-muted, #64748b); font-size: .6rem; }.routing-header strong { font-size: .67rem; }.routing-header > div:last-child { display: flex; gap: .6rem; }.routing-header button { background: none; border: 0; color: var(--color-accent, #2f78c4); cursor: pointer; font-size: .6rem; padding: 0; }
.route-list { border: 1px solid var(--color-border, #d9e0ea); border-radius: .65rem; display: grid; margin-top: .45rem; max-height: 16rem; overflow: auto; }.route-list label { align-items: center; cursor: pointer; display: grid; gap: .5rem; grid-template-columns: auto auto minmax(0, 1fr) auto; padding: .65rem .7rem; }.route-list label + label { border-top: 1px solid var(--color-border, #d9e0ea); }.route-list input { height: 0; opacity: 0; position: absolute; width: 0; }.route-check { align-items: center; border: 1px solid var(--color-border, #c8d1dd); border-radius: .3rem; color: transparent; display: flex; height: 1.15rem; justify-content: center; width: 1.15rem; }.route-list input:checked + .route-check { background: var(--color-accent, #2f78c4); border-color: var(--color-accent, #2f78c4); color: #fff; }.route-list label > span:nth-of-type(2) { display: grid; gap: .1rem; }.route-list label strong { font-size: .66rem; }.route-list label small { color: var(--color-text-muted, #64748b); font-size: .56rem; text-transform: capitalize; }.route-list.automatic { opacity: .66; }.routing-empty { align-items: center; background: var(--color-surface-subtle, #f3f6f9); border-radius: .6rem; display: flex; gap: .55rem; margin-top: .55rem; padding: .7rem; }.routing-empty span { display: grid; gap: .12rem; }.routing-empty strong { font-size: .67rem; }.routing-empty small { color: var(--color-text-muted, #64748b); font-size: .58rem; }.routing-warning { align-items: center; color: var(--color-warning, #8a5a12) !important; display: flex; gap: .35rem; }
.event-routing { border-top: 1px solid var(--color-border, #d9e0ea); margin-top: 1rem; padding-top: .15rem; }.event-routing > p { color: var(--color-text-muted, #64748b); font-size: .61rem; line-height: 1.45; margin: .35rem 0 0; }.event-route-list { max-height: 12rem; }
.policy-actions { align-items: center; border-top: 1px solid var(--color-border, #d9e0ea); display: flex; gap: 1rem; justify-content: space-between; padding: .85rem 1.15rem; }.policy-actions > div { align-items: center; color: var(--color-text-muted, #64748b); display: flex; font-size: .63rem; gap: .45rem; }.policy-actions strong { color: var(--color-text, #182230); }
.system-grid { display: grid; gap: 1rem; grid-template-columns: repeat(2, minmax(0, 1fr)); }.system-card { overflow: hidden; padding: 0; }.system-card > header { align-items: center; border-bottom: 1px solid var(--color-border, #d9e0ea); display: flex; gap: .6rem; padding: .8rem 1rem; }.system-card > header > span { align-items: center; background: color-mix(in srgb, var(--color-accent, #2f78c4) 8%, transparent); border-radius: .45rem; color: var(--color-accent, #2f78c4); display: flex; height: 2rem; justify-content: center; width: 2rem; }.system-card h2 { font-size: .82rem; margin: 0; }.system-card header p { color: var(--color-text-muted, #64748b); font-size: .6rem; margin: .15rem 0 0; }
.identity-form { align-items: end; display: grid; gap: .55rem; grid-template-columns: minmax(0, 1fr) auto; padding: .85rem 1rem; }.identity-form label { display: grid; font-size: .64rem; font-weight: 700; gap: .3rem; }
.compact-facts { display: grid; margin: 0; }.identity-form + .compact-facts { border-top: 1px solid var(--color-border, #d9e0ea); }.compact-facts > div { align-items: center; display: grid; gap: .6rem; grid-template-columns: minmax(5.5rem, .35fr) minmax(0, 1fr); padding: .55rem 1rem; }.compact-facts > div + div { border-top: 1px solid var(--color-border, #d9e0ea); }.compact-facts dt { color: var(--color-text-muted, #64748b); font-size: .6rem; }.compact-facts dd { font-size: .66rem; font-weight: 650; margin: 0; overflow-wrap: anywhere; }.compact-facts code { font-size: .6rem; }.hardware-facts > div:first-child { align-items: start; }.system-empty { color: var(--color-text-muted, #64748b); font-size: .67rem; padding: 1rem; }
.connection-chain { display: grid; padding: .45rem 1rem; }.connection-chain > div { align-items: flex-start; display: grid; gap: .55rem; grid-template-columns: auto minmax(0, 1fr); padding: .48rem 0; position: relative; }.connection-chain > div:not(:last-child)::after { background: var(--color-border, #d9e0ea); bottom: -.4rem; content: ''; left: .28rem; position: absolute; top: .95rem; width: 1px; }.connection-chain i { background: var(--color-text-muted, #94a3b8); border: .15rem solid var(--color-surface, #fff); border-radius: 50%; box-shadow: 0 0 0 1px var(--color-border, #d9e0ea); height: .58rem; margin-top: .18rem; width: .58rem; z-index: 1; }.connection-chain i.ok { background: var(--color-success, #24865a); }.connection-chain span { display: grid; gap: .12rem; }.connection-chain strong { font-size: .67rem; }.connection-chain small { color: var(--color-text-muted, #64748b); font-size: .58rem; }
.credential-actions { align-items: flex-start; display: flex; flex-wrap: wrap; gap: .55rem; padding: 1rem; }.credential-actions p { color: var(--color-text-muted, #64748b); flex-basis: 100%; font-size: .64rem; line-height: 1.5; margin: .15rem 0 0; }
.failure-panel { border-color: color-mix(in srgb, var(--color-danger, #b42318) 25%, var(--color-border, #d9e0ea)); }.failure-panel .eyebrow, .failure-icon { color: var(--color-danger, #b42318); }.failure-count { background: color-mix(in srgb, var(--color-danger, #b42318) 10%, transparent); color: var(--color-danger, #b42318); }.failure-entry + .failure-entry, .failure-entry:first-of-type { border-top: 1px solid var(--color-border, #d9e0ea); }.failure-entry summary { align-items: center; cursor: pointer; display: grid; gap: .55rem; grid-template-columns: auto minmax(0, 1fr) auto auto; padding: .7rem 1rem; }.failure-entry summary > span:nth-child(2) { display: grid; gap: .1rem; }.failure-entry summary strong { font-size: .68rem; }.failure-entry summary small, .failure-entry summary time { color: var(--color-text-muted, #64748b); font-size: .58rem; }.failure-entry[open] summary > svg { transform: rotate(180deg); }.failure-entry__body { background: var(--color-surface-subtle, #f7f9fc); border-top: 1px solid var(--color-border, #d9e0ea); padding: .8rem 1rem 1rem; }.failure-entry__body dl { display: grid; gap: .5rem; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0 0 .7rem; }.failure-entry__body dt { color: var(--color-text-muted, #64748b); font-size: .53rem; text-transform: uppercase; }.failure-entry__body dd { font-size: .62rem; font-weight: 650; margin: .12rem 0 0; }.failure-entry__body pre { background: #111827; border-radius: .55rem; color: #e5edf7; font-size: .62rem; line-height: 1.55; margin: 0; max-height: 18rem; overflow: auto; padding: .8rem; white-space: pre-wrap; word-break: break-word; }
.danger-zone { align-items: center; border-color: color-mix(in srgb, var(--color-danger, #b42318) 23%, var(--color-border, #d9e0ea)); display: flex; gap: 1rem; justify-content: space-between; padding: .9rem 1rem; }.danger-zone > div:first-child { align-items: center; display: flex; gap: .65rem; }.danger-zone > div:first-child > span { align-items: center; background: color-mix(in srgb, var(--color-danger, #b42318) 8%, transparent); border-radius: .5rem; color: var(--color-danger, #b42318); display: flex; height: 2.2rem; justify-content: center; width: 2.2rem; }.danger-zone h2 { font-size: .78rem; margin: 0; }.danger-zone p { color: var(--color-text-muted, #64748b); font-size: .62rem; margin: .18rem 0 0; }.danger-zone > div:last-child { display: flex; gap: .5rem; }
@keyframes live-pulse { 0%, 100% { box-shadow: 0 0 0 0 currentColor; } 50% { box-shadow: 0 0 0 .22rem transparent; } }
@keyframes pulse { 0%, 100% { opacity: .4; transform: scale(.8); } 50% { opacity: 1; transform: scale(1); } }
@media (max-width: 70rem) { .resource-kpis { grid-template-columns: 1fr 1fr; }.resource-kpi:last-child { grid-column: 1 / -1; }.overview-grid { grid-template-columns: 1fr; }.allocation-list { max-height: none; }.resource-map__grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }.disk-map { border-block-start: 1px solid var(--color-border, #d9e0ea); border-inline-start: 0 !important; grid-column: 1 / -1; }.policy-grid { grid-template-columns: 1fr; }.policy-grid fieldset + fieldset { border-block-start: 1px solid var(--color-border, #d9e0ea); border-inline-start: 0; } }
@media (max-width: 48rem) { .mission-work { align-items: start; grid-template-columns: auto minmax(0, 1fr); }.mission-work__meta { align-items: start; grid-column: 2; justify-items: start; }.resource-kpis, .system-grid { grid-template-columns: 1fr; }.resource-kpi:last-child { grid-column: auto; }.chart-grid, .resource-map__grid { grid-template-columns: 1fr; }.telemetry-chart + .telemetry-chart, .resource-map article + article { border-block-start: 1px solid var(--color-border, #d9e0ea); border-inline-start: 0; }.disk-map { grid-column: auto; }.resource-policy__heading, .policy-actions { align-items: flex-start; flex-direction: column; }.policy-actions .button { width: 100%; }.failure-entry__body dl { grid-template-columns: repeat(2, minmax(0, 1fr)); }.danger-zone { align-items: flex-start; flex-direction: column; } }
@media (max-width: 34rem) { .mission-deck__topline { align-items: flex-start; gap: .7rem; }.mission-identity__icon { display: none; }.control-tabs { overflow-x: auto; }.control-tabs button { flex: 0 0 auto; }.control-tabs button span { display: none; }.resource-kpi { align-items: flex-start; }.choice-switch { grid-template-columns: 1fr; }.capacity-impact { grid-template-columns: 1fr; }.identity-form { grid-template-columns: 1fr; }.identity-form .button { justify-self: start; }.failure-entry summary { grid-template-columns: auto minmax(0, 1fr) auto; }.failure-entry summary time { display: none; }.danger-zone > div:last-child { flex-direction: column; width: 100%; }.danger-zone .button { width: 100%; } }
</style>
