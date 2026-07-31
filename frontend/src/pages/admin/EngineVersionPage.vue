<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
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
const dockerfileDirty = ref(false)
const error = ref('')
const benchmarks = ref<EngineBenchmarkJob[]>([])

const currentBenchmarks = computed(() => benchmarks.value.filter((benchmark) => benchmark.build_hash === version.value?.build_hash))
const currentBenchmark = computed(() => currentBenchmarks.value[0] ?? null)
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
    const response = await api.post<{ dockerfile: string; model: string }>(`/api/admin/engine-versions/${id.value}/generate-dockerfile`, {
      body: { additional_context: generationContext.value.trim() },
    })
    version.value.dockerfile = response.dockerfile
    dockerfileChanged()
    toast.success(`Dockerfile generated with ${response.model}. Review it, then save.`)
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

onMounted(load)
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
        <div class="detail-heading"><div><h2>Dockerfile</h2><p>The image must provide an executable at <code>/opt/cope/engine</code> with <code>ENTRYPOINT ["./engine"]</code>.</p></div><button class="button button--secondary" type="button" :disabled="generating" @click="generate">{{ generating ? 'Generating…' : 'Generate with AI' }}</button></div>
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
          <div class="benchmark-actions"><StreamStatus :state="streamState" label="Live benchmark updates" /><span class="benchmark-state" :class="`benchmark-state--${currentBenchmark?.status ?? 'missing'}`">{{ currentBenchmark ? humanize(currentBenchmark.status) : 'Not benchmarked' }}</span><button class="button button--secondary button--small" type="button" :disabled="rescheduling || currentBenchmark?.status === 'running' || !version.dockerfile.trim() || dockerfileDirty" @click="reschedule">{{ dockerfileDirty ? 'Save before benchmarking' : rescheduling ? 'Queueing…' : currentBenchmark ? 'Re-run benchmark' : 'Benchmark' }}</button></div>
        </div>
        <div v-if="currentBenchmark" class="benchmark-current">
          <div class="benchmark-result">
            <strong v-if="currentBenchmark.result">{{ formatNumber(currentBenchmark.result.nps) }} NPS</strong>
            <strong v-else>{{ currentBenchmark.status === 'running' ? 'Benchmark in progress' : currentBenchmark.status === 'queued' ? 'Queued for benchmarking' : 'Benchmark failed' }}</strong>
            <span v-if="currentBenchmark.result">Completed {{ formatDate(currentBenchmark.result.recorded_at) }} in {{ (currentBenchmark.result.elapsed_ms / 1000).toFixed(1) }}s</span>
            <span v-else-if="currentBenchmark.next_retry_at">Automatic retry after {{ formatDate(currentBenchmark.next_retry_at) }}</span>
            <span v-else-if="currentBenchmark.started_at">Started {{ formatDate(currentBenchmark.started_at) }}</span>
            <span v-else>Scheduled {{ formatDate(currentBenchmark.scheduled_at) }}</span>
          </div>
          <dl class="benchmark-facts"><div><dt>Benchmarker</dt><dd>{{ currentBenchmark.benchmarker?.label ?? 'Awaiting assignment' }}<small v-if="currentBenchmark.benchmarker">{{ humanize(currentBenchmark.benchmarker.status) }}</small></dd></div><div><dt>Hardware</dt><dd>{{ currentBenchmark.hardware ? `${currentBenchmark.hardware.cpu_model} · ${currentBenchmark.hardware.physical_cores} cores · ${currentBenchmark.hardware.ram_gb} GB` : 'Not reported' }}</dd></div><div><dt>Attempts</dt><dd>{{ currentBenchmark.attempt }}</dd></div></dl>
          <p v-if="currentBenchmark.error" class="benchmark-error">{{ currentBenchmark.error }}</p>
          <details v-if="currentBenchmark.output" class="benchmark-output"><summary>{{ currentBenchmark.result ? 'Bench output' : 'Build / benchmark log' }}</summary><pre>{{ currentBenchmark.output }}</pre></details>
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
</style>
