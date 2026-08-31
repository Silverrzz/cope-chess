<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import { useToast } from '@/composables/useToast'
import { errorText, formatDate } from '@/components/admin/format'

interface EngineChoice {
  id: number
  family_id: number
  name: string
  author: string
  version: string
  repository: string
  distribution: 'managed' | 'worker_local'
  artifact_ready: boolean
  active: boolean
}

interface WorkerChoice { id: number; label: string; status: string }

interface JobItem {
  id: number
  engine_id: number
  engine_name: string
  engine_version: string
  position: number
  status: string
  result: { matched_name?: string; option_line?: string; elapsed_ms?: number }
  error: string
}

interface ToolJob {
  id: number
  status: string
  input: { option_name?: string }
  worker?: { id: number; label: string } | null
  total_items: number
  completed_items: number
  attempt: number
  error: string
  created_at: string
  started_at?: string | null
  finished_at?: string | null
  items?: JobItem[]
}

interface ContextResponse { engines: EngineChoice[]; workers: WorkerChoice[]; recent_jobs: ToolJob[] }

const route = useRoute()
const router = useRouter()
const toast = useToast()
const context = ref<ContextResponse | null>(null)
const loading = ref(true)
const running = ref(false)
const error = ref('')
const optionName = ref('')
const engineQuery = ref('')
const selectedIds = ref<number[]>([])
const engineFilter = ref<'all' | 'selected' | 'cached'>('all')
const resultFilter = ref<'all' | 'supported' | 'unsupported' | 'failed'>('all')
const resultQuery = ref('')
const activeJob = ref<ToolJob | null>(null)
let pollTimer: number | undefined

const filteredEngines = computed(() => {
  const needle = engineQuery.value.trim().toLowerCase()
  return (context.value?.engines ?? []).filter((engine) => {
    if (engineFilter.value === 'selected' && !selectedIds.value.includes(engine.id)) return false
    if (engineFilter.value === 'cached' && !engine.artifact_ready) return false
    return !needle || `${engine.name} ${engine.version} ${engine.author} ${engine.repository}`.toLowerCase().includes(needle)
  })
})

const allVisibleSelected = computed(() => filteredEngines.value.length > 0 && filteredEngines.value.every((engine) => selectedIds.value.includes(engine.id)))
const selectedNames = computed(() => {
  const selected = new Set(selectedIds.value)
  return (context.value?.engines ?? []).filter((engine) => selected.has(engine.id))
})
const progress = computed(() => activeJob.value ? Math.round(activeJob.value.completed_items / activeJob.value.total_items * 100) : 0)
const resultCounts = computed(() => {
  const items = activeJob.value?.items ?? []
  return {
    supported: items.filter((item) => item.status === 'supported').length,
    unsupported: items.filter((item) => item.status === 'unsupported').length,
    failed: items.filter((item) => item.status === 'failed').length,
  }
})
const filteredResults = computed(() => {
  const needle = resultQuery.value.trim().toLowerCase()
  return (activeJob.value?.items ?? []).filter((item) =>
    (resultFilter.value === 'all' || item.status === resultFilter.value) &&
    (!needle || `${item.engine_name} ${item.engine_version} ${item.result.matched_name ?? ''}`.toLowerCase().includes(needle)),
  )
})
const active = computed(() => activeJob.value?.status === 'queued' || activeJob.value?.status === 'running')

async function loadContext(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    context.value = await api.get<ContextResponse>('/api/admin/tools/who-has-this')
    const jobId = Number(route.query.job)
    if (Number.isInteger(jobId) && jobId > 0) await loadJob(jobId)
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    loading.value = false
  }
}

function toggleEngine(engineId: number): void {
  selectedIds.value = selectedIds.value.includes(engineId)
    ? selectedIds.value.filter((id) => id !== engineId)
    : [...selectedIds.value, engineId]
}

function toggleVisible(): void {
  const visible = new Set(filteredEngines.value.map((engine) => engine.id))
  if (allVisibleSelected.value) selectedIds.value = selectedIds.value.filter((id) => !visible.has(id))
  else selectedIds.value = [...new Set([...selectedIds.value, ...visible])]
}

async function runTool(): Promise<void> {
  if (!optionName.value.trim() || !selectedIds.value.length) return
  running.value = true
  error.value = ''
  stopPolling()
  try {
    const response = await api.post<{ job: ToolJob; message: string }>('/api/admin/tools/who-has-this', {
      body: { option_name: optionName.value, engine_ids: selectedIds.value },
    })
    activeJob.value = response.job
    await router.replace({ query: { ...route.query, job: response.job.id } })
    toast.success(response.message)
    schedulePoll()
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    running.value = false
  }
}

async function loadJob(jobId: number): Promise<void> {
  stopPolling()
  try {
    const response = await api.get<{ job: ToolJob }>(`/api/admin/tools/jobs/${jobId}`)
    activeJob.value = response.job
    optionName.value = response.job.input.option_name ?? ''
    selectedIds.value = response.job.items?.map((item) => item.engine_id) ?? selectedIds.value
    if (response.job.status === 'queued' || response.job.status === 'running') schedulePoll()
  } catch (cause) {
    error.value = errorText(cause)
  }
}

function schedulePoll(): void {
  stopPolling()
  pollTimer = window.setTimeout(async () => {
    if (!activeJob.value) return
    await loadJob(activeJob.value.id)
  }, 1000)
}

function stopPolling(): void {
  if (pollTimer !== undefined) window.clearTimeout(pollTimer)
  pollTimer = undefined
}

async function cancelJob(): Promise<void> {
  if (!activeJob.value || !['queued', 'running'].includes(activeJob.value.status)) return
  try {
    const response = await api.post<{ message: string }>(`/api/admin/tools/jobs/${activeJob.value.id}/cancel`)
    toast.success(response.message)
    await loadJob(activeJob.value.id)
  } catch (cause) {
    toast.error(cause)
  }
}

function openRecent(job: ToolJob): void {
  router.replace({ query: { ...route.query, job: job.id } })
  loadJob(job.id)
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = { pending: 'Waiting', running: 'Inspecting', supported: 'Supported', unsupported: 'Not supported', failed: 'Could not inspect', queued: 'Queued', completed: 'Complete', cancelled: 'Cancelled' }
  return labels[status] ?? status
}

function duration(milliseconds?: number): string {
  if (milliseconds === undefined) return ''
  return milliseconds < 1000 ? `${milliseconds} ms` : `${(milliseconds / 1000).toFixed(1)} s`
}

onMounted(loadContext)
onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="admin-page page-stack who-page">
    <AdminPageHeader title="Who Has This">
      <template #actions><BaseButton variant="ghost" to="/admin/tools"><template #icon><AppIcon name="arrow-left" :size="16" /></template>All tools</BaseButton></template>
    </AdminPageHeader>
    <InlineFeedback :message="error" />

    <div class="tool-layout">
      <main class="tool-main">
        <section class="query-panel panel">
          <div class="query-panel__header">
            <div class="step-number">1</div>
            <div><span class="eyebrow">Query</span><h2>UCI option</h2><p>Matching ignores letter case and repeated spaces.</p></div>
          </div>
          <div class="option-row">
            <BaseInput v-model="optionName" label="Option name" icon="search" placeholder="e.g. UCI_Chess960" list="uci-option-suggestions" :disabled="active" />
            <datalist id="uci-option-suggestions"><option value="UCI_Chess960"/><option value="SyzygyPath"/><option value="MultiPV"/><option value="Ponder"/><option value="EvalFile"/><option value="Move Overhead"/></datalist>
            <div class="query-tip"><AppIcon name="info" :size="16" /><span>The worker starts every selected engine and reads its complete <code>uci</code> response.</span></div>
          </div>
        </section>

        <section class="engine-panel panel">
          <div class="engine-panel__header">
            <div class="query-panel__title"><div class="step-number">2</div><div><span class="eyebrow">Scope</span><h2>Engine versions</h2></div></div>
            <span class="selection-count"><strong>{{ selectedIds.length }}</strong> selected</span>
          </div>
          <div class="engine-toolbar">
            <div class="filter-tabs" role="tablist" aria-label="Engine filter">
              <button v-for="filter in (['all','selected','cached'] as const)" :key="filter" type="button" :class="{ active: engineFilter === filter }" @click="engineFilter = filter">{{ filter === 'cached' ? 'Artifact ready' : filter }}</button>
            </div>
            <label class="engine-search"><AppIcon name="search" :size="15" /><input v-model="engineQuery" type="search" placeholder="Filter engines…" /></label>
            <button class="select-visible" type="button" :disabled="!filteredEngines.length || active" @click="toggleVisible">{{ allVisibleSelected ? 'Clear visible' : 'Select visible' }}</button>
          </div>
          <div v-if="loading" class="engine-loading">Loading engine versions…</div>
          <div v-else class="engine-list" :class="{ 'engine-list--empty': !filteredEngines.length }">
            <label v-for="engine in filteredEngines" :key="engine.id" class="engine-row" :class="{ 'engine-row--selected': selectedIds.includes(engine.id) }">
              <input type="checkbox" :checked="selectedIds.includes(engine.id)" :disabled="active" @change="toggleEngine(engine.id)" />
              <span class="engine-mark">{{ engine.name.charAt(0).toUpperCase() }}</span>
              <span class="engine-copy"><strong>{{ engine.name }}</strong><small>{{ engine.author || 'Unknown author' }}</small></span>
              <span class="version-pill">{{ engine.version }}</span>
              <span class="cache-state" :class="{ 'cache-state--build': !engine.artifact_ready }"><AppIcon :name="engine.artifact_ready ? 'archive' : 'refresh'" :size="13" />{{ engine.distribution === 'worker_local' ? 'Worker-local' : engine.artifact_ready ? 'Artifact' : 'Build' }}</span>
            </label>
            <span v-if="!filteredEngines.length">No engine versions match this filter.</span>
          </div>
          <div v-if="selectedNames.length" class="selection-strip">
            <span v-for="engine in selectedNames.slice(0, 6)" :key="engine.id">{{ engine.name }} <b>{{ engine.version }}</b><button type="button" :disabled="active" :aria-label="`Remove ${engine.name} ${engine.version}`" @click="toggleEngine(engine.id)">×</button></span>
            <span v-if="selectedNames.length > 6" class="selection-strip__more">+{{ selectedNames.length - 6 }} more</span>
          </div>
          <div class="run-bar">
            <div class="worker-readiness"><span :class="{ online: context?.workers.length }"/><span v-if="context?.workers.length"><strong>{{ context.workers.length }}</strong> connected worker{{ context.workers.length === 1 ? '' : 's' }} ready to claim</span><span v-else>No workers connected; the run will wait safely in the queue.</span></div>
            <BaseButton variant="primary" size="large" :loading="running" :disabled="active || !selectedIds.length || !optionName.trim()" @click="runTool"><template #icon><AppIcon name="play" :size="16" /></template>Run inspection</BaseButton>
          </div>
        </section>

        <section v-if="activeJob" class="results-panel panel">
          <div class="results-head">
            <div><span class="eyebrow">Run #{{ activeJob.id }}</span><h2>{{ activeJob.input.option_name }}</h2><p>{{ activeJob.worker ? `Claimed by ${activeJob.worker.label}` : 'Waiting for an available worker' }}</p></div>
            <span class="run-status" :class="`run-status--${activeJob.status}`"><span />{{ statusLabel(activeJob.status) }}</span>
          </div>
          <div v-if="active" class="progress-block">
            <div class="progress-copy"><span>{{ activeJob.completed_items }} of {{ activeJob.total_items }} engines inspected</span><strong>{{ progress }}%</strong></div>
            <div class="progress-track"><span :style="{ width: `${progress}%` }" /></div>
            <BaseButton variant="danger" size="small" @click="cancelJob"><template #icon><AppIcon name="x-circle" :size="14" /></template>Cancel job</BaseButton>
          </div>
          <div class="result-summary">
            <button type="button" :class="{ active: resultFilter === 'supported' }" @click="resultFilter = 'supported'"><span class="summary-icon summary-icon--yes"><AppIcon name="check" :size="17" /></span><span><strong>{{ resultCounts.supported }}</strong><small>Support it</small></span></button>
            <button type="button" :class="{ active: resultFilter === 'unsupported' }" @click="resultFilter = 'unsupported'"><span class="summary-icon summary-icon--no"><AppIcon name="close" :size="17" /></span><span><strong>{{ resultCounts.unsupported }}</strong><small>Do not</small></span></button>
            <button type="button" :class="{ active: resultFilter === 'failed' }" @click="resultFilter = 'failed'"><span class="summary-icon summary-icon--error"><AppIcon name="alert-circle" :size="17" /></span><span><strong>{{ resultCounts.failed }}</strong><small>Errors</small></span></button>
          </div>
          <div class="results-toolbar">
            <button type="button" :class="{ active: resultFilter === 'all' }" @click="resultFilter = 'all'">All {{ activeJob.total_items }}</button>
            <label><AppIcon name="search" :size="14" /><input v-model="resultQuery" type="search" placeholder="Filter results…" /></label>
          </div>
          <div class="result-list">
            <article v-for="item in filteredResults" :key="item.id" class="result-row">
              <span class="result-state" :class="`result-state--${item.status}`"><AppIcon :name="item.status === 'supported' ? 'check' : item.status === 'unsupported' ? 'close' : item.status === 'failed' ? 'alert-circle' : item.status === 'running' ? 'refresh' : 'clock'" :size="16" /></span>
              <span class="result-engine"><strong>{{ item.engine_name }}</strong><small>{{ item.engine_version }}</small></span>
              <span v-if="item.status === 'supported'" class="declaration"><code>{{ item.result.option_line }}</code><small>Matched as {{ item.result.matched_name }}</small></span>
              <span v-else-if="item.status === 'unsupported'" class="declaration"><strong>No matching option</strong><small>The engine completed its UCI handshake normally.</small></span>
              <span v-else-if="item.status === 'failed'" class="declaration declaration--error"><strong>Inspection failed</strong><small>{{ item.error }}</small></span>
              <span v-else class="declaration"><strong>{{ statusLabel(item.status) }}</strong><small>{{ item.status === 'running' ? 'Building or starting the engine.' : 'Waiting for the worker.' }}</small></span>
              <span class="result-duration">{{ duration(item.result.elapsed_ms) }}</span>
            </article>
          </div>
          <InlineFeedback v-if="activeJob.error" :message="activeJob.error" />
        </section>
      </main>

      <aside class="tool-aside">
        <section class="aside-card panel"><span class="eyebrow">How it works</span><ol><li><span>1</span><p><strong>Claim</strong>A connected worker takes the whole run.</p></li><li><span>2</span><p><strong>Prepare</strong>Cached artifacts are reused; missing builds are created once.</p></li><li><span>3</span><p><strong>Inspect</strong>Each engine returns its real UCI option declaration.</p></li></ol></section>
        <section class="aside-card panel"><span class="eyebrow">Recent runs</span><div v-if="context?.recent_jobs.length" class="history"><button v-for="job in context.recent_jobs" :key="job.id" type="button" :class="{ active: activeJob?.id === job.id }" @click="openRecent(job)"><span><strong>{{ job.input.option_name }}</strong><small>{{ formatDate(job.created_at) }}</small></span><span class="history__status" :class="`history__status--${job.status}`">{{ statusLabel(job.status) }}</span></button></div><p v-else class="empty-history">No runs yet.</p></section>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.who-page{gap:1.35rem}.tool-layout{align-items:start;display:grid;gap:1rem;grid-template-columns:minmax(0,1fr) 17rem}.tool-main,.tool-aside{display:grid;gap:1rem}.tool-aside{position:sticky;top:calc(var(--header-height) + 1rem)}.panel{overflow:hidden}.query-panel{padding:1rem}.query-panel__header,.query-panel__title{align-items:flex-start;display:flex;gap:.7rem}.step-number{align-items:center;background:var(--color-accent-soft);border:1px solid color-mix(in srgb,var(--color-accent) 22%,transparent);border-radius:.55rem;color:var(--color-accent);display:flex;font-size:.7rem;font-weight:800;height:1.85rem;justify-content:center;width:1.85rem}.eyebrow{color:var(--color-accent);font-size:.59rem;font-weight:760;letter-spacing:.1em;text-transform:uppercase}.query-panel h2,.engine-panel h2,.results-panel h2{font-size:.9rem;margin:.15rem 0 0}.query-panel p,.results-head p{color:var(--color-text-muted);font-size:.67rem;margin:.2rem 0 0}.option-row{align-items:end;display:grid;gap:.75rem;grid-template-columns:minmax(16rem,28rem) minmax(12rem,1fr);margin-top:.9rem}.query-tip{align-items:center;background:var(--color-surface-sunken);border:1px solid var(--color-border);border-radius:var(--radius-md);color:var(--color-text-muted);display:flex;font-size:.65rem;gap:.5rem;min-height:var(--control-height);padding:.5rem .65rem}.query-tip code{color:var(--color-text);font-size:.65rem}.engine-panel__header{align-items:center;border-bottom:1px solid var(--color-border);display:flex;justify-content:space-between;padding:1rem}.selection-count{background:var(--color-accent-soft);border-radius:999px;color:var(--color-accent);font-size:.64rem;padding:.35rem .55rem}.engine-toolbar{align-items:center;background:var(--color-surface-sunken);border-bottom:1px solid var(--color-border);display:flex;gap:.6rem;padding:.55rem .7rem}.filter-tabs{background:var(--color-surface-raised);border:1px solid var(--color-border);border-radius:.5rem;display:flex;padding:.15rem}.filter-tabs button,.select-visible,.results-toolbar>button{background:transparent;border:0;border-radius:.35rem;color:var(--color-text-muted);cursor:pointer;font-size:.62rem;font-weight:650;padding:.35rem .5rem;text-transform:capitalize}.filter-tabs button.active,.results-toolbar>button.active{background:var(--color-accent-soft);color:var(--color-accent)}.engine-search,.results-toolbar label{align-items:center;background:var(--color-surface-raised);border:1px solid var(--color-border);border-radius:.45rem;color:var(--color-text-muted);display:flex;gap:.4rem;margin-left:auto;padding:.35rem .5rem}.engine-search input,.results-toolbar input{background:transparent;border:0;color:var(--color-text);font-size:.66rem;outline:0;width:10rem}.select-visible{color:var(--color-accent)}.engine-list{display:grid;max-height:22rem;overflow:auto}.engine-list--empty,.engine-loading{color:var(--color-text-muted);font-size:.7rem;min-height:8rem;padding:2rem;text-align:center}.engine-row{align-items:center;cursor:pointer;display:grid;gap:.65rem;grid-template-columns:auto auto minmax(0,1fr) auto auto;padding:.55rem .75rem;transition:background var(--transition-fast)}.engine-row+.engine-row{border-top:1px solid var(--color-border)}.engine-row:hover,.engine-row--selected{background:color-mix(in srgb,var(--color-accent) 5%,var(--color-surface-raised))}.engine-row input{accent-color:var(--color-accent)}.engine-mark{align-items:center;background:var(--color-surface-sunken);border:1px solid var(--color-border);border-radius:.45rem;color:var(--color-text-muted);display:flex;font-size:.7rem;font-weight:780;height:2rem;justify-content:center;width:2rem}.engine-row--selected .engine-mark{background:var(--color-accent-soft);color:var(--color-accent)}.engine-copy{display:grid}.engine-copy strong{font-size:.72rem}.engine-copy small{color:var(--color-text-muted);font-size:.58rem;margin-top:.1rem}.version-pill,.cache-state{border-radius:999px;font-size:.58rem;font-weight:650;padding:.27rem .45rem}.version-pill{background:var(--color-surface-sunken);color:var(--color-text-muted)}.cache-state{align-items:center;background:color-mix(in srgb,var(--color-success) 9%,transparent);color:var(--color-success);display:flex;gap:.25rem}.cache-state--build{background:color-mix(in srgb,var(--color-warning) 9%,transparent);color:var(--color-warning)}.selection-strip{border-top:1px solid var(--color-border);display:flex;flex-wrap:wrap;gap:.35rem;padding:.55rem .75rem}.selection-strip>span{align-items:center;background:var(--color-surface-sunken);border-radius:.4rem;color:var(--color-text-muted);display:flex;font-size:.57rem;gap:.25rem;padding:.25rem .35rem}.selection-strip b{color:var(--color-text)}.selection-strip button{background:transparent;border:0;color:var(--color-text-muted);cursor:pointer;font-size:.8rem;line-height:1}.selection-strip .selection-strip__more{background:var(--color-accent-soft);color:var(--color-accent)}.run-bar{align-items:center;background:linear-gradient(90deg,var(--color-surface-sunken),var(--color-surface-raised));border-top:1px solid var(--color-border);display:flex;gap:1rem;justify-content:space-between;padding:.75rem}.worker-readiness{align-items:center;color:var(--color-text-muted);display:flex;font-size:.63rem;gap:.4rem}.worker-readiness>span:first-child{background:var(--color-text-faint);border-radius:50%;height:.42rem;width:.42rem}.worker-readiness>span.online{background:var(--color-success);box-shadow:0 0 0 3px color-mix(in srgb,var(--color-success) 12%,transparent)}.worker-readiness strong{color:var(--color-text)}.results-head{align-items:flex-start;border-bottom:1px solid var(--color-border);display:flex;justify-content:space-between;padding:1rem}.run-status{align-items:center;background:var(--color-accent-soft);border-radius:999px;color:var(--color-accent);display:flex;font-size:.62rem;font-weight:700;gap:.35rem;padding:.35rem .55rem}.run-status>span{animation:status-pulse 1.2s infinite;background:currentColor;border-radius:50%;height:.35rem;width:.35rem}.run-status--completed{background:color-mix(in srgb,var(--color-success) 10%,transparent);color:var(--color-success)}.run-status--completed>span,.run-status--failed>span,.run-status--cancelled>span{animation:none}.run-status--failed,.run-status--cancelled{background:color-mix(in srgb,var(--color-danger) 10%,transparent);color:var(--color-danger)}.progress-block{align-items:center;border-bottom:1px solid var(--color-border);display:grid;gap:.45rem;grid-template-columns:minmax(0,1fr) auto;padding:.7rem 1rem}.progress-copy{color:var(--color-text-muted);display:flex;font-size:.62rem;grid-column:1/-1;justify-content:space-between}.progress-copy strong{color:var(--color-accent)}.progress-track{background:var(--color-surface-sunken);border-radius:999px;height:.35rem;overflow:hidden}.progress-track span{background:linear-gradient(90deg,var(--color-accent),color-mix(in srgb,var(--color-accent) 55%,var(--color-success)));border-radius:inherit;display:block;height:100%;transition:width .35s ease}.result-summary{border-bottom:1px solid var(--color-border);display:grid;grid-template-columns:repeat(3,1fr);padding:.65rem}.result-summary button{align-items:center;background:transparent;border:1px solid transparent;border-radius:.55rem;color:inherit;cursor:pointer;display:flex;gap:.55rem;padding:.55rem;text-align:left}.result-summary button:hover,.result-summary button.active{background:var(--color-surface-hover);border-color:var(--color-border)}.summary-icon{align-items:center;border-radius:.45rem;display:flex;height:2rem;justify-content:center;width:2rem}.summary-icon--yes{background:color-mix(in srgb,var(--color-success) 10%,transparent);color:var(--color-success)}.summary-icon--no{background:var(--color-surface-sunken);color:var(--color-text-muted)}.summary-icon--error{background:color-mix(in srgb,var(--color-danger) 10%,transparent);color:var(--color-danger)}.result-summary button>span:last-child{display:grid}.result-summary strong{font-size:.85rem}.result-summary small{color:var(--color-text-muted);font-size:.58rem}.results-toolbar{align-items:center;background:var(--color-surface-sunken);border-bottom:1px solid var(--color-border);display:flex;padding:.45rem .7rem}.result-list{display:grid}.result-row{align-items:center;display:grid;gap:.7rem;grid-template-columns:auto minmax(8rem,.65fr) minmax(12rem,1.5fr) auto;padding:.65rem .85rem}.result-row+.result-row{border-top:1px solid var(--color-border)}.result-state{align-items:center;background:var(--color-surface-sunken);border-radius:50%;color:var(--color-text-muted);display:flex;height:1.75rem;justify-content:center;width:1.75rem}.result-state--supported{background:color-mix(in srgb,var(--color-success) 10%,transparent);color:var(--color-success)}.result-state--failed{background:color-mix(in srgb,var(--color-danger) 10%,transparent);color:var(--color-danger)}.result-state--running svg{animation:spin 1s linear infinite}.result-engine,.declaration{display:grid;gap:.12rem;min-width:0}.result-engine strong,.declaration strong{font-size:.68rem}.result-engine small,.declaration small,.result-duration{color:var(--color-text-muted);font-size:.57rem}.declaration code{background:var(--color-surface-sunken);border:1px solid var(--color-border);border-radius:.3rem;font-size:.58rem;overflow:hidden;padding:.25rem .35rem;text-overflow:ellipsis;white-space:nowrap}.declaration--error small{color:var(--color-danger);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.aside-card{padding:.85rem}.aside-card ol{display:grid;gap:.85rem;list-style:none;margin:.85rem 0 0;padding:0}.aside-card li{display:grid;gap:.55rem;grid-template-columns:auto 1fr}.aside-card li>span{align-items:center;background:var(--color-surface-sunken);border-radius:50%;color:var(--color-text-muted);display:flex;font-size:.58rem;font-weight:750;height:1.5rem;justify-content:center;width:1.5rem}.aside-card p{color:var(--color-text-muted);font-size:.62rem;line-height:1.45;margin:0}.aside-card p strong{color:var(--color-text);display:block;font-size:.66rem;margin-bottom:.1rem}.history{display:grid;margin:.65rem -.35rem -.35rem}.history button{align-items:center;background:transparent;border:0;border-radius:.45rem;color:inherit;cursor:pointer;display:flex;gap:.5rem;justify-content:space-between;padding:.5rem;text-align:left}.history button:hover,.history button.active{background:var(--color-surface-hover)}.history button>span:first-child{display:grid;min-width:0}.history strong{font-size:.63rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.history small,.empty-history{color:var(--color-text-muted);font-size:.55rem}.history__status{border-radius:999px;color:var(--color-text-muted);font-size:.52rem;font-weight:700;padding:.22rem .35rem}.history__status--completed{background:color-mix(in srgb,var(--color-success) 9%,transparent);color:var(--color-success)}.history__status--running{background:var(--color-accent-soft);color:var(--color-accent)}.history__status--failed{background:color-mix(in srgb,var(--color-danger) 9%,transparent);color:var(--color-danger)}.empty-history{margin:.7rem 0 0}@keyframes status-pulse{50%{opacity:.35}}@keyframes spin{to{transform:rotate(360deg)}}@media(max-width:70rem){.tool-layout{grid-template-columns:1fr}.tool-aside{grid-template-columns:repeat(2,1fr);position:static}}@media(max-width:48rem){.option-row{align-items:stretch;grid-template-columns:1fr}.engine-toolbar{align-items:stretch;flex-wrap:wrap}.engine-search{margin-left:0;order:3;width:100%}.engine-search input{width:100%}.engine-row{grid-template-columns:auto auto minmax(0,1fr) auto}.cache-state{display:none}.run-bar{align-items:stretch;flex-direction:column}.result-row{grid-template-columns:auto minmax(0,1fr) auto}.declaration{grid-column:2/-1}.tool-aside{grid-template-columns:1fr}}@media(max-width:34rem){.version-pill{display:none}.result-summary{grid-template-columns:1fr}.result-summary button{justify-content:flex-start}}
</style>
