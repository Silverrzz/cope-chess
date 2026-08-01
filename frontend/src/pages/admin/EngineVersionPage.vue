<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import { useEventStream } from '@/composables/useEventStream'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import EngineOptionsEditor from '@/components/admin/EngineOptionsEditor.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import StreamStatus from '@/components/ui/StreamStatus.vue'
import { errorText, formatDate, formatNumber, humanize } from '@/components/admin/format'
import type { Engine, EngineBenchmarkJob } from '@/components/admin/types'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const { confirm } = useConfirm()
const id = computed(() => Number(route.params.versionId))
const version = ref<Engine | null>(null)
const loading = ref(true)
const saving = ref(false)
const generating = ref(false)
const generationContext = ref('')
const deleting = ref(false)
const rescheduling = ref(false)
const forgetting = ref(false)
const dockerfileDirty = ref(false)
const error = ref('')
const benchmarks = ref<EngineBenchmarkJob[]>([])
const nowMs = ref(Date.now())
const lastActivityMs = ref<number | null>(null)
const consoleRef = ref<HTMLElement | null>(null)
const followConsole = ref(true)
let clockTimer: ReturnType<typeof setInterval> | null = null
let activitySignature = ''

const progressStages = [
  { label: 'Assigned', substages: ['assignment'] },
  { label: 'Source', substages: ['cache_lock', 'cache_lookup', 'source_download'] },
  { label: 'Build', substages: ['container_build'] },
  { label: 'Verify', substages: ['artifact_extract', 'artifact_verify', 'artifact_prepare'] },
  { label: 'Benchmark', substages: ['engine_bench'] },
] as const

const progressLabels: Record<string, string> = {
  assignment: 'Job assigned',
  cache_lock: 'Waiting for build cache',
  cache_lookup: 'Checking build cache',
  source_download: 'Downloading source',
  container_build: 'Building engine image',
  artifact_extract: 'Extracting executable',
  artifact_verify: 'Verifying executable',
  artifact_prepare: 'Preparing engine artifact',
  engine_bench: 'Running engine benchmark',
}

const currentBenchmarks = computed(() => benchmarks.value.filter((benchmark) => benchmark.build_hash === version.value?.build_hash))
const currentBenchmark = computed(() => currentBenchmarks.value[0] ?? null)
const currentProgress = computed(() => {
  const output = currentBenchmark.value?.output.trim() ?? ''
  if (!output) return null
  const matches = [...output.matchAll(/^\[([^\]]+)\] ([a-z0-9_]+)\/([a-z0-9_]+) (running|completed)$/gm)]
  const match = matches.at(-1)
  if (!match) return { stage: 'working', substage: 'working', status: 'running' as const, title: 'Working', detail: output.split('\n').at(-1) ?? '', updatedAt: null }
  const detailStart = (match.index ?? 0) + match[0].length
  return {
    stage: match[2] ?? 'working',
    substage: match[3] ?? 'working',
    status: match[4] === 'completed' ? 'completed' as const : 'running' as const,
    title: progressLabels[match[3] ?? ''] ?? humanize(match[3] ?? 'working'),
    detail: output.slice(detailStart).trim(),
    updatedAt: match[1] ?? null,
  }
})
const currentStageIndex = computed(() => {
  if (currentBenchmark.value?.status === 'succeeded') return progressStages.length
  const substage = currentProgress.value?.substage
  if (!substage) return -1
  return progressStages.findIndex((stage) => stage.substages.some((item) => item === substage))
})
const progressPercent = computed(() => {
  const status = currentBenchmark.value?.status
  if (status === 'succeeded') return 100
  if (status === 'queued' || !status) return 0
  const index = Math.max(0, currentStageIndex.value)
  const withinStage = currentProgress.value?.status === 'completed' ? 1 : 0.5
  return Math.min(98, Math.max(3, Math.round(((index + withinStage) / progressStages.length) * 100)))
})
const progressHeading = computed(() => {
  const benchmark = currentBenchmark.value
  if (!benchmark) return ''
  if (benchmark.status === 'queued') return 'Waiting for a benchmarker'
  if (benchmark.status === 'succeeded') return 'Benchmark complete'
  if (benchmark.status === 'failed') return currentProgress.value?.title ?? 'Benchmark failed'
  return currentProgress.value?.title ?? 'Starting benchmark'
})
const elapsedText = computed(() => {
  const benchmark = currentBenchmark.value
  if (!benchmark) return ''
  const started = Date.parse(benchmark.started_at ?? benchmark.scheduled_at)
  const finished = benchmark.finished_at ? Date.parse(benchmark.finished_at) : nowMs.value
  if (!Number.isFinite(started) || !Number.isFinite(finished)) return ''
  return formatDuration(Math.max(0, Math.floor((finished - started) / 1000)))
})
const activityHealth = computed(() => {
  const benchmark = currentBenchmark.value
  if (!benchmark) return { tone: 'idle', label: 'Not started', detail: 'No benchmark job exists.' }
  if (benchmark.status === 'queued') return { tone: 'waiting', label: 'Waiting', detail: 'The job is queued and has not been claimed yet.' }
  if (benchmark.status === 'succeeded') return { tone: 'success', label: 'Completed', detail: `Finished after ${elapsedText.value}.` }
  if (benchmark.status === 'failed') return { tone: 'danger', label: 'Failed', detail: benchmark.error || 'The benchmark stopped without a result.' }
  if (benchmark.benchmarker && benchmark.benchmarker.status !== 'busy') return { tone: 'danger', label: 'Benchmarker unavailable', detail: `The assigned benchmarker is ${humanize(benchmark.benchmarker.status ?? 'offline')}.` }
  if (streamState.value !== 'live') return { tone: 'warning', label: 'Feed reconnecting', detail: 'The job may still be running, but live updates are not connected.' }
  const ageSeconds = lastActivityMs.value === null ? Number.POSITIVE_INFINITY : Math.floor((nowMs.value - lastActivityMs.value) / 1000)
  if (ageSeconds <= 12) return { tone: 'active', label: 'Active', detail: 'The benchmarker is reporting normally.' }
  if (ageSeconds <= 30) return { tone: 'warning', label: 'Quiet', detail: `No new worker message for ${ageSeconds}s.` }
  return { tone: 'danger', label: 'Possibly stalled', detail: `No new worker message for ${formatDuration(ageSeconds)}.` }
})
const consoleOutput = computed(() => {
  const benchmark = currentBenchmark.value
  if (!benchmark) return ''
  if (benchmark.output.trim()) return benchmark.output.trim()
  if (benchmark.status === 'queued') return '$ benchmark queued\nWaiting for an available benchmarker to claim this job...'
  if (benchmark.status === 'running') return '$ benchmark claimed\nWaiting for the first worker progress message...'
  if (benchmark.status === 'failed') return benchmark.error || 'Benchmark failed without console output.'
  return 'Benchmark completed without console output.'
})
const consoleLineCount = computed(() => consoleOutput.value ? consoleOutput.value.split('\n').length : 0)
const lastActivityText = computed(() => {
  if (lastActivityMs.value === null) return 'No messages received'
  const seconds = Math.max(0, Math.floor((nowMs.value - lastActivityMs.value) / 1000))
  return seconds < 2 ? 'Updated just now' : `Updated ${formatDuration(seconds)} ago`
})
const { state: streamState } = useEventStream<{ benchmarks: EngineBenchmarkJob[] }>(
  computed(() => `/api/admin/engine-versions/${id.value}/events`),
  {
    event: 'engine-version.snapshot',
    onMessage: (snapshot) => {
      benchmarks.value = snapshot.benchmarks
      if (version.value) {
        version.value.benchmark_current = snapshot.benchmarks.some(
          (benchmark) => benchmark.build_hash === version.value?.build_hash && benchmark.status === 'succeeded',
        )
        version.value.active = Boolean(version.value.engine_active && version.value.benchmark_current)
      }
    },
  },
)

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const response = await api.get<{ version: Engine; benchmarks: EngineBenchmarkJob[] }>(`/api/admin/engine-versions/${id.value}`)
    version.value = response.version
    benchmarks.value = response.benchmarks
    dockerfileDirty.value = false
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  if (!version.value) return
  saving.value = true
  error.value = ''
  try {
    const response = await api.put<{ message: string }>(`/api/admin/engine-versions/${id.value}`, {
      body: {
        version: version.value.version.trim(),
        dockerfile: version.value.dockerfile,
        uci_options: version.value.uci_options,
      },
    })
    toast.success(response.message)
    await load()
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    saving.value = false
  }
}

async function generate(): Promise<void> {
  if (!version.value) return
  generating.value = true
  error.value = ''
  try {
    const response = await api.post<{ dockerfile: string; model: string; reviewed: boolean; used_failure_context: boolean }>(`/api/admin/engine-versions/${id.value}/generate-dockerfile`, {
      body: { additional_context: generationContext.value.trim() },
    })
    version.value.dockerfile = response.dockerfile
    dockerfileChanged()
    toast.success(response.used_failure_context ? `Dockerfile regenerated and reviewed with ${response.model} using the previous build failure.` : `Dockerfile generated and reviewed with ${response.model}. Review it, then save.`)
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    generating.value = false
  }
}

async function reschedule(): Promise<void> {
  rescheduling.value = true
  error.value = ''
  try {
    const response = await api.post<{ message: string }>(`/api/admin/engine-versions/${id.value}/benchmarks/reschedule`)
    toast.success(response.message)
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    rescheduling.value = false
  }
}

async function forgetBenchmark(): Promise<void> {
  if (!version.value || !currentBenchmark.value) return
  if (!await confirm({ title: 'Forget benchmark?', message: `Remove the benchmark and hardware assignment for ${version.value.name} ${version.value.version}? You can then benchmark it on a currently connected machine.`, confirmLabel: 'Forget benchmark', tone: 'danger' })) return
  forgetting.value = true
  error.value = ''
  try {
    const response = await api.delete<{ message: string }>(`/api/admin/engine-versions/${id.value}/benchmarks`)
    toast.success(response.message)
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    forgetting.value = false
  }
}

function dockerfileChanged(): void {
  if (!version.value) return
  dockerfileDirty.value = true
  version.value.active = false
  version.value.benchmark_current = false
}

async function remove(): Promise<void> {
  if (!version.value) return
  if (!await confirm({ title: 'Delete engine version?', message: `Delete ${version.value.name} ${version.value.version}?`, confirmLabel: 'Delete version', tone: 'danger' })) return
  deleting.value = true
  try {
    const response = await api.delete<{ message: string }>(`/api/admin/engine-versions/${id.value}`)
    toast.success(response.message)
    await router.push(`/admin/engines/${version.value.engine_id}`)
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    deleting.value = false
  }
}

function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remaining = seconds % 60
  if (hours) return `${hours}h ${minutes.toString().padStart(2, '0')}m ${remaining.toString().padStart(2, '0')}s`
  if (minutes) return `${minutes}m ${remaining.toString().padStart(2, '0')}s`
  return `${remaining}s`
}

function progressStageState(index: number): 'complete' | 'current' | 'failed' | 'pending' {
  const benchmark = currentBenchmark.value
  if (!benchmark || benchmark.status === 'queued') return 'pending'
  if (benchmark.status === 'succeeded' || index < currentStageIndex.value) return 'complete'
  if (index === currentStageIndex.value) {
    if (benchmark.status === 'failed') return 'failed'
    return currentProgress.value?.status === 'completed' ? 'complete' : 'current'
  }
  return 'pending'
}

function handleConsoleScroll(): void {
  const element = consoleRef.value
  if (!element) return
  followConsole.value = element.scrollHeight - element.scrollTop - element.clientHeight < 36
}

watch(
  () => {
    const benchmark = currentBenchmark.value
    if (!benchmark) return ''
    return `${benchmark.id}|${benchmark.status}|${benchmark.output.length}|${benchmark.output.slice(-240)}|${benchmark.error}`
  },
  (signature) => {
    if (signature && signature !== activitySignature) {
      const benchmark = currentBenchmark.value
      const recordedAt = activitySignature
        ? Number.NaN
        : Date.parse(currentProgress.value?.updatedAt ?? benchmark?.finished_at ?? benchmark?.started_at ?? benchmark?.scheduled_at ?? '')
      lastActivityMs.value = Number.isFinite(recordedAt) ? recordedAt : Date.now()
    }
    activitySignature = signature
  },
  { immediate: true },
)

watch(consoleOutput, async () => {
  if (!followConsole.value) return
  await nextTick()
  const element = consoleRef.value
  if (element) element.scrollTop = element.scrollHeight
})

watch(followConsole, async (enabled) => {
  if (!enabled) return
  await nextTick()
  const element = consoleRef.value
  if (element) element.scrollTop = element.scrollHeight
})

onMounted(() => {
  clockTimer = setInterval(() => { nowMs.value = Date.now() }, 1000)
  void load()
})

onBeforeUnmount(() => {
  if (clockTimer !== null) clearInterval(clockTimer)
})
</script>

<template>
  <div class="admin-page page-stack">
    <AdminPageHeader :title="version ? `${version.name} ${version.version}` : 'Engine version'">
      <template #actions><RouterLink v-if="version" class="button button--ghost" :to="`/admin/engines/${version.engine_id}`">Back to engine</RouterLink></template>
    </AdminPageHeader>
    <InlineFeedback :message="error" />
    <div v-if="loading" class="panel loading-card">Loading version…</div>
    <form v-else-if="version" class="page-stack" @submit.prevent="save">
      <section class="panel detail-card">
        <div class="detail-heading"><div><h2>Source</h2><p>COPE checks out this exact source before building on each worker.</p></div><div class="availability" :class="{ 'availability--active': version.active }"><strong>{{ version.active ? 'Active' : 'Inactive' }}</strong><small>{{ version.benchmark_current ? (version.engine_active ? 'Current build benchmarked' : 'Engine disabled') : 'Current build needs a benchmark' }}</small></div></div>
        <div class="form-grid">
          <label class="field"><span>Version label</span><input v-model="version.version" class="input" required maxlength="80"></label>
          <div class="field"><span>Repository</span><a class="readonly-value" :href="version.repository_url.replace(/\.git$/, '')" target="_blank" rel="noopener">{{ version.repository_full_name }}</a></div>
          <div class="field"><span>{{ version.source_kind === 'release' ? 'Release' : 'Commit' }}</span><code class="readonly-value">{{ version.source_ref }}</code></div>
          <div class="field"><span>Build cache key</span><code class="readonly-value" :title="version.build_hash">{{ version.build_hash }}</code></div>
          <div class="field"><span>Created</span><span class="readonly-value">{{ formatDate(version.created_at) }}</span></div>
        </div>
      </section>

      <section class="panel detail-card">
        <div class="detail-heading"><div><h2>Dockerfile</h2><p>The image must provide an executable at <code>/opt/cope/engine</code> with <code>ENTRYPOINT ["./engine"]</code>.</p></div><button class="button button--secondary" type="button" :disabled="generating" @click="generate">{{ generating ? 'Generating and reviewing…' : currentBenchmark?.status === 'failed' ? 'Repair with AI' : 'Generate with AI' }}</button></div>
        <label class="field generation-context"><span>Additional context for AI generation</span><textarea v-model="generationContext" class="input" rows="3" maxlength="4000" placeholder="Optional build requirements, target features, or repository-specific notes" /></label>
        <textarea v-model="version.dockerfile" class="input dockerfile-editor" spellcheck="false" aria-label="Dockerfile" placeholder="Add a Dockerfile before benchmarking" @input="dockerfileChanged" />
      </section>

      <section class="panel detail-card">
        <div class="detail-heading"><div><h2>Default UCI options</h2><p>Applied whenever this version starts unless a tournament overrides them.</p></div></div>
        <EngineOptionsEditor v-model="version.uci_options" />
      </section>

      <section class="panel benchmark-card" aria-labelledby="benchmarks-title">
        <div class="detail-heading">
          <div><h2 id="benchmarks-title">Benchmarking</h2><p>Build and <code>bench</code> results stream live from the benchmark service.</p></div>
          <div class="benchmark-actions"><StreamStatus :state="streamState" label="Live benchmark updates" /><span class="benchmark-state" :class="`benchmark-state--${currentBenchmark?.status ?? 'missing'}`">{{ currentBenchmark ? humanize(currentBenchmark.status) : 'Not benchmarked' }}</span><button v-if="currentBenchmark" class="button button--danger button--small" type="button" :disabled="forgetting || rescheduling || currentBenchmark.status === 'running'" @click="forgetBenchmark">{{ forgetting ? 'Forgetting…' : 'Forget' }}</button><button class="button button--secondary button--small" type="button" :disabled="rescheduling || forgetting || currentBenchmark?.status === 'running' || !version.dockerfile.trim() || dockerfileDirty" @click="reschedule">{{ dockerfileDirty ? 'Save before benchmarking' : rescheduling ? 'Queueing…' : currentBenchmark ? 'Re-run benchmark' : 'Benchmark' }}</button></div>
        </div>
        <div v-if="currentBenchmark" class="benchmark-current">
          <div class="benchmark-summary">
            <div class="benchmark-result">
              <strong v-if="currentBenchmark.result">{{ formatNumber(currentBenchmark.result.nps) }} NPS</strong>
              <strong v-else>{{ currentBenchmark.status === 'running' ? 'Benchmark in progress' : currentBenchmark.status === 'queued' ? 'Queued for benchmarking' : 'Benchmark failed' }}</strong>
              <span v-if="currentBenchmark.result">Completed {{ formatDate(currentBenchmark.result.recorded_at) }} in {{ (currentBenchmark.result.elapsed_ms / 1000).toFixed(1) }}s</span>
              <span v-else-if="currentBenchmark.next_retry_at">Automatic retry after {{ formatDate(currentBenchmark.next_retry_at) }}</span>
              <span v-else-if="currentBenchmark.started_at">Started {{ formatDate(currentBenchmark.started_at) }} · {{ elapsedText }} elapsed</span>
              <span v-else>Scheduled {{ formatDate(currentBenchmark.scheduled_at) }} · {{ elapsedText }} elapsed</span>
            </div>
            <div class="benchmark-health" :class="`benchmark-health--${activityHealth.tone}`" role="status" aria-live="polite">
              <span class="benchmark-health__dot" aria-hidden="true" />
              <span><strong>{{ activityHealth.label }}</strong><small>{{ activityHealth.detail }}</small></span>
            </div>
          </div>

          <section class="benchmark-progress-panel" aria-labelledby="benchmark-progress-title">
            <div class="benchmark-progress-heading">
              <span><strong id="benchmark-progress-title">{{ progressHeading }}</strong><small v-if="currentProgress?.detail">{{ currentProgress.detail }}</small></span>
              <span>{{ progressPercent }}%</span>
            </div>
            <div class="benchmark-progress-track" role="progressbar" :aria-valuenow="progressPercent" aria-valuemin="0" aria-valuemax="100" :aria-valuetext="progressHeading">
              <span class="benchmark-progress-fill" :class="{ 'benchmark-progress-fill--active': currentBenchmark.status === 'running' }" :style="{ width: `${progressPercent}%` }" />
            </div>
            <ol class="benchmark-stage-list">
              <li v-for="(stage, index) in progressStages" :key="stage.label" :class="`benchmark-stage benchmark-stage--${progressStageState(index)}`">
                <span aria-hidden="true">{{ progressStageState(index) === 'complete' ? '✓' : progressStageState(index) === 'failed' ? '!' : index + 1 }}</span>
                <small>{{ stage.label }}</small>
              </li>
            </ol>
          </section>

          <dl class="benchmark-facts"><div><dt>Benchmarker</dt><dd>{{ currentBenchmark.benchmarker?.label ?? 'Awaiting assignment' }}<small v-if="currentBenchmark.benchmarker">{{ humanize(currentBenchmark.benchmarker.status) }}</small></dd></div><div><dt>Hardware</dt><dd>{{ currentBenchmark.hardware ? `${currentBenchmark.hardware.cpu_model} · ${currentBenchmark.hardware.physical_cores} cores · ${currentBenchmark.hardware.ram_gb} GB` : 'Not reported' }}</dd></div><div><dt>Elapsed</dt><dd>{{ elapsedText }}<small>{{ lastActivityText }}</small></dd></div><div><dt>Attempts</dt><dd>{{ currentBenchmark.attempt }}</dd></div></dl>
          <p v-if="currentBenchmark.error" class="benchmark-error">{{ currentBenchmark.error }}</p>
          <section class="benchmark-console" aria-labelledby="benchmark-console-title">
            <header>
              <span class="benchmark-console__title"><span class="benchmark-console__light" :class="`benchmark-console__light--${activityHealth.tone}`" aria-hidden="true" /><strong id="benchmark-console-title">{{ currentBenchmark.status === 'running' ? 'Live benchmark console' : 'Benchmark console' }}</strong></span>
              <span class="benchmark-console__meta">{{ consoleLineCount }} lines · {{ lastActivityText }}</span>
              <label><input v-model="followConsole" type="checkbox"> Follow output</label>
            </header>
            <pre ref="consoleRef" role="log" aria-live="polite" aria-relevant="additions text" tabindex="0" @scroll.passive="handleConsoleScroll">{{ consoleOutput }}</pre>
          </section>
        </div>
        <p v-else class="benchmark-empty">No job exists for this build. Save a Dockerfile, then request a benchmark when you are ready.</p>
        <details v-if="benchmarks.length > currentBenchmarks.length" class="benchmark-history"><summary>Previous build history ({{ benchmarks.length - currentBenchmarks.length }})</summary><div v-for="benchmark in benchmarks.filter((item) => item.build_hash !== version?.build_hash)" :key="benchmark.id" class="history-row"><span :class="`benchmark-state benchmark-state--${benchmark.status}`">{{ humanize(benchmark.status) }}</span><span>{{ formatDate(benchmark.finished_at ?? benchmark.scheduled_at) }}</span><strong v-if="benchmark.result">{{ formatNumber(benchmark.result.nps) }} NPS</strong><span v-else>{{ benchmark.error || 'No result' }}</span><details v-if="benchmark.output"><summary>Log</summary><pre>{{ benchmark.output }}</pre></details></div></details>
      </section>

      <div class="form-actions">
        <button class="button button--danger" type="button" :disabled="deleting || saving" @click="remove">{{ deleting ? 'Deleting…' : 'Delete version' }}</button>
        <button class="button button--primary" type="submit" :disabled="saving || deleting">{{ saving ? 'Saving…' : 'Save version' }}</button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.loading-card,.detail-card,.benchmark-card{padding:1rem}.detail-card,.benchmark-card{display:grid;gap:1rem}.detail-heading{align-items:start;border-bottom:1px solid var(--color-border);display:flex;gap:1rem;justify-content:space-between;padding-bottom:.8rem}.detail-heading h2{font-size:.95rem;margin:0}.detail-heading p{color:var(--color-text-muted);font-size:.7rem;margin:.2rem 0 0}.availability{display:grid;gap:.12rem;text-align:right}.availability strong{color:var(--color-text-muted);font-size:.76rem}.availability small{color:var(--color-text-muted);font-size:.67rem}.availability--active strong{color:var(--color-success,#166534)}.form-grid{display:grid;gap:.85rem;grid-template-columns:repeat(2,minmax(0,1fr))}.field{display:grid;gap:.38rem;min-width:0}.field>span:first-child{font-size:.76rem;font-weight:650}.readonly-value{color:var(--color-text);font-size:.73rem;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.dockerfile-editor{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.72rem;line-height:1.55;min-height:27rem;resize:vertical;tab-size:2;white-space:pre}.form-actions,.benchmark-actions{align-items:center;display:flex;gap:.6rem;justify-content:flex-end}.benchmark-state{border-radius:999px;font-size:.67rem;font-weight:700;padding:.32rem .55rem;text-transform:capitalize;white-space:nowrap}.benchmark-state--succeeded{background:#dcfce7;color:#166534}.benchmark-state--queued{background:#dbeafe;color:#1d4ed8}.benchmark-state--running{background:#fef3c7;color:#92400e}.benchmark-state--failed{background:#fee2e2;color:#b91c1c}.benchmark-state--missing{background:var(--color-surface-subtle);color:var(--color-text-muted)}.benchmark-current{display:grid;gap:.85rem}.benchmark-result{display:grid;gap:.2rem}.benchmark-result strong{font-size:1.1rem}.benchmark-result span,.benchmark-empty{color:var(--color-text-muted);font-size:.73rem}.benchmark-facts{display:grid;margin:0}.benchmark-facts>div{border-top:1px solid var(--color-border);display:grid;gap:.75rem;grid-template-columns:7rem 1fr;padding:.55rem 0}.benchmark-facts dt{color:var(--color-text-muted);font-size:.68rem}.benchmark-facts dd{font-size:.73rem;margin:0}.benchmark-facts small{color:var(--color-text-muted);display:block;margin-top:.15rem}.benchmark-error{background:#fef2f2;border-left:3px solid var(--color-danger);font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.7rem;line-height:1.45;margin:0;padding:.65rem;white-space:pre-wrap}.benchmark-output,.benchmark-history{border-top:1px solid var(--color-border);font-size:.73rem;padding-top:.75rem}.benchmark-output summary,.benchmark-history summary{cursor:pointer;font-weight:650}.benchmark-output pre,.history-row pre{background:#0f172a;color:#e2e8f0;font-size:.67rem;line-height:1.45;margin:.65rem 0 0;max-height:22rem;overflow:auto;padding:.75rem;white-space:pre-wrap}.history-row{align-items:start;border-top:1px solid var(--color-border);display:grid;gap:.6rem;grid-template-columns:auto 10rem auto minmax(0,1fr);padding:.65rem 0}.history-row details{grid-column:1/-1}@media(max-width:42rem){.detail-heading{align-items:stretch;flex-direction:column}.availability{text-align:left}.form-grid{grid-template-columns:1fr}.dockerfile-editor{min-height:22rem}.history-row{grid-template-columns:1fr}.benchmark-facts>div{grid-template-columns:1fr}}
.benchmark-progress{align-items:flex-start;background:color-mix(in srgb,var(--color-accent) 7%,transparent);border:1px solid color-mix(in srgb,var(--color-accent) 22%,transparent);border-radius:var(--radius-md,.6rem);display:flex;gap:.65rem;padding:.65rem .75rem}.benchmark-progress>span:last-child{display:grid;gap:.2rem;min-width:0}.benchmark-progress strong{font-size:.74rem}.benchmark-progress small{color:var(--color-text-muted);font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.67rem;line-height:1.4;max-height:5.6rem;overflow:auto;white-space:pre-wrap}.benchmark-progress__pulse{animation:benchmark-pulse 1.2s ease-in-out infinite;background:var(--color-accent);border-radius:50%;height:.55rem;margin-top:.18rem;width:.55rem}@keyframes benchmark-pulse{50%{opacity:.35;transform:scale(.78)}}
.benchmark-summary{align-items:start;display:flex;gap:1rem;justify-content:space-between}.benchmark-health{align-items:flex-start;background:var(--color-surface-subtle);border:1px solid var(--color-border);border-radius:var(--radius-md);display:flex;gap:.55rem;max-width:28rem;padding:.55rem .7rem}.benchmark-health>span:last-child{display:grid;gap:.12rem}.benchmark-health strong{font-size:.72rem}.benchmark-health small{color:var(--color-text-muted);font-size:.66rem;line-height:1.35}.benchmark-health__dot{background:var(--color-text-faint);border-radius:50%;box-shadow:0 0 0 .2rem color-mix(in srgb,var(--color-text-faint) 15%,transparent);flex:0 0 auto;height:.5rem;margin-top:.18rem;width:.5rem}.benchmark-health--active .benchmark-health__dot{animation:benchmark-live-pulse 1.5s ease-in-out infinite;background:var(--color-success);box-shadow:0 0 0 .2rem color-mix(in srgb,var(--color-success) 15%,transparent)}.benchmark-health--success .benchmark-health__dot{background:var(--color-success);box-shadow:0 0 0 .2rem color-mix(in srgb,var(--color-success) 15%,transparent)}.benchmark-health--waiting .benchmark-health__dot,.benchmark-health--warning .benchmark-health__dot{background:var(--color-warning);box-shadow:0 0 0 .2rem color-mix(in srgb,var(--color-warning) 15%,transparent)}.benchmark-health--danger .benchmark-health__dot{background:var(--color-danger);box-shadow:0 0 0 .2rem color-mix(in srgb,var(--color-danger) 15%,transparent)}
.benchmark-progress-panel{background:color-mix(in srgb,var(--color-accent) 5%,var(--color-surface));border:1px solid color-mix(in srgb,var(--color-accent) 20%,var(--color-border));border-radius:var(--radius-md);display:grid;gap:.65rem;padding:.8rem}.benchmark-progress-heading{align-items:start;display:flex;gap:1rem;justify-content:space-between}.benchmark-progress-heading>span:first-child{display:grid;gap:.18rem;min-width:0}.benchmark-progress-heading strong{font-size:.78rem}.benchmark-progress-heading small{color:var(--color-text-muted);display:-webkit-box;font-family:var(--font-mono);font-size:.65rem;line-height:1.4;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:3;white-space:pre-wrap}.benchmark-progress-heading>span:last-child{color:var(--color-accent);font-family:var(--font-mono);font-size:.72rem;font-weight:700}.benchmark-progress-track{background:color-mix(in srgb,var(--color-border) 72%,transparent);border-radius:999px;height:.58rem;overflow:hidden}.benchmark-progress-fill{background:var(--color-success);border-radius:inherit;display:block;height:100%;min-width:0;transition:width .35s ease}.benchmark-progress-fill--active{animation:benchmark-stripes 1s linear infinite;background-color:var(--color-accent);background-image:linear-gradient(135deg,transparent 25%,rgb(255 255 255 / 22%) 25%,rgb(255 255 255 / 22%) 50%,transparent 50%,transparent 75%,rgb(255 255 255 / 22%) 75%);background-size:1.2rem 1.2rem}.benchmark-stage-list{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));list-style:none;margin:.05rem 0 0;padding:0}.benchmark-stage{align-items:center;color:var(--color-text-faint);display:grid;gap:.3rem;justify-items:center;position:relative;text-align:center}.benchmark-stage::before{background:var(--color-border);content:"";height:1px;left:0;position:absolute;right:0;top:.65rem}.benchmark-stage:first-child::before{left:50%}.benchmark-stage:last-child::before{right:50%}.benchmark-stage>span{align-items:center;background:var(--color-surface);border:1px solid var(--color-border-strong);border-radius:50%;display:flex;font-size:.6rem;font-weight:750;height:1.3rem;justify-content:center;position:relative;width:1.3rem;z-index:1}.benchmark-stage small{font-size:.62rem}.benchmark-stage--complete{color:var(--color-success)}.benchmark-stage--complete::before{background:color-mix(in srgb,var(--color-success) 58%,var(--color-border))}.benchmark-stage--complete>span{background:var(--color-success);border-color:var(--color-success);color:var(--color-surface)}.benchmark-stage--current{color:var(--color-accent);font-weight:700}.benchmark-stage--current>span{background:var(--color-accent);border-color:var(--color-accent);box-shadow:0 0 0 .2rem color-mix(in srgb,var(--color-accent) 15%,transparent);color:var(--color-on-accent)}.benchmark-stage--failed{color:var(--color-danger);font-weight:700}.benchmark-stage--failed>span{background:var(--color-danger);border-color:var(--color-danger);color:var(--color-on-danger)}
.benchmark-console{background:#0b111b;border:1px solid #263346;border-radius:var(--radius-md);box-shadow:inset 0 1px 0 rgb(255 255 255 / 4%);min-width:0;overflow:hidden}.benchmark-console header{align-items:center;background:#121b28;border-bottom:1px solid #263346;color:#aebbd0;display:flex;font-size:.65rem;gap:.8rem;min-height:2.35rem;padding:.45rem .7rem}.benchmark-console__title{align-items:center;color:#edf4ff;display:flex;gap:.45rem}.benchmark-console__light{background:#718096;border-radius:50%;height:.5rem;width:.5rem}.benchmark-console__light--active,.benchmark-console__light--success{background:#45d483;box-shadow:0 0 .45rem rgb(69 212 131 / 65%)}.benchmark-console__light--waiting,.benchmark-console__light--warning{background:#f4c95d}.benchmark-console__light--danger{background:#ff6b78}.benchmark-console__meta{margin-left:auto}.benchmark-console label{align-items:center;cursor:pointer;display:flex;gap:.35rem;white-space:nowrap}.benchmark-console input{accent-color:#78a7ff}.benchmark-console pre{background:#0b111b;color:#d9e5f5;font-family:var(--font-mono);font-size:.68rem;line-height:1.55;margin:0;max-height:27rem;min-height:12rem;overflow:auto;padding:.85rem;scrollbar-color:#42536b #0b111b;tab-size:2;white-space:pre-wrap;word-break:break-word}.benchmark-console pre:focus{outline:2px solid var(--color-focus);outline-offset:-2px}
@keyframes benchmark-live-pulse{50%{opacity:.45;transform:scale(.8)}}@keyframes benchmark-stripes{to{background-position:1.2rem 0}}@media(max-width:42rem){.benchmark-summary{align-items:stretch;flex-direction:column}.benchmark-health{max-width:none}.benchmark-actions{align-items:flex-end;flex-wrap:wrap}.benchmark-stage small{font-size:.56rem}.benchmark-console header{align-items:flex-start;flex-wrap:wrap}.benchmark-console__meta{margin-left:0;order:3;width:100%}.benchmark-console label{margin-left:auto}}@media(prefers-reduced-motion:reduce){.benchmark-health--active .benchmark-health__dot,.benchmark-progress-fill--active{animation:none}.benchmark-progress-fill{transition:none}}
</style>
