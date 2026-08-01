<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import { useConfirm } from '@/composables/useConfirm'
import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import StatusBadge from '@/components/admin/StatusBadge.vue'
import { errorText, formatDate, formatNumber, humanize } from '@/components/admin/format'
import type { Engine, Game, Tournament, WorkerRow } from '@/components/admin/types'

interface DashboardData {
  workers: WorkerRow[]
  live_games: Game[]
  engines: Engine[] | Record<string, Engine | string>
  db_stats: Record<string, number>
  running_tournaments: Tournament[]
  complete_tournaments: Tournament[]
  recent_games: Game[]
  system: { version: string; schema_version: number; services: Array<{ service: string; app_version: string; last_seen: string }> }
}

interface BenchmarkActivity {
  updated_at: string
  stage: string
  substage: string
  status: string
  detail: string
}

interface BenchmarkQueueJob {
  id: number
  engine_version_id: number | null
  engine_name: string
  engine_version: string
  build_hash: string
  hardware_key: string
  status: 'queued' | 'running' | 'failed'
  attempt: number
  scheduled_at: string
  started_at: string | null
  next_retry_at: string | null
  error: string
  activity: BenchmarkActivity | null
  benchmarker: { id: number; label: string; status: string | null } | null
}

interface BenchmarkerManagerRow {
  id: number
  label: string
  status: string
  last_seen?: string | null
  hardware?: { reported: boolean; summary: string; detail: string }
  work: BenchmarkQueueJob | null
}

interface EngineNeedingBenchmark {
  id: number
  name: string
  version: string
  build_hash: string
  dockerfile_ready: boolean
}

interface BenchmarkManagerData {
  benchmarkers: BenchmarkerManagerRow[]
  queue: BenchmarkQueueJob[]
  engines_needing_benchmark: EngineNeedingBenchmark[]
}

const data = ref<DashboardData | null>(null)
const loading = ref(true)
const managerLoading = ref(true)
const error = ref('')
const managerError = ref('')
const actionMessage = ref('')
const benchmarkManager = ref<BenchmarkManagerData>({ benchmarkers: [], queue: [], engines_needing_benchmark: [] })
const revokingBenchmarker = ref<number | null>(null)
const queueingEngine = ref<number | null>(null)
const { confirm } = useConfirm()
let benchmarkRefreshTimer: number | undefined

const metrics = computed(() => [
  { label: 'Tournaments', value: data.value?.db_stats.tournaments, to: '/admin/tournaments', icon: 'tournament' },
  { label: 'Engines', value: data.value?.db_stats.engines, to: '/admin/engines', icon: 'engine' },
  { label: 'Rating lists', value: data.value?.db_stats.rating_lists, to: '/admin/ratings', icon: 'category' },
  { label: 'Opening suites', value: data.value?.db_stats.opening_suites, to: '/admin/openings', icon: 'opening' },
  { label: 'Workers', value: data.value?.db_stats.workers, to: '/admin/workers', icon: 'worker' },
])

function engineName(engineId: number): string {
  const engines = data.value?.engines
  if (Array.isArray(engines)) return engines.find((engine) => (engine.id ?? engine.engine_id) === engineId)?.name ?? `Engine ${engineId}`
  const value = engines?.[String(engineId)]
  return typeof value === 'string' ? value : value?.name ?? `Engine ${engineId}`
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.get<DashboardData>('/api/admin/dashboard')
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    loading.value = false
  }
}

async function loadBenchmarkManager(silent = false): Promise<void> {
  if (!silent) managerLoading.value = true
  try {
    benchmarkManager.value = await api.get<BenchmarkManagerData>('/api/admin/benchmarks/manager')
    managerError.value = ''
  } catch (cause) {
    managerError.value = errorText(cause)
  } finally {
    if (!silent) managerLoading.value = false
  }
}

function benchmarkerWork(benchmarker: BenchmarkerManagerRow): string {
  if (!benchmarker.work) return benchmarker.status === 'connected' ? 'Ready for work' : 'No active benchmark'
  const activity = benchmarker.work.activity
  if (!activity) return `Preparing ${benchmarker.work.engine_name}`
  return `${humanize(activity.stage)} · ${humanize(activity.substage)}`
}

function engineQueueStatus(engine: EngineNeedingBenchmark): string {
  return benchmarkManager.value.queue.find((job) => job.build_hash === engine.build_hash)?.status ?? ''
}

async function revokeBenchmarker(benchmarker: BenchmarkerManagerRow): Promise<void> {
  const accepted = await confirm({ title: 'Revoke benchmarker?', message: `Revoke “${benchmarker.label}”? Its credentials will stop working and any active benchmark will return to the queue.`, confirmLabel: 'Revoke benchmarker', tone: 'danger' })
  if (!accepted) return
  revokingBenchmarker.value = benchmarker.id
  try {
    const response = await api.delete<{ message: string }>(`/api/admin/benchmarkers/${benchmarker.id}`)
    actionMessage.value = response.message
    await loadBenchmarkManager(true)
  } catch (cause) {
    managerError.value = errorText(cause)
  } finally {
    revokingBenchmarker.value = null
  }
}

async function queueBenchmark(engine: EngineNeedingBenchmark): Promise<void> {
  queueingEngine.value = engine.id
  try {
    const response = await api.post<{ message: string }>(`/api/admin/engine-versions/${engine.id}/benchmarks/reschedule`)
    actionMessage.value = response.message
    await loadBenchmarkManager(true)
  } catch (cause) {
    managerError.value = errorText(cause)
  } finally {
    queueingEngine.value = null
  }
}

onMounted(async () => {
  await Promise.all([load(), loadBenchmarkManager()])
  benchmarkRefreshTimer = window.setInterval(() => { void loadBenchmarkManager(true) }, 3_000)
})
onBeforeUnmount(() => {
  if (benchmarkRefreshTimer !== undefined) window.clearInterval(benchmarkRefreshTimer)
})
</script>

<template>
  <div class="admin-page dashboard-page">
    <AdminPageHeader title="Dashboard">
      <template #actions><RouterLink class="button button--secondary" to="/admin/updates">Update platform</RouterLink><RouterLink class="button button--primary" to="/admin/tournaments/new">New tournament</RouterLink></template>
    </AdminPageHeader>
    <InlineFeedback :message="error" />
    <InlineFeedback :message="managerError" />
    <InlineFeedback :message="actionMessage" tone="info" />

    <div v-if="loading" class="panel loading-panel" role="status">Loading dashboard…</div>
    <template v-else-if="data">
      <section class="panel benchmark-manager">
        <header class="benchmark-manager__heading">
          <div><h2>Benchmark manager</h2><p>Monitor benchmark clients, control the queue, and schedule missing engine measurements.</p></div>
          <span>{{ benchmarkManager.benchmarkers.filter((item) => item.status === 'connected' || item.status === 'busy').length }} online</span>
        </header>

        <div class="benchmark-manager__section">
          <div class="benchmark-manager__title"><div><h3>Registered benchmarkers</h3><p>Live activity and build progress for every benchmark client.</p></div><strong>{{ benchmarkManager.benchmarkers.length }}</strong></div>
          <div v-if="managerLoading" class="benchmark-manager__loading" role="status">Loading benchmarkers…</div>
          <div v-else-if="benchmarkManager.benchmarkers.length" class="benchmark-table-scroll">
            <table class="benchmark-table">
              <thead><tr><th>Benchmarker</th><th>Status</th><th>Current activity</th><th>Hardware</th><th>Last seen</th><th><span class="sr-only">Actions</span></th></tr></thead>
              <tbody>
                <tr v-for="benchmarker in benchmarkManager.benchmarkers" :key="benchmarker.id">
                  <td><strong>{{ benchmarker.label }}</strong><small>#{{ benchmarker.id }}</small></td>
                  <td><StatusBadge :status="benchmarker.status" /></td>
                  <td class="benchmark-work"><strong>{{ benchmarkerWork(benchmarker) }}</strong><small v-if="benchmarker.work">{{ benchmarker.work.engine_name }} {{ benchmarker.work.engine_version }}<template v-if="benchmarker.work.activity"> · {{ benchmarker.work.activity.detail }}</template></small></td>
                  <td><span>{{ benchmarker.hardware?.summary ?? 'Not reported' }}</span><small>{{ benchmarker.hardware?.detail }}</small></td>
                  <td>{{ formatDate(benchmarker.last_seen) }}</td>
                  <td><button class="button button--danger button--small" type="button" :disabled="revokingBenchmarker === benchmarker.id" @click="revokeBenchmarker(benchmarker)">{{ revokingBenchmarker === benchmarker.id ? 'Revoking…' : 'Revoke' }}</button></td>
                </tr>
              </tbody>
            </table>
          </div>
          <AdminEmptyState v-else title="No benchmarkers registered" description="Connect a benchmarker to start processing engine measurements." />
        </div>

        <div class="benchmark-manager__section">
          <div class="benchmark-manager__title"><div><h3>Engine benchmark queue</h3><p>Running, waiting, and retryable benchmark jobs.</p></div><strong>{{ benchmarkManager.queue.length }}</strong></div>
          <div v-if="benchmarkManager.queue.length" class="benchmark-queue" tabindex="0">
            <div v-for="job in benchmarkManager.queue" :key="job.id" class="benchmark-queue__row">
              <RouterLink v-if="job.engine_version_id" :to="`/admin/engine-versions/${job.engine_version_id}`"><strong>{{ job.engine_name }}</strong><small>{{ job.engine_version }} · Job #{{ job.id }}</small></RouterLink>
              <span v-else><strong>{{ job.engine_name }}</strong><small>{{ job.engine_version }} · Job #{{ job.id }}</small></span>
              <StatusBadge :status="job.status" />
              <span class="benchmark-queue__activity"><strong>{{ job.activity ? `${humanize(job.activity.stage)} · ${humanize(job.activity.substage)}` : job.status === 'queued' ? 'Waiting for a benchmarker' : job.error || 'Awaiting retry' }}</strong><small>{{ job.activity?.detail ?? (job.benchmarker ? `Assigned to ${job.benchmarker.label}` : `Attempt ${job.attempt}`) }}</small></span>
              <time :datetime="job.started_at ?? job.scheduled_at">{{ formatDate(job.started_at ?? job.scheduled_at) }}</time>
            </div>
          </div>
          <AdminEmptyState v-else title="Benchmark queue is empty" description="There are no running, queued, or failed benchmark jobs." />
        </div>

        <div class="benchmark-manager__section">
          <div class="benchmark-manager__title"><div><h3>Engines needing a benchmark</h3><p>Active engine builds without a current benchmark result.</p></div><strong>{{ benchmarkManager.engines_needing_benchmark.length }}</strong></div>
          <div v-if="benchmarkManager.engines_needing_benchmark.length" class="benchmark-needs-list">
            <div v-for="engine in benchmarkManager.engines_needing_benchmark" :key="engine.id">
              <RouterLink :to="`/admin/engine-versions/${engine.id}`"><strong>{{ engine.name }}</strong><small>{{ engine.version }} · {{ engine.build_hash.slice(0, 12) }}</small></RouterLink>
              <StatusBadge v-if="engineQueueStatus(engine)" :status="engineQueueStatus(engine)" />
              <button class="button button--primary button--small" type="button" :disabled="!engine.dockerfile_ready || queueingEngine === engine.id || ['queued', 'running'].includes(engineQueueStatus(engine))" @click="queueBenchmark(engine)">{{ queueingEngine === engine.id ? 'Queueing…' : engineQueueStatus(engine) === 'running' ? 'Running' : engineQueueStatus(engine) === 'queued' ? 'Queued' : engineQueueStatus(engine) === 'failed' ? 'Queue again' : 'Queue benchmark' }}</button>
            </div>
          </div>
          <AdminEmptyState v-else title="All active engines are benchmarked" description="Every active engine build has a current result." />
        </div>
      </section>

      <section class="metric-grid" aria-label="Database totals">
        <RouterLink v-for="metric in metrics" :key="metric.label" class="metric-card" :to="metric.to">
          <span class="metric-card__icon" aria-hidden="true">
            <svg v-if="metric.icon === 'tournament'" viewBox="0 0 24 24"><path d="M7 4h10v4a5 5 0 0 1-10 0V4ZM9 17h6M12 13v4M5 6H3v1a4 4 0 0 0 4 4M19 6h2v1a4 4 0 0 1-4 4M7 20h10" /></svg>
            <svg v-else-if="metric.icon === 'engine'" viewBox="0 0 24 24"><path d="M7 7h10v10H7zM9 4v3m3-3v3m3-3v3M9 17v3m3-3v3m3-3v3M4 9h3m-3 3h3m-3 3h3m10-6h3m-3 3h3m-3 3h3" /></svg>
            <svg v-else-if="metric.icon === 'category'" viewBox="0 0 24 24"><path d="M4 6h16M4 12h10M4 18h7" /></svg>
            <svg v-else-if="metric.icon === 'opening'" viewBox="0 0 24 24"><path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3V4Zm3 16a3 3 0 0 1 0-6h11M9 8h6" /></svg>
            <svg v-else viewBox="0 0 24 24"><rect x="4" y="6" width="16" height="12" rx="2" /><path d="M8 10h.01M12 10h.01M16 10h.01M8 14h8" /></svg>
          </span>
          <span><strong>{{ formatNumber(metric.value) }}</strong><small>{{ metric.label }}</small></span>
          <svg class="metric-card__arrow" aria-hidden="true" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6" /></svg>
        </RouterLink>
      </section>

      <section class="panel system-strip"><div><span>Release</span><code>{{ data.system.version }}</code></div><div><span>Schema</span><strong>v{{ data.system.schema_version }}</strong></div><div v-for="service in data.system.services" :key="service.service"><span>{{ humanize(service.service) }}</span><strong>{{ formatDate(service.last_seen) }}</strong></div></section>

      <div class="dashboard-grid">
        <section class="panel dashboard-panel dashboard-panel--wide">
          <div class="panel-heading"><div><h2>Active tournaments</h2></div><RouterLink to="/admin/tournaments">View all</RouterLink></div>
          <div v-if="data.running_tournaments.length" class="compact-list">
            <RouterLink v-for="tournament in data.running_tournaments" :key="tournament.id" :to="`/admin/tournaments/${tournament.id}`" class="compact-row">
              <span class="compact-row__primary"><strong>{{ tournament.name }}</strong><small>Round {{ tournament.current_round || 1 }} · {{ tournament.config.participants.length }} engines</small></span>
              <StatusBadge :status="tournament.status" />
              <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6" /></svg>
            </RouterLink>
          </div>
          <AdminEmptyState v-else title="Nothing is running">
            <RouterLink class="button button--secondary button--small" to="/admin/tournaments">Open tournaments</RouterLink>
          </AdminEmptyState>
        </section>

        <section class="panel dashboard-panel">
          <div class="panel-heading"><div><h2>Workers</h2></div><RouterLink to="/admin/workers">Manage</RouterLink></div>
          <div v-if="data.workers.length" class="worker-list">
            <RouterLink v-for="row in data.workers.slice(0, 6)" :key="row.worker.id" :to="`/admin/workers/${row.worker.id}`">
              <span class="worker-avatar" aria-hidden="true">{{ row.worker.label.slice(0, 2).toUpperCase() }}</span>
              <span><strong>{{ row.worker.label }}</strong><small>{{ formatDate(row.worker.last_seen) }}</small></span>
              <StatusBadge :status="row.status" />
            </RouterLink>
          </div>
          <AdminEmptyState v-else title="No workers registered" />
        </section>

        <section class="panel dashboard-panel">
          <div class="panel-heading"><div><h2>Live games</h2></div></div>
          <div v-if="data.live_games.length" class="game-list">
            <RouterLink v-for="game in data.live_games.slice(0, 6)" :key="game.id" :to="`/tournaments/${game.tournament_id}?game_id=${game.id}`">
              <span><strong>{{ engineName(game.white_engine_id) }}</strong><small>White</small></span>
              <em>vs</em>
              <span class="game-list__black"><strong>{{ engineName(game.black_engine_id) }}</strong><small>Black</small></span>
            </RouterLink>
          </div>
          <AdminEmptyState v-else title="No live games" />
        </section>

        <section v-if="data.complete_tournaments.length" class="panel dashboard-panel dashboard-panel--wide">
          <div class="panel-heading"><div><h2>Ratings awaiting commit</h2></div></div>
          <div class="commit-list">
            <div v-for="tournament in data.complete_tournaments" :key="tournament.id">
              <span><RouterLink :to="`/admin/tournaments/${tournament.id}`">{{ tournament.name }}</RouterLink><small>{{ tournament.config.participants.length }} participants</small></span>
              <RouterLink class="button button--primary button--small" :to="`/admin/tournaments/${tournament.id}`">Commit results</RouterLink>
            </div>
          </div>
        </section>

        <section class="panel dashboard-panel dashboard-panel--wide">
          <div class="panel-heading"><div><h2>Recent results</h2></div></div>
          <div v-if="data.recent_games.length" class="results-list">
            <RouterLink v-for="game in data.recent_games.slice(0, 8)" :key="game.id" :to="`/tournaments/${game.tournament_id}?game_id=${game.id}`">
              <span>{{ engineName(game.white_engine_id) }}</span><strong>{{ game.result ?? '½-½' }}</strong><span>{{ engineName(game.black_engine_id) }}</span><small>{{ formatDate(game.finished_at) }}</small>
            </RouterLink>
          </div>
          <AdminEmptyState v-else title="No finished games" />
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.dashboard-page { display: grid; gap: 1rem; }
.loading-panel { color: var(--color-text-muted, #64748b); min-height: 12rem; padding: 2rem; }
.benchmark-manager { overflow: hidden; padding: 0; }
.benchmark-manager__heading { align-items: center; background: var(--color-surface-subtle, #f6f8fb); border-bottom: 1px solid var(--color-border, #d9e0ea); display: flex; gap: 1rem; justify-content: space-between; padding: 1rem; }
.benchmark-manager__heading h2, .benchmark-manager__title h3 { margin: 0; }
.benchmark-manager__heading h2 { font-size: 1rem; }
.benchmark-manager__heading p, .benchmark-manager__title p { color: var(--color-text-muted, #64748b); font-size: .7rem; margin: .2rem 0 0; }
.benchmark-manager__heading > span, .benchmark-manager__title > strong { background: var(--color-surface, #fff); border: 1px solid var(--color-border, #d9e0ea); border-radius: 999px; font-size: .68rem; padding: .28rem .55rem; white-space: nowrap; }
.benchmark-manager__section + .benchmark-manager__section { border-top: 1px solid var(--color-border, #d9e0ea); }
.benchmark-manager__title { align-items: center; display: flex; gap: 1rem; justify-content: space-between; padding: .8rem 1rem; }
.benchmark-manager__title h3 { font-size: .84rem; }
.benchmark-manager__loading { color: var(--color-text-muted, #64748b); font-size: .75rem; padding: 1rem; }
.benchmark-table-scroll { overflow-x: auto; }
.benchmark-table { border-collapse: collapse; min-width: 68rem; width: 100%; }
.benchmark-table th { color: var(--color-text-muted, #64748b); font-size: .63rem; letter-spacing: .04em; padding: .55rem .75rem; text-align: left; text-transform: uppercase; }
.benchmark-table td { border-top: 1px solid var(--color-border, #d9e0ea); font-size: .72rem; padding: .65rem .75rem; vertical-align: middle; }
.benchmark-table td:first-child, .benchmark-table td:nth-child(4), .benchmark-work { display: grid; min-width: 0; }
.benchmark-table small, .benchmark-queue small, .benchmark-needs-list small { color: var(--color-text-muted, #64748b); display: block; font-size: .64rem; margin-top: .15rem; }
.benchmark-work { max-width: 24rem; }
.benchmark-work strong, .benchmark-work small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.benchmark-table td:last-child { text-align: right; }
.benchmark-queue { border-top: 1px solid var(--color-border, #d9e0ea); max-height: 22rem; overflow: auto; }
.benchmark-queue__row { align-items: center; border-bottom: 1px solid var(--color-border, #d9e0ea); display: grid; gap: .75rem; grid-template-columns: minmax(11rem, 1fr) auto minmax(15rem, 2fr) auto; padding: .65rem 1rem; }
.benchmark-queue__row:last-child { border-bottom: 0; }
.benchmark-queue__row > a, .benchmark-queue__row > span:first-child, .benchmark-queue__activity { color: inherit; display: grid; min-width: 0; text-decoration: none; }
.benchmark-queue__row > a:hover strong { color: var(--color-accent, #315fcc); }
.benchmark-queue__activity strong, .benchmark-queue__activity small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.benchmark-queue time { color: var(--color-text-muted, #64748b); font-size: .66rem; white-space: nowrap; }
.benchmark-needs-list { border-top: 1px solid var(--color-border, #d9e0ea); display: grid; }
.benchmark-needs-list > div { align-items: center; border-bottom: 1px solid var(--color-border, #d9e0ea); display: grid; gap: .75rem; grid-template-columns: minmax(0, 1fr) auto auto; padding: .65rem 1rem; }
.benchmark-needs-list > div:last-child { border-bottom: 0; }
.benchmark-needs-list a { color: inherit; display: grid; min-width: 0; text-decoration: none; }
.benchmark-needs-list a:hover strong { color: var(--color-accent, #315fcc); }
.metric-grid { display: grid; gap: .75rem; grid-template-columns: repeat(5, minmax(0, 1fr)); }
.system-strip { display: flex; flex-wrap: wrap; gap: 1.2rem; padding: .75rem 1rem; }.system-strip > div { display: grid; gap: .15rem; }.system-strip span { color: var(--color-text-muted, #64748b); font-size: .62rem; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }.system-strip strong, .system-strip code { font-size: .72rem; }
.metric-card { align-items: center; background: var(--color-surface, #fff); border: 1px solid var(--color-border, #d9e0ea); border-radius: var(--radius-lg, .8rem); color: inherit; display: flex; gap: .7rem; min-width: 0; padding: .8rem; text-decoration: none; transition: border-color 120ms ease, transform 120ms ease; }
.metric-card:hover { border-color: var(--color-accent, #315fcc); transform: translateY(-1px); }
.metric-card__icon { align-items: center; background: var(--color-surface-subtle, #f1f5f9); border-radius: .5rem; color: var(--color-accent, #315fcc); display: flex; flex: 0 0 auto; height: 2rem; justify-content: center; width: 2rem; }
.metric-card__icon svg, .metric-card__arrow, .compact-row > svg { fill: none; height: 1rem; stroke: currentColor; stroke-linecap: round; stroke-linejoin: round; stroke-width: 1.7; width: 1rem; }
.metric-card > span:nth-child(2) { display: grid; min-width: 0; }
.metric-card strong { font-size: 1.05rem; }
.metric-card small { color: var(--color-text-muted, #64748b); font-size: .68rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.metric-card__arrow { color: var(--color-text-muted, #64748b); margin-left: auto; }
.dashboard-grid { display: grid; gap: .9rem; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.dashboard-panel { min-width: 0; overflow: hidden; padding: 0; }
.dashboard-panel--wide { grid-column: 1 / -1; }
.panel-heading { align-items: flex-start; border-bottom: 1px solid var(--color-border, #d9e0ea); display: flex; gap: 1rem; justify-content: space-between; padding: .9rem 1rem; }
.panel-heading h2 { font-size: .9rem; margin: 0; }
.panel-heading p { color: var(--color-text-muted, #64748b); font-size: .72rem; margin: .2rem 0 0; }
.panel-heading > a { font-size: .75rem; }
.compact-list, .worker-list, .game-list, .commit-list, .results-list { display: grid; }
.compact-row { align-items: center; border-bottom: 1px solid var(--color-border, #d9e0ea); color: inherit; display: grid; gap: .8rem; grid-template-columns: minmax(0, 1fr) auto auto; padding: .75rem 1rem; text-decoration: none; }
.compact-row:last-child { border-bottom: 0; }
.compact-row:hover { background: var(--color-surface-subtle, #f6f8fb); }
.compact-row__primary, .worker-list a > span:nth-child(2), .commit-list div > span { display: grid; min-width: 0; }
.compact-row strong, .worker-list strong { font-size: .8rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.compact-row small, .worker-list small, .commit-list small { color: var(--color-text-muted, #64748b); font-size: .68rem; margin-top: .15rem; }
.worker-list a { align-items: center; border-bottom: 1px solid var(--color-border, #d9e0ea); color: inherit; display: grid; gap: .65rem; grid-template-columns: auto minmax(0, 1fr) auto; padding: .7rem 1rem; text-decoration: none; }
.worker-list a:last-child { border-bottom: 0; }
.worker-avatar { align-items: center; background: var(--color-surface-subtle, #f1f5f9); border-radius: 50%; color: var(--color-text-muted, #64748b); display: flex; font-size: .62rem; font-weight: 750; height: 1.8rem; justify-content: center; width: 1.8rem; }
.game-list a { align-items: center; border-bottom: 1px solid var(--color-border, #d9e0ea); color: inherit; display: grid; gap: .5rem; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); padding: .75rem 1rem; text-decoration: none; }
.game-list a:last-child { border-bottom: 0; }
.game-list a > span { display: grid; min-width: 0; }
.game-list strong { font-size: .78rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.game-list small { color: var(--color-text-muted, #64748b); font-size: .65rem; }
.game-list em { color: var(--color-text-muted, #64748b); font-size: .65rem; font-style: normal; }
.game-list__black { text-align: right; }
.commit-list > div { align-items: center; border-bottom: 1px solid var(--color-border, #d9e0ea); display: flex; gap: .8rem; justify-content: space-between; padding: .75rem 1rem; }
.commit-list > div:last-child { border-bottom: 0; }
.commit-list a { font-size: .8rem; font-weight: 650; }
.results-list a { align-items: center; border-bottom: 1px solid var(--color-border, #d9e0ea); color: inherit; display: grid; font-size: .78rem; gap: .65rem; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) auto; padding: .7rem 1rem; text-decoration: none; }
.results-list a:last-child { border-bottom: 0; }
.results-list a span:nth-child(3) { text-align: right; }
.results-list strong { background: var(--color-surface-subtle, #f1f5f9); border-radius: .3rem; padding: .25rem .4rem; }
.results-list small { color: var(--color-text-muted, #64748b); font-size: .68rem; }
@media (max-width: 68rem) { .metric-grid { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 48rem) { .dashboard-grid { grid-template-columns: 1fr; } .dashboard-panel--wide { grid-column: auto; } .metric-grid { grid-template-columns: repeat(2, 1fr); } .benchmark-queue__row { grid-template-columns: minmax(0, 1fr) auto; } .benchmark-queue__activity { grid-column: 1 / -1; } .benchmark-queue time { display: none; } }
@media (max-width: 32rem) { .metric-grid { grid-template-columns: 1fr; } .results-list a { grid-template-columns: 1fr auto 1fr; } .results-list small { display: none; } .benchmark-manager__heading { align-items: flex-start; } .benchmark-manager__heading > span { display: none; } .benchmark-needs-list > div { grid-template-columns: minmax(0, 1fr) auto; } .benchmark-needs-list .status-badge { grid-column: 1 / -1; grid-row: 2; width: fit-content; } }
</style>
