<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import { useConfirm } from '@/composables/useConfirm'
import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import StatusBadge from '@/components/admin/StatusBadge.vue'
import WorkerTokenPanel from '@/components/admin/WorkerTokenPanel.vue'
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

interface BenchmarkManagerJob {
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
  finished_at: string | null
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
  work: BenchmarkManagerJob | null
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
  queue: BenchmarkManagerJob[]
  failures: BenchmarkManagerJob[]
  engines_needing_benchmark: EngineNeedingBenchmark[]
}

interface MintedBenchmarker {
  id: number
  token: string
  expires_at: string
  start_command: string
  message: string
}

const data = ref<DashboardData | null>(null)
const loading = ref(true)
const managerLoading = ref(true)
const error = ref('')
const managerError = ref('')
const actionMessage = ref('')
const showBenchmarkerCreate = ref(false)
const benchmarkerLabel = ref('')
const creatingBenchmarker = ref(false)
const mintedBenchmarker = ref<MintedBenchmarker | null>(null)
const benchmarkManager = ref<BenchmarkManagerData>({ benchmarkers: [], queue: [], failures: [], engines_needing_benchmark: [] })
const revokingBenchmarker = ref<number | null>(null)
const queueingEngine = ref<number | null>(null)
const forgettingFailure = ref<number | null>(null)
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

async function createBenchmarker(): Promise<void> {
  creatingBenchmarker.value = true
  try {
    const response = await api.post<MintedBenchmarker>('/api/admin/benchmarkers', {
      body: { label: benchmarkerLabel.value.trim() || 'benchmarker', ttl_seconds: 7200 },
    })
    mintedBenchmarker.value = response
    benchmarkerLabel.value = ''
    showBenchmarkerCreate.value = false
    actionMessage.value = response.message
    await loadBenchmarkManager(true)
  } catch (cause) {
    managerError.value = errorText(cause)
  } finally {
    creatingBenchmarker.value = false
  }
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

async function forgetFailure(job: BenchmarkManagerJob): Promise<void> {
  const accepted = await confirm({ title: 'Forget failed benchmark?', message: `Forget the failed benchmark for ${job.engine_name} ${job.engine_version}?`, confirmLabel: 'Forget', tone: 'danger' })
  if (!accepted) return
  forgettingFailure.value = job.id
  try {
    const response = await api.delete<{ message: string }>(`/api/admin/benchmark-jobs/${job.id}`)
    actionMessage.value = response.message
    await loadBenchmarkManager(true)
  } catch (cause) {
    managerError.value = errorText(cause)
  } finally {
    forgettingFailure.value = null
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
    <WorkerTokenPanel v-if="mintedBenchmarker" :token="mintedBenchmarker.token" :expires-at="mintedBenchmarker.expires_at" :start-command="mintedBenchmarker.start_command" title="One-time benchmarker credential" warning="Copy this token or start command now. It cannot be recovered after you leave or refresh this page." />
    <form v-if="showBenchmarkerCreate" class="panel benchmarker-create" @submit.prevent="createBenchmarker">
      <div><h2>Register a benchmarker</h2><p>The one-time credential expires after two hours.</p></div>
      <label><span>Benchmarker label</span><input v-model="benchmarkerLabel" class="input" maxlength="80" autofocus></label>
      <div class="button-row"><button class="button button--ghost" type="button" @click="showBenchmarkerCreate = false">Cancel</button><button class="button button--primary" type="submit" :disabled="creatingBenchmarker">{{ creatingBenchmarker ? 'Generating…' : 'Generate credential' }}</button></div>
    </form>

    <div v-if="loading" class="panel loading-panel" role="status">Loading dashboard…</div>
    <template v-else-if="data">
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

        <section class="panel dashboard-panel dashboard-panel--wide benchmark-ops">
          <div class="panel-heading"><div><h2>Benchmark operations</h2><p>Clients, pending work, and engine coverage.</p></div><div class="benchmark-ops__actions"><span>{{ benchmarkManager.benchmarkers.filter((item) => item.status === 'connected' || item.status === 'busy').length }} online</span><button class="button button--primary button--small" type="button" @click="showBenchmarkerCreate = !showBenchmarkerCreate">New benchmarker</button></div></div>
          <div v-if="managerLoading" class="benchmark-ops__loading" role="status">Loading benchmark operations…</div>
          <div v-else class="benchmark-ops__grid">
            <section class="benchmark-ops__column">
              <header><h3>Benchmarkers</h3><span>{{ benchmarkManager.benchmarkers.length }}</span></header>
              <div v-if="benchmarkManager.benchmarkers.length" class="benchmark-ops__list" tabindex="0">
                <div v-for="benchmarker in benchmarkManager.benchmarkers" :key="benchmarker.id" class="benchmark-ops__row benchmarker-row">
                  <span class="benchmark-ops__copy"><strong>{{ benchmarker.label }}</strong><small :title="benchmarker.work?.activity?.detail">{{ benchmarker.work ? `${benchmarker.work.engine_name} · ${benchmarkerWork(benchmarker)}` : benchmarkerWork(benchmarker) }}</small></span>
                  <StatusBadge :status="benchmarker.status" />
                  <button class="icon-button icon-button--danger" type="button" :disabled="revokingBenchmarker === benchmarker.id" :aria-label="`Revoke ${benchmarker.label}`" title="Revoke benchmarker" @click="revokeBenchmarker(benchmarker)"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18" /></svg></button>
                </div>
              </div>
              <p v-else class="benchmark-ops__empty">No benchmarkers registered.</p>
            </section>

            <section class="benchmark-ops__column">
              <header><h3>Queue</h3><span>{{ benchmarkManager.queue.length }}</span></header>
              <div v-if="benchmarkManager.queue.length" class="benchmark-ops__list" tabindex="0">
                <div v-for="job in benchmarkManager.queue" :key="job.id" class="benchmark-ops__row queue-row">
                  <RouterLink v-if="job.engine_version_id" class="benchmark-ops__copy" :to="`/admin/engine-versions/${job.engine_version_id}`"><strong>{{ job.engine_name }} <small>{{ job.engine_version }}</small></strong><small :title="job.activity?.detail ?? job.error">{{ job.activity ? humanize(job.activity.substage) : job.status === 'queued' ? 'Waiting for a benchmarker' : 'Benchmark in progress' }}</small></RouterLink>
                  <span v-else class="benchmark-ops__copy"><strong>{{ job.engine_name }} <small>{{ job.engine_version }}</small></strong><small>{{ job.activity ? humanize(job.activity.substage) : job.error || 'Waiting' }}</small></span>
                  <StatusBadge :status="job.status" />
                </div>
              </div>
              <p v-else class="benchmark-ops__empty">Queue is empty.</p>
            </section>

            <section class="benchmark-ops__column">
              <header><h3>Failures</h3><span>{{ benchmarkManager.failures.length }}</span></header>
              <div v-if="benchmarkManager.failures.length" class="benchmark-ops__list" tabindex="0">
                <div v-for="job in benchmarkManager.failures" :key="job.id" class="benchmark-ops__row failure-row">
                  <RouterLink v-if="job.engine_version_id" class="benchmark-ops__copy" :to="`/admin/engine-versions/${job.engine_version_id}`"><strong>{{ job.engine_name }} <small>{{ job.engine_version }}</small></strong><small :title="job.error">{{ job.error || `Failed ${formatDate(job.finished_at)}` }}</small></RouterLink>
                  <span v-else class="benchmark-ops__copy"><strong>{{ job.engine_name }} <small>{{ job.engine_version }}</small></strong><small :title="job.error">{{ job.error || `Failed ${formatDate(job.finished_at)}` }}</small></span>
                  <StatusBadge :status="job.status" />
                  <button class="button button--danger button--small" type="button" :disabled="forgettingFailure === job.id" @click="forgetFailure(job)">{{ forgettingFailure === job.id ? 'Forgetting…' : 'Forget' }}</button>
                </div>
              </div>
              <p v-else class="benchmark-ops__empty">No failed benchmarks.</p>
            </section>

            <section class="benchmark-ops__column">
              <header><h3>Needs benchmark</h3><span>{{ benchmarkManager.engines_needing_benchmark.length }}</span></header>
              <div v-if="benchmarkManager.engines_needing_benchmark.length" class="benchmark-ops__list" tabindex="0">
                <div v-for="engine in benchmarkManager.engines_needing_benchmark" :key="engine.id" class="benchmark-ops__row needs-row">
                  <RouterLink class="benchmark-ops__copy" :to="`/admin/engine-versions/${engine.id}`"><strong>{{ engine.name }}</strong><small>{{ engine.version }} · {{ engine.build_hash.slice(0, 8) }}</small></RouterLink>
                  <button class="button button--primary button--small" type="button" :disabled="!engine.dockerfile_ready || queueingEngine === engine.id" @click="queueBenchmark(engine)">{{ queueingEngine === engine.id ? 'Queueing…' : 'Queue' }}</button>
                </div>
              </div>
              <p v-else class="benchmark-ops__empty">All engine versions are benchmarked or queued.</p>
            </section>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.dashboard-page { display: grid; gap: 1rem; }
.loading-panel { color: var(--color-text-muted, #64748b); min-height: 12rem; padding: 2rem; }
.benchmark-ops .panel-heading { align-items: center; padding: .7rem .9rem; }
.benchmark-ops__actions { align-items: center; display: flex; gap: .55rem; }
.benchmark-ops__actions > span { background: var(--color-surface-subtle, #f1f5f9); border-radius: 999px; font-size: .65rem; padding: .25rem .5rem; }
.benchmarker-create { align-items: end; display: grid; gap: 1rem; grid-template-columns: minmax(16rem, 1.5fr) minmax(14rem, 1fr) auto; padding: 1rem; }
.benchmarker-create h2 { font-size: .9rem; margin: 0; }
.benchmarker-create p { color: var(--color-text-muted, #64748b); font-size: .72rem; margin: .2rem 0 0; }
.benchmarker-create label { display: grid; font-size: .76rem; font-weight: 650; gap: .35rem; }
.benchmark-ops__loading { color: var(--color-text-muted, #64748b); font-size: .72rem; padding: .9rem; }
.benchmark-ops__grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); }
.benchmark-ops__column { min-width: 0; }
.benchmark-ops__column + .benchmark-ops__column { border-left: 1px solid var(--color-border, #d9e0ea); }
.benchmark-ops__column > header { align-items: center; background: var(--color-surface-subtle, #f6f8fb); border-bottom: 1px solid var(--color-border, #d9e0ea); display: flex; justify-content: space-between; padding: .5rem .65rem; }
.benchmark-ops__column h3 { font-size: .7rem; letter-spacing: .02em; margin: 0; }
.benchmark-ops__column > header span { color: var(--color-text-muted, #64748b); font-size: .64rem; }
.benchmark-ops__list { max-height: 13rem; overflow-y: auto; }
.benchmark-ops__row { align-items: center; border-bottom: 1px solid var(--color-border, #d9e0ea); display: grid; gap: .45rem; min-height: 3rem; padding: .45rem .6rem; }
.benchmark-ops__row:last-child { border-bottom: 0; }
.benchmarker-row { grid-template-columns: minmax(0, 1fr) auto auto; }
.queue-row { grid-template-columns: minmax(0, 1fr) auto; }
.failure-row { grid-template-columns: minmax(0, 1fr) auto auto; }
.needs-row { grid-template-columns: minmax(0, 1fr) auto; }
.benchmark-ops__copy { color: inherit; display: block; min-width: 0; text-decoration: none; }
.benchmark-ops__copy strong { display: block; font-size: .72rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.benchmark-ops__copy strong small { display: inline; font-weight: 500; }
.benchmark-ops__copy > small, .benchmark-ops__copy strong small { color: var(--color-text-muted, #64748b); font-size: .61rem; }
.benchmark-ops__copy > small { display: block; margin-top: .12rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.benchmark-ops__copy:hover strong { color: var(--color-accent, #315fcc); }
.benchmark-ops__row .icon-button { height: 1.65rem; width: 1.65rem; }
.benchmark-ops__row .icon-button svg { fill: none; height: .85rem; stroke: currentColor; stroke-linecap: round; stroke-width: 1.8; width: .85rem; }
.benchmark-ops__row .button { min-height: 1.75rem; padding: .3rem .5rem; }
.benchmark-ops__empty { color: var(--color-text-muted, #64748b); font-size: .68rem; margin: 0; padding: .8rem; }
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
@media (max-width: 68rem) { .metric-grid { grid-template-columns: repeat(3, 1fr); } .benchmark-ops__grid { grid-template-columns: 1fr; } .benchmark-ops__column + .benchmark-ops__column { border-left: 0; border-top: 1px solid var(--color-border, #d9e0ea); } }
@media (max-width: 48rem) { .dashboard-grid { grid-template-columns: 1fr; } .dashboard-panel--wide { grid-column: auto; } .metric-grid { grid-template-columns: repeat(2, 1fr); } .benchmarker-create { align-items: stretch; grid-template-columns: 1fr; } }
@media (max-width: 32rem) { .metric-grid { grid-template-columns: 1fr; } .results-list a { grid-template-columns: 1fr auto 1fr; } .results-list small { display: none; } }
</style>
