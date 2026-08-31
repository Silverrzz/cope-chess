<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '@/api/client'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import { errorText, formatDate, formatNumber } from '@/components/admin/format'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { useToast } from '@/composables/useToast'

interface Engine { id: number; family_id: number; name: string; author: string; version: string; distribution: string; artifact_ready: boolean; active: boolean }
interface RatingList { id: number; name: string; ratings: Array<{ engine_id: number; elo: number }> }
interface Worker { id: number; label: string; status: string; threads: number | null; hash_mb: number | null }
interface ToolItem { id: number; engine_id: number; engine_name: string; engine_version: string; status: string; error: string }
interface ToolJob { id: number; tool_name: string; status: string; worker: { id: number; label: string } | null; total_items: number; completed_items: number; required_threads: number; required_hash_mb: number; progress_current: number; progress_total: number; progress_detail: string; attempt: number; error: string; created_at: string; started_at: string | null; finished_at: string | null; items: ToolItem[] }
interface SuiteSummary { id: number; name: string; puzzle_count: number; unique_count: number; included_count: number; rated_count: number; active_job: ToolJob | null; created_at: string; updated_at: string }
interface Puzzle { id: number; position: number; title: string; fen: string; solutions: string[]; included: boolean; uniqueness_status: string; verified_solution: string; best_move: string; second_move: string; best_sigmoid: number | null; second_sigmoid: number | null; sigmoid_gap: number | null; uniqueness_depth: number | null; uniqueness_nodes: number | null; uniqueness_time_ms: number | null; uniqueness_error: string; difficulty_elo: number | null }
interface Run { id: number; stage: 'uniqueness' | 'difficulty'; rating_list_id: number | null; settings: Record<string, unknown>; created_at: string; job: ToolJob }
interface EngineResult { id: number; run_id: number; puzzle_id: number; engine_id: number; engine_name: string; engine_version: string; engine_elo: number; estimate_elo: number | null; status: string; best_move: string; solution_nodes: number | null; final_nodes: number | null; depth: number | null; time_ms: number; error: string }
interface SuiteDetail extends SuiteSummary { puzzles: Puzzle[]; runs: Run[]; engine_results: EngineResult[] }
interface Context { engines: Engine[]; rating_lists: RatingList[]; suites: SuiteSummary[]; workers: Worker[] }

const route = useRoute()
const router = useRouter()
const toast = useToast()
const context = ref<Context | null>(null)
const suite = ref<SuiteDetail | null>(null)
const loading = ref(true)
const suiteLoading = ref(false)
const pending = ref('')
const error = ref('')
const importForm = reactive({ name: '', puzzles: '' })
const uniqueness = reactive({ engine_id: 0, seconds: 30, multipv: 2, threads: 1, hash_mb: 256, min_gap: 0.15 })
const difficulty = reactive({ rating_list_id: 0, engine_ids: [] as number[], seconds: 30, threads: 1, hash_mb: 256 })
const puzzleFilter = ref<'all' | 'included' | 'unique' | 'rejected'>('all')
const puzzleSearch = ref('')
let pollTimer: number | undefined

const selectedSuiteId = computed(() => {
  const value = Number(route.query.suite)
  return Number.isInteger(value) && value > 0 ? value : 0
})
const activeRun = computed(() => suite.value?.runs.find((run) => ['queued', 'running'].includes(run.job.status)) ?? null)
const latestUniqueness = computed(() => suite.value?.runs.find((run) => run.stage === 'uniqueness') ?? null)
const latestDifficulty = computed(() => suite.value?.runs.find((run) => run.stage === 'difficulty') ?? null)
const selectedRatingList = computed(() => context.value?.rating_lists.find((item) => item.id === difficulty.rating_list_id) ?? null)
const ratingMap = computed(() => new Map(selectedRatingList.value?.ratings.map((rating) => [rating.engine_id, rating.elo]) ?? []))
const ratedEngines = computed(() => (context.value?.engines ?? []).filter((engine) => ratingMap.value.has(engine.id)))
const activeJob = computed(() => activeRun.value?.job ?? null)
const unfilteredSuite = computed(() => Boolean(suite.value?.puzzles.length) && suite.value!.puzzles.every((puzzle) => puzzle.uniqueness_status === 'pending'))
const ratingCandidateCount = computed(() => {
  if (!suite.value) return 0
  if (unfilteredSuite.value) return suite.value.puzzles.filter((puzzle) => puzzle.solutions.length === 1).length
  return suite.value.puzzles.filter((puzzle) => puzzle.included && puzzle.uniqueness_status === 'unique' && puzzle.verified_solution).length
})
const progressPercent = computed(() => {
  const job = activeJob.value
  if (!job?.progress_total) return job?.status === 'completed' ? 100 : 0
  return Math.min(100, Math.round(job.progress_current * 100 / job.progress_total))
})
const importLineCount = computed(() => importForm.puzzles.split(/\r?\n/).filter((line) => line.trim()).length)
const resultMap = computed(() => {
  const map = new Map<number, EngineResult[]>()
  for (const result of suite.value?.engine_results ?? []) {
    const values = map.get(result.puzzle_id) ?? []
    values.push(result)
    map.set(result.puzzle_id, values)
  }
  return map
})
const displayedPuzzles = computed(() => {
  const query = puzzleSearch.value.trim().toLowerCase()
  const values = (suite.value?.puzzles ?? []).filter((puzzle) => {
    if (puzzleFilter.value === 'included' && !puzzle.included) return false
    if (puzzleFilter.value === 'unique' && puzzle.uniqueness_status !== 'unique') return false
    if (puzzleFilter.value === 'rejected' && !['ambiguous', 'failed'].includes(puzzle.uniqueness_status)) return false
    return !query || puzzle.fen.toLowerCase().includes(query) || puzzle.solutions.some((move) => move.includes(query)) || puzzle.verified_solution.includes(query)
  })
  if (suite.value?.rated_count) {
    values.sort((left, right) => {
      if (left.difficulty_elo == null) return 1
      if (right.difficulty_elo == null) return -1
      return left.difficulty_elo - right.difficulty_elo || left.position - right.position
    })
  }
  return values
})
const visiblePuzzles = computed(() => displayedPuzzles.value.slice(0, 500))

watch(selectedSuiteId, async (value) => {
  if (!value) {
    suite.value = null
    return
  }
  await loadSuite()
}, { immediate: true })

watch(selectedRatingList, (value) => {
  if (!value) {
    difficulty.engine_ids = []
    return
  }
  const available = new Set(value.ratings.map((rating) => rating.engine_id))
  difficulty.engine_ids = difficulty.engine_ids.filter((id) => available.has(id))
})

async function loadContext(): Promise<void> {
  try {
    context.value = await api.get<Context>('/api/admin/tools/puzzle-suite-manager')
    if (!uniqueness.engine_id) uniqueness.engine_id = context.value.engines[0]?.id ?? 0
    if (!difficulty.rating_list_id) difficulty.rating_list_id = context.value.rating_lists[0]?.id ?? 0
  } catch (cause) {
    error.value = errorText(cause)
  }
}

async function loadSuite(silent = false): Promise<void> {
  if (!selectedSuiteId.value) return
  if (!silent) suiteLoading.value = true
  try {
    const response = await api.get<{ suite: SuiteDetail }>(`/api/admin/tools/puzzle-suite-manager/suites/${selectedSuiteId.value}`)
    suite.value = response.suite
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    suiteLoading.value = false
  }
}

async function initialize(): Promise<void> {
  loading.value = true
  error.value = ''
  await loadContext()
  if (selectedSuiteId.value) await loadSuite()
  loading.value = false
}

async function createSuite(): Promise<void> {
  if (pending.value) return
  pending.value = 'import'
  error.value = ''
  try {
    const response = await api.post<{ suite: SuiteDetail; message: string }>('/api/admin/tools/puzzle-suite-manager/suites', { body: { name: importForm.name, puzzles: importForm.puzzles } })
    toast.success(response.message)
    importForm.name = ''
    importForm.puzzles = ''
    await loadContext()
    await router.replace({ query: { ...route.query, suite: response.suite.id } })
    suite.value = response.suite
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    pending.value = ''
  }
}

async function startUniqueness(): Promise<void> {
  if (!suite.value || pending.value) return
  pending.value = 'uniqueness'
  error.value = ''
  try {
    const response = await api.post<{ message: string }>(`/api/admin/tools/puzzle-suite-manager/suites/${suite.value.id}/uniqueness`, {
      body: {
        engine_id: uniqueness.engine_id,
        movetime_ms: Math.round(uniqueness.seconds * 1000),
        multipv: uniqueness.multipv,
        threads: uniqueness.threads,
        hash_mb: uniqueness.hash_mb,
        min_sigmoid_gap: uniqueness.min_gap,
      },
    })
    toast.success(response.message)
    await Promise.all([loadSuite(true), loadContext()])
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    pending.value = ''
  }
}

async function startDifficulty(): Promise<void> {
  if (!suite.value || pending.value) return
  pending.value = 'difficulty'
  error.value = ''
  try {
    const response = await api.post<{ message: string }>(`/api/admin/tools/puzzle-suite-manager/suites/${suite.value.id}/difficulty`, {
      body: {
        engine_ids: difficulty.engine_ids,
        rating_list_id: difficulty.rating_list_id,
        movetime_ms: Math.round(difficulty.seconds * 1000),
        threads: difficulty.threads,
        hash_mb: difficulty.hash_mb,
      },
    })
    toast.success(response.message)
    await Promise.all([loadSuite(true), loadContext()])
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    pending.value = ''
  }
}

async function togglePuzzle(puzzle: Puzzle): Promise<void> {
  if (!suite.value || pending.value || activeRun.value) return
  pending.value = `puzzle-${puzzle.id}`
  try {
    await api.patch(`/api/admin/tools/puzzle-suite-manager/suites/${suite.value.id}/puzzles/${puzzle.id}`, { body: { included: !puzzle.included } })
    puzzle.included = !puzzle.included
    suite.value.included_count += puzzle.included ? 1 : -1
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    pending.value = ''
  }
}

function toggleDifficultyEngine(engineId: number): void {
  const index = difficulty.engine_ids.indexOf(engineId)
  if (index >= 0) difficulty.engine_ids.splice(index, 1)
  else difficulty.engine_ids.push(engineId)
}

function eligibleWorkers(threads: number, hashMb: number): number {
  return (context.value?.workers ?? []).filter((worker) => (worker.threads ?? 0) >= threads && (worker.hash_mb ?? 0) >= hashMb).length
}

function stageLabel(stage: string): string {
  return stage === 'uniqueness' ? 'Unique-solution filter' : 'Difficulty rating'
}

function percent(value: number | null): string {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`
}

function nodes(value: number | null): string {
  return value == null ? '—' : formatNumber(value)
}

function statusText(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

async function copyOutput(ordered: boolean): Promise<void> {
  const values = (suite.value?.puzzles ?? []).filter((puzzle) => (
    puzzle.included && puzzle.verified_solution
    || unfilteredSuite.value && puzzle.solutions.length === 1
  ))
  if (ordered) values.sort((left, right) => (left.difficulty_elo ?? Number.MAX_VALUE) - (right.difficulty_elo ?? Number.MAX_VALUE) || left.position - right.position)
  else values.sort((left, right) => left.position - right.position)
  await navigator.clipboard.writeText(values.map((puzzle) => `${puzzle.fen}|${puzzle.verified_solution || puzzle.solutions[0]}`).join('\n'))
  toast.success(`Copied ${values.length} puzzles.`)
}

function openSuite(id: number): void {
  void router.replace({ query: { ...route.query, suite: id } })
}

function clearSuite(): void {
  const query = { ...route.query }
  delete query.suite
  void router.replace({ query })
}

onMounted(() => {
  void initialize()
  pollTimer = window.setInterval(async () => {
    if (!activeRun.value) return
    await Promise.all([loadSuite(true), loadContext()])
  }, 1500)
})

onBeforeUnmount(() => {
  if (pollTimer !== undefined) window.clearInterval(pollTimer)
})
</script>

<template>
  <div class="admin-page page-stack puzzle-manager">
    <AdminPageHeader title="Puzzle Suite Manager" description="Filter for a single verified solution, then rate the survivors by engine solve effort.">
      <template #actions><BaseButton variant="ghost" to="/admin/tools"><template #icon><AppIcon name="arrow-left" :size="16" /></template>All tools</BaseButton></template>
    </AdminPageHeader>
    <InlineFeedback :message="error" />

    <div class="manager-grid">
      <aside class="suite-rail">
        <section class="panel rail-card">
          <div class="rail-heading"><div><span class="eyebrow">Library</span><h2>Puzzle suites</h2></div><button v-if="suite" type="button" @click="clearSuite"><AppIcon name="plus" :size="16" />New</button></div>
          <div v-if="loading" class="rail-empty">Loading suites…</div>
          <div v-else-if="context?.suites.length" class="suite-list">
            <button v-for="item in context.suites" :key="item.id" type="button" :class="{ active: item.id === selectedSuiteId }" @click="openSuite(item.id)">
              <span><strong>{{ item.name }}</strong><small>{{ item.included_count }} kept · {{ item.rated_count }} rated</small></span>
              <span v-if="item.active_job" class="live-dot" />
              <AppIcon v-else name="chevron-right" :size="15" />
            </button>
          </div>
          <div v-else class="rail-empty">No puzzle suites yet.</div>
        </section>
        <section class="panel rail-card model-card">
          <span class="eyebrow">Difficulty model</span>
          <p>Estimate = engine Elo + 100 × log₂(nodes / 10,000). Unsolved searches add a 100 Elo lower-bound bonus; solve and unsolved estimates are averaged.</p>
        </section>
      </aside>

      <main v-if="!selectedSuiteId" class="workspace">
        <section class="panel import-card">
          <div class="card-heading"><span class="step-mark"><AppIcon name="upload" :size="19" /></span><div><span class="eyebrow">Import</span><h2>Create a puzzle suite</h2><p>Uses the Puzzle Gauntlet format and move validation.</p></div></div>
          <form @submit.prevent="createSuite">
            <label>Suite name<input v-model="importForm.name" maxlength="120" required placeholder="Tactical candidates · September" /></label>
            <label>Puzzle block<span class="format-chip">fen|solution</span><textarea v-model="importForm.puzzles" rows="18" required placeholder="r1bq1rk1/pp3ppp/2n1pn2/2bp4/8/1PN1PN2/PBPP1PPP/R2QKB1R w KQ - 2 9|Nxd5" /></label>
            <div class="form-footer"><span>{{ formatNumber(importLineCount) }} non-empty lines</span><BaseButton variant="primary" type="submit" :loading="pending === 'import'" :disabled="!importForm.name.trim() || !importLineCount"><template #icon><AppIcon name="upload" :size="16" /></template>Import suite</BaseButton></div>
          </form>
        </section>
      </main>

      <main v-else class="workspace">
        <div v-if="suiteLoading && !suite" class="panel loading-card">Loading puzzle suite…</div>
        <template v-else-if="suite">
          <section class="panel suite-hero">
            <div><span class="eyebrow">Suite #{{ suite.id }}</span><h2>{{ suite.name }}</h2><p>Updated {{ formatDate(suite.updated_at) }}</p></div>
            <div class="suite-stats"><div><strong>{{ formatNumber(suite.puzzle_count) }}</strong><span>Imported</span></div><div><strong>{{ formatNumber(suite.unique_count) }}</strong><span>Unique</span></div><div><strong>{{ formatNumber(suite.included_count) }}</strong><span>Included</span></div><div><strong>{{ formatNumber(suite.rated_count) }}</strong><span>Rated</span></div></div>
          </section>

          <section v-if="activeJob" class="panel telemetry">
            <div class="telemetry-head"><div><span class="live-dot" /><span class="eyebrow">Live worker telemetry</span><h2>{{ stageLabel(activeRun?.stage ?? '') }}</h2></div><span class="status-pill" :class="`status-pill--${activeJob.status}`">{{ statusText(activeJob.status) }}</span></div>
            <div class="telemetry-body">
              <div class="progress-copy"><strong>{{ activeJob.progress_detail || 'Waiting for a worker to claim the job' }}</strong><span>{{ formatNumber(activeJob.progress_current) }} / {{ formatNumber(activeJob.progress_total) }} searches</span></div>
              <div class="progress-track"><span :style="{ width: `${progressPercent}%` }" /></div>
              <div class="telemetry-grid"><div><small>Worker</small><strong>{{ activeJob.worker?.label ?? 'Awaiting eligible worker' }}</strong></div><div><small>Resources</small><strong>{{ activeJob.required_threads }} threads · {{ formatNumber(activeJob.required_hash_mb) }} MB</strong></div><div><small>Attempt</small><strong>{{ activeJob.attempt }}</strong></div><div><small>Progress</small><strong>{{ progressPercent }}%</strong></div></div>
              <div v-if="activeJob.items?.length" class="engine-telemetry"><span v-for="item in activeJob.items" :key="item.id"><i :class="`item-${item.status}`" />{{ item.engine_name }} {{ item.engine_version }}<small>{{ statusText(item.status) }}</small></span></div>
            </div>
          </section>

          <div class="stage-grid">
            <section class="panel stage-card" :class="{ complete: latestUniqueness?.job.status === 'completed' }">
              <div class="stage-head"><span class="stage-number">1</span><div><span class="eyebrow">Filter</span><h2>Verify one solution</h2><p>Compare MultiPV #1 and #2 using WDL expected score in sigmoid probability space.</p></div><AppIcon v-if="latestUniqueness && latestUniqueness.job.status === 'completed'" name="check-circle" :size="20" /></div>
              <div class="stage-form">
                <label class="wide">Analysis engine<select v-model.number="uniqueness.engine_id" :disabled="!!activeRun"><option v-for="engine in context?.engines" :key="engine.id" :value="engine.id">{{ engine.name }} {{ engine.version }}</option></select></label>
                <label>Time<input v-model.number="uniqueness.seconds" type="number" min="0.1" max="3600" step="0.1" /><span>seconds</span></label>
                <label>MultiPV<input v-model.number="uniqueness.multipv" type="number" min="2" max="20" /></label>
                <label>Threads<input v-model.number="uniqueness.threads" type="number" min="1" max="1024" /></label>
                <label>Hash<input v-model.number="uniqueness.hash_mb" type="number" min="1" /><span>MB</span></label>
                <label class="wide">Minimum sigmoid gap<input v-model.number="uniqueness.min_gap" type="number" min="0.01" max="1" step="0.01" /><span>{{ Math.round(uniqueness.min_gap * 100) }}%</span></label>
              </div>
              <div class="stage-footer"><span><i :class="{ ready: eligibleWorkers(uniqueness.threads, uniqueness.hash_mb) }" />{{ eligibleWorkers(uniqueness.threads, uniqueness.hash_mb) }} resource-eligible worker{{ eligibleWorkers(uniqueness.threads, uniqueness.hash_mb) === 1 ? '' : 's' }}</span><BaseButton variant="primary" :loading="pending === 'uniqueness'" :disabled="!!activeRun || !uniqueness.engine_id" @click="startUniqueness"><template #icon><AppIcon name="play" :size="15" /></template>{{ latestUniqueness ? 'Run filter again' : 'Run uniqueness filter' }}</BaseButton></div>
            </section>

            <section class="panel stage-card" :class="{ locked: !ratingCandidateCount, complete: latestDifficulty?.job.status === 'completed' }">
              <div class="stage-head"><span class="stage-number">2</span><div><span class="eyebrow">Rate</span><h2>Measure difficulty</h2><p>Track when each engine first finds the target move and combine nodes with list Elo.</p></div><AppIcon v-if="latestDifficulty && latestDifficulty.job.status === 'completed'" name="check-circle" :size="20" /></div>
              <div class="stage-form">
                <label class="wide">Rating list<select v-model.number="difficulty.rating_list_id" :disabled="!!activeRun"><option v-for="item in context?.rating_lists" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
                <label>Time<input v-model.number="difficulty.seconds" type="number" min="0.1" max="3600" step="0.1" /><span>seconds</span></label>
                <label>Threads<input v-model.number="difficulty.threads" type="number" min="1" max="1024" /></label>
                <label>Hash<input v-model.number="difficulty.hash_mb" type="number" min="1" /><span>MB</span></label>
              </div>
              <div class="engine-picker">
                <div class="picker-head"><strong>Engines</strong><span>{{ difficulty.engine_ids.length }} selected · {{ ratedEngines.length }} rated</span></div>
                <div v-if="ratedEngines.length" class="picker-list"><label v-for="engine in ratedEngines" :key="engine.id" :class="{ selected: difficulty.engine_ids.includes(engine.id) }"><input type="checkbox" :checked="difficulty.engine_ids.includes(engine.id)" :disabled="!!activeRun" @change="toggleDifficultyEngine(engine.id)" /><span><strong>{{ engine.name }}</strong><small>{{ engine.version }}</small></span><b>{{ Math.round(ratingMap.get(engine.id) ?? 0) }}</b></label></div>
                <div v-else class="picker-empty">This rating list has no rated engines.</div>
              </div>
              <div class="stage-footer"><span><i :class="{ ready: eligibleWorkers(difficulty.threads, difficulty.hash_mb) }" />{{ ratingCandidateCount }} rating candidates · {{ eligibleWorkers(difficulty.threads, difficulty.hash_mb) }} resource-eligible worker{{ eligibleWorkers(difficulty.threads, difficulty.hash_mb) === 1 ? '' : 's' }}</span><BaseButton variant="primary" :loading="pending === 'difficulty'" :disabled="!!activeRun || !ratingCandidateCount || !difficulty.engine_ids.length || !difficulty.rating_list_id" @click="startDifficulty"><template #icon><AppIcon name="gauge" :size="15" /></template>{{ latestDifficulty ? 'Rate again' : 'Rate difficulty' }}</BaseButton></div>
            </section>
          </div>

          <section class="panel output-card">
            <div class="output-head"><div><span class="eyebrow">Stage output</span><h2>Curated puzzle set</h2><p>Unfiltered suites use their supplied solution directly. Filtered suites use only curated inclusions. Rated output is ordered from easiest to hardest.</p></div><div><BaseButton size="small" variant="ghost" :disabled="!ratingCandidateCount" @click="copyOutput(false)"><template #icon><AppIcon name="copy" :size="14" /></template>Copy filtered</BaseButton><BaseButton size="small" variant="secondary" :disabled="!suite.rated_count" @click="copyOutput(true)"><template #icon><AppIcon name="gauge" :size="14" /></template>Copy ordered</BaseButton></div></div>
            <div class="output-toolbar"><div class="filter-tabs"><button v-for="item in ([['all','All'],['included','Included'],['unique','Unique'],['rejected','Rejected']] as const)" :key="item[0]" type="button" :class="{ active: puzzleFilter === item[0] }" @click="puzzleFilter = item[0]">{{ item[1] }}</button></div><label class="search-box"><AppIcon name="search" :size="14" /><input v-model="puzzleSearch" placeholder="Search FEN or move" /></label><span>{{ formatNumber(displayedPuzzles.length) }} shown</span></div>
            <div class="puzzle-table-wrap">
              <table>
                <thead><tr><th>Keep</th><th>#</th><th>Position</th><th>Solution</th><th>WDL sigmoid</th><th>Effort</th><th>Difficulty</th><th>Status</th></tr></thead>
                <tbody>
                  <tr v-for="puzzle in visiblePuzzles" :key="puzzle.id" :class="{ excluded: !puzzle.included }">
                    <td><input type="checkbox" :checked="puzzle.included" :disabled="puzzle.uniqueness_status !== 'unique' || !!activeRun || pending === `puzzle-${puzzle.id}`" @change="togglePuzzle(puzzle)" /></td>
                    <td>{{ puzzle.position + 1 }}</td>
                    <td class="fen-cell"><code>{{ puzzle.fen }}</code><small v-if="puzzle.uniqueness_error">{{ puzzle.uniqueness_error }}</small></td>
                    <td><strong>{{ puzzle.verified_solution || puzzle.solutions.join(', ') }}</strong><small v-if="puzzle.second_move">runner-up {{ puzzle.second_move }}</small></td>
                    <td><strong>{{ percent(puzzle.best_sigmoid) }}</strong><small v-if="puzzle.sigmoid_gap != null">gap {{ percent(puzzle.sigmoid_gap) }}</small></td>
                    <td><strong>{{ nodes(puzzle.uniqueness_nodes) }}</strong><small v-if="resultMap.get(puzzle.id)?.length">{{ resultMap.get(puzzle.id)?.filter((result) => result.status === 'solved').length }}/{{ resultMap.get(puzzle.id)?.length }} engines solved</small></td>
                    <td><strong>{{ puzzle.difficulty_elo == null ? '—' : Math.round(puzzle.difficulty_elo) }}</strong><small v-if="puzzle.difficulty_elo != null">puzzle Elo</small></td>
                    <td><span class="row-status" :class="`row-status--${puzzle.uniqueness_status}`">{{ statusText(puzzle.uniqueness_status) }}</span></td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-if="displayedPuzzles.length > visiblePuzzles.length" class="table-limit">Showing the first 500 matching puzzles. Narrow the filter or export the complete output.</div>
          </section>

          <section v-if="suite.runs.length" class="panel run-history">
            <div class="history-head"><span class="eyebrow">Run history</span><h2>Separate stage jobs</h2></div>
            <div class="history-list"><div v-for="run in suite.runs" :key="run.id"><span class="history-icon"><AppIcon :name="run.stage === 'uniqueness' ? 'filter' : 'gauge'" :size="16" /></span><span><strong>{{ stageLabel(run.stage) }}</strong><small>{{ formatDate(run.created_at) }} · {{ run.job.worker?.label ?? 'Unclaimed' }}</small></span><span>{{ run.job.completed_items }}/{{ run.job.total_items }} engines</span><b :class="`status-${run.job.status}`">{{ statusText(run.job.status) }}</b></div></div>
          </section>
        </template>
      </main>
    </div>
  </div>
</template>

<style scoped>
.puzzle-manager{gap:1.25rem}.manager-grid{align-items:start;display:grid;gap:1rem;grid-template-columns:16.5rem minmax(0,1fr)}.suite-rail{display:grid;gap:.8rem;position:sticky;top:calc(var(--header-height) + 1rem)}.rail-card{overflow:hidden;padding:.85rem}.rail-heading,.card-heading,.stage-head,.telemetry-head,.output-head{align-items:flex-start;display:flex;gap:.7rem;justify-content:space-between}.rail-heading h2,.card-heading h2,.stage-head h2,.telemetry h2,.output-head h2,.history-head h2,.suite-hero h2{font-size:.9rem;margin:.14rem 0 0}.rail-heading button{align-items:center;background:transparent;border:0;color:var(--color-accent);cursor:pointer;display:flex;font-size:.62rem;gap:.25rem}.eyebrow{color:var(--color-accent);font-size:.58rem;font-weight:780;letter-spacing:.1em;text-transform:uppercase}.suite-list{display:grid;margin:.7rem -.35rem -.35rem}.suite-list button{align-items:center;background:transparent;border:0;border-radius:.5rem;color:inherit;cursor:pointer;display:grid;gap:.4rem;grid-template-columns:minmax(0,1fr) auto;padding:.58rem;text-align:left}.suite-list button:hover,.suite-list button.active{background:var(--color-surface-hover)}.suite-list button.active{box-shadow:inset 2px 0 var(--color-accent)}.suite-list button>span:first-child{display:grid;gap:.12rem;min-width:0}.suite-list strong{font-size:.66rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.suite-list small,.rail-empty{color:var(--color-text-muted);font-size:.56rem}.rail-empty{padding:1.2rem .2rem;text-align:center}.live-dot{animation:pulse 1.2s infinite;background:var(--color-success);border-radius:50%;box-shadow:0 0 0 3px color-mix(in srgb,var(--color-success) 12%,transparent);height:.42rem;width:.42rem}.model-card p{color:var(--color-text-muted);font-size:.62rem;line-height:1.55;margin:.5rem 0 0}.workspace{display:grid;gap:1rem;min-width:0}.import-card{padding:1rem}.card-heading{justify-content:flex-start}.card-heading p,.stage-head p,.output-head p,.suite-hero p{color:var(--color-text-muted);font-size:.64rem;margin:.25rem 0 0}.step-mark,.stage-number,.history-icon{align-items:center;background:var(--color-accent-soft);border-radius:.55rem;color:var(--color-accent);display:flex;flex:none;justify-content:center}.step-mark{height:2.2rem;width:2.2rem}.import-card form{display:grid;gap:.8rem;margin-top:1rem}.import-card label,.stage-form label{color:var(--color-text-muted);display:grid;font-size:.62rem;font-weight:650;gap:.35rem;position:relative}.import-card input,.import-card textarea,.stage-form input,.stage-form select{background:var(--color-surface-sunken);border:1px solid var(--color-border);border-radius:.5rem;color:var(--color-text);font:inherit;outline:0;padding:.58rem .65rem}.import-card textarea{font-family:ui-monospace,monospace;line-height:1.55;min-height:22rem;resize:vertical}.import-card input:focus,.import-card textarea:focus,.stage-form input:focus,.stage-form select:focus{border-color:var(--color-accent);box-shadow:0 0 0 2px var(--color-accent-soft)}.format-chip{background:var(--color-accent-soft);border-radius:.3rem;color:var(--color-accent);font-family:ui-monospace,monospace;font-size:.58rem;padding:.2rem .35rem;position:absolute;right:0;top:-.15rem}.form-footer,.stage-footer{align-items:center;border-top:1px solid var(--color-border);display:flex;justify-content:space-between;padding-top:.75rem}.form-footer>span,.stage-footer>span{color:var(--color-text-muted);font-size:.61rem}.suite-hero{align-items:center;background:linear-gradient(110deg,color-mix(in srgb,var(--color-accent) 8%,var(--color-surface-raised)),var(--color-surface-raised));display:flex;justify-content:space-between;padding:1rem}.suite-stats{display:grid;grid-template-columns:repeat(4,minmax(4rem,1fr))}.suite-stats div{display:grid;padding:.2rem .9rem;text-align:center}.suite-stats div+div{border-left:1px solid var(--color-border)}.suite-stats strong{font-size:1rem}.suite-stats span{color:var(--color-text-muted);font-size:.55rem}.telemetry{border-color:color-mix(in srgb,var(--color-accent) 30%,var(--color-border));overflow:hidden}.telemetry-head{align-items:center;border-bottom:1px solid var(--color-border);padding:.8rem 1rem}.telemetry-head>div{align-items:center;display:grid;gap:.1rem;grid-template-columns:auto 1fr}.telemetry-head h2{grid-column:2}.status-pill,.row-status,.history-list b{border-radius:999px;font-size:.54rem;font-weight:720;padding:.25rem .42rem}.status-pill--queued,.status-queued{background:color-mix(in srgb,var(--color-warning) 10%,transparent);color:var(--color-warning)}.status-pill--running,.status-running{background:var(--color-accent-soft);color:var(--color-accent)}.status-completed{background:color-mix(in srgb,var(--color-success) 10%,transparent);color:var(--color-success)}.status-failed,.status-cancelled{background:color-mix(in srgb,var(--color-danger) 10%,transparent);color:var(--color-danger)}.telemetry-body{display:grid;gap:.65rem;padding:.85rem 1rem}.progress-copy{color:var(--color-text-muted);display:flex;font-size:.61rem;justify-content:space-between}.progress-copy strong{color:var(--color-text)}.progress-track{background:var(--color-surface-sunken);border-radius:999px;height:.42rem;overflow:hidden}.progress-track span{background:linear-gradient(90deg,var(--color-accent),var(--color-success));display:block;height:100%;transition:width .4s ease}.telemetry-grid{display:grid;grid-template-columns:repeat(4,1fr)}.telemetry-grid div{display:grid;gap:.12rem}.telemetry-grid small{color:var(--color-text-muted);font-size:.53rem}.telemetry-grid strong{font-size:.64rem}.engine-telemetry{display:flex;flex-wrap:wrap;gap:.35rem}.engine-telemetry>span{align-items:center;background:var(--color-surface-sunken);border-radius:.4rem;display:flex;font-size:.57rem;gap:.3rem;padding:.3rem .4rem}.engine-telemetry i,.stage-footer i{background:var(--color-text-faint);border-radius:50%;height:.38rem;width:.38rem}.engine-telemetry i.item-running,.stage-footer i.ready{background:var(--color-accent)}.engine-telemetry i.item-supported{background:var(--color-success)}.engine-telemetry i.item-failed,.engine-telemetry i.item-unsupported{background:var(--color-danger)}.engine-telemetry small{color:var(--color-text-muted);margin-left:.2rem}.stage-grid{display:grid;gap:1rem;grid-template-columns:repeat(2,minmax(0,1fr))}.stage-card{display:flex;flex-direction:column;overflow:hidden}.stage-card.complete{border-color:color-mix(in srgb,var(--color-success) 24%,var(--color-border))}.stage-card.locked{opacity:.7}.stage-head{border-bottom:1px solid var(--color-border);justify-content:flex-start;padding:.9rem}.stage-head>div{min-width:0}.stage-head>svg{color:var(--color-success);margin-left:auto}.stage-number{font-size:.7rem;font-weight:800;height:1.8rem;width:1.8rem}.stage-form{display:grid;gap:.6rem;grid-template-columns:repeat(3,1fr);padding:.85rem}.stage-form label.wide{grid-column:1/-1}.stage-form label>span{bottom:.58rem;font-size:.53rem;font-weight:500;position:absolute;right:.55rem}.stage-form label:has(>span) input{padding-right:3.2rem}.stage-form select{width:100%}.engine-picker{border-top:1px solid var(--color-border);display:grid;min-height:11rem}.picker-head{align-items:center;background:var(--color-surface-sunken);display:flex;justify-content:space-between;padding:.5rem .7rem}.picker-head strong{font-size:.63rem}.picker-head span{color:var(--color-text-muted);font-size:.55rem}.picker-list{display:grid;max-height:12rem;overflow:auto}.picker-list label{align-items:center;cursor:pointer;display:grid;gap:.5rem;grid-template-columns:auto minmax(0,1fr) auto;padding:.45rem .7rem}.picker-list label+label{border-top:1px solid var(--color-border)}.picker-list label.selected{background:color-mix(in srgb,var(--color-accent) 5%,transparent)}.picker-list input{accent-color:var(--color-accent)}.picker-list label>span{display:grid}.picker-list strong,.picker-list b{font-size:.61rem}.picker-list small{color:var(--color-text-muted);font-size:.52rem}.picker-list b{color:var(--color-accent)}.picker-empty{color:var(--color-text-muted);font-size:.62rem;padding:2rem;text-align:center}.stage-footer{margin-top:auto;padding:.7rem .85rem}.stage-footer>span{align-items:center;display:flex;gap:.35rem}.output-card{overflow:hidden}.output-head{align-items:center;border-bottom:1px solid var(--color-border);padding:.9rem 1rem}.output-head>div:last-child{display:flex;gap:.4rem}.output-toolbar{align-items:center;background:var(--color-surface-sunken);border-bottom:1px solid var(--color-border);display:flex;gap:.6rem;padding:.5rem .7rem}.filter-tabs{background:var(--color-surface-raised);border:1px solid var(--color-border);border-radius:.45rem;display:flex;padding:.12rem}.filter-tabs button{background:transparent;border:0;border-radius:.32rem;color:var(--color-text-muted);cursor:pointer;font-size:.57rem;padding:.32rem .45rem}.filter-tabs button.active{background:var(--color-accent-soft);color:var(--color-accent)}.search-box{align-items:center;background:var(--color-surface-raised);border:1px solid var(--color-border);border-radius:.45rem;color:var(--color-text-muted);display:flex;gap:.35rem;margin-left:auto;padding:.34rem .45rem}.search-box input{background:transparent;border:0;color:var(--color-text);font-size:.59rem;outline:0;width:10rem}.output-toolbar>span{color:var(--color-text-muted);font-size:.56rem}.puzzle-table-wrap{overflow:auto}.puzzle-table-wrap table{border-collapse:collapse;min-width:62rem;width:100%}.puzzle-table-wrap th{background:var(--color-surface-sunken);color:var(--color-text-muted);font-size:.52rem;font-weight:700;letter-spacing:.04em;padding:.45rem .55rem;text-align:left;text-transform:uppercase}.puzzle-table-wrap td{font-size:.59rem;padding:.5rem .55rem;vertical-align:middle}.puzzle-table-wrap tr+tr td{border-top:1px solid var(--color-border)}.puzzle-table-wrap tr.excluded{opacity:.52}.puzzle-table-wrap input{accent-color:var(--color-accent)}.puzzle-table-wrap td>strong,.puzzle-table-wrap td>small{display:block}.puzzle-table-wrap td>small{color:var(--color-text-muted);font-size:.5rem;margin-top:.12rem}.fen-cell{max-width:25rem}.fen-cell code{display:block;font-size:.54rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.fen-cell small{color:var(--color-danger)!important}.row-status--unique{background:color-mix(in srgb,var(--color-success) 10%,transparent);color:var(--color-success)}.row-status--ambiguous{background:color-mix(in srgb,var(--color-warning) 10%,transparent);color:var(--color-warning)}.row-status--failed{background:color-mix(in srgb,var(--color-danger) 10%,transparent);color:var(--color-danger)}.row-status--pending{background:var(--color-surface-sunken);color:var(--color-text-muted)}.table-limit{border-top:1px solid var(--color-border);color:var(--color-text-muted);font-size:.58rem;padding:.65rem;text-align:center}.run-history{overflow:hidden}.history-head{border-bottom:1px solid var(--color-border);padding:.8rem 1rem}.history-list{display:grid}.history-list>div{align-items:center;display:grid;gap:.6rem;grid-template-columns:auto minmax(0,1fr) auto auto;padding:.6rem .85rem}.history-list>div+div{border-top:1px solid var(--color-border)}.history-icon{height:1.8rem;width:1.8rem}.history-list>div>span:nth-child(2){display:grid}.history-list strong{font-size:.63rem}.history-list small,.history-list>div>span:nth-child(3){color:var(--color-text-muted);font-size:.54rem}.loading-card{color:var(--color-text-muted);font-size:.7rem;padding:3rem;text-align:center}@keyframes pulse{50%{opacity:.3}}@media(max-width:78rem){.stage-grid{grid-template-columns:1fr}}@media(max-width:64rem){.manager-grid{grid-template-columns:1fr}.suite-rail{grid-template-columns:1fr 1fr;position:static}}@media(max-width:45rem){.suite-rail{grid-template-columns:1fr}.suite-hero{align-items:flex-start;flex-direction:column}.suite-stats{width:100%}.telemetry-grid{gap:.6rem;grid-template-columns:1fr 1fr}.stage-form{grid-template-columns:1fr 1fr}.output-head{align-items:flex-start;flex-direction:column}.output-toolbar{align-items:stretch;flex-wrap:wrap}.search-box{margin-left:0;width:100%}.search-box input{width:100%}.history-list>div{grid-template-columns:auto minmax(0,1fr) auto}.history-list>div>span:nth-child(3){display:none}}
</style>
