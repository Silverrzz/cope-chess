<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import LiveParticipantManager from '@/components/admin/LiveParticipantManager.vue'
import StatusBadge from '@/components/admin/StatusBadge.vue'
import TournamentConfigForm from '@/components/admin/TournamentConfigForm.vue'
import TournamentScheduleEditor from '@/components/admin/TournamentScheduleEditor.vue'
import { errorText, formatDate, formatDuration, formatRelativeDate, formatTimeControl, humanize } from '@/components/admin/format'
import { buildTournamentDetailsMarkdown } from '@/components/public/tournamentSummary'
import AppIcon from '@/components/ui/AppIcon.vue'
import type { Engine, FormSeed, Game, Tournament, TournamentConfig, TournamentEstimate } from '@/components/admin/types'

interface Commit { rating_list_id: number; status: string; requested_at: string; applied_at?: string | null; error?: string | null }
interface RatingList { id: number; name: string }
interface GamePagination { page: number; page_size: number; total: number; pages: number }
interface GameSummary { total: number; pairs: number; pending: number; assigned: number; live: number; finished: number; abandoned: number }
interface LiveParticipantSummary { engine_id: number; total: number; pending: number; assigned: number; live: number; finished: number; abandoned: number }
interface LiveTournamentRoster { editable: boolean; reason: string; available_engines: Engine[]; participants: LiveParticipantSummary[]; hero_engine_id: number | null }
interface ResultFilterOption { value: string; label: string }
interface TournamentViewer { id: string; name: string; name_source: 'current' | 'history' | 'anonymous'; connections: number }
interface Response {
  tournament: Tournament
  viewers: TournamentViewer[]
  games: Game[]
  engines: Engine[] | Record<string, Engine | string>
  settings?: Array<[string, string] | { label: string; value: string }> | Record<string, string>
  commits: Commit[]
  rating_lists: RatingList[]
  actions: Record<string, string>
  game_pagination: GamePagination
  game_summary: GameSummary
  roster: LiveTournamentRoster
  estimate: TournamentEstimate
  capabilities: { editable: boolean; schedulable: boolean; unschedulable: boolean; concurrency_editable: boolean; deletable: boolean; can_commit_ratings: boolean }
  form?: FormSeed
}

const resultFilterGroups: Array<{ label: string; options: ResultFilterOption[] }> = [
  {
    label: 'Wins',
    options: [
      { value: 'win_checkmate', label: 'Checkmate' },
      { value: 'win_king_capture', label: 'King capture' },
      { value: 'win_no_legal_move', label: 'No legal move' },
      { value: 'win_adjudication', label: 'Adjudication' },
      { value: 'win_max_moves', label: 'Maximum moves' },
      { value: 'win_timeout', label: 'Timeout' },
      { value: 'win_illegal_move', label: 'Illegal move' },
      { value: 'win_engine_error', label: 'Engine error' },
      { value: 'win_variant_end', label: 'Variant end' },
      { value: 'win_other', label: 'Other' },
    ],
  },
  {
    label: 'Draws',
    options: [
      { value: 'draw_stalemate', label: 'Stalemate' },
      { value: 'draw_insufficient_material', label: 'Insufficient material' },
      { value: 'draw_adjudication', label: 'Adjudication' },
      { value: 'draw_max_moves', label: 'Maximum moves' },
      { value: 'draw_threefold_repetition', label: 'Threefold repetition' },
      { value: 'draw_fivefold_repetition', label: 'Fivefold repetition' },
      { value: 'draw_fifty_moves', label: 'Fifty moves' },
      { value: 'draw_seventyfive_moves', label: 'Seventy-five moves' },
      { value: 'draw_variant_end', label: 'Variant end' },
      { value: 'draw_other', label: 'Other' },
    ],
  },
]

const route = useRoute()
const router = useRouter()
const toast = useToast()
const { confirm } = useConfirm()
const data = ref<Response | null>(null)
const loading = ref(true)
const gamesLoading = ref(false)
const error = ref('')
const pending = ref('')
const copyingDetails = ref(false)
const detailsCopied = ref(false)
const concurrency = ref(1)
const tournamentName = ref('')
const showCommit = ref(false)
const showSchedule = ref(false)
const showRename = ref(false)
const nameInput = ref<HTMLInputElement | null>(null)
const activePanel = ref<'games' | 'participants' | 'settings'>('games')
const clock = ref(Date.now())
const selectedLists = ref<number[]>([])
const selectedResultTypes = ref<string[]>([])
const gamePage = ref(1)
let loadSequence = 0
let clockTimer: number | undefined
let viewersTimer: number | undefined
let viewersController: AbortController | null = null
const viewersError = ref('')
const id = computed(() => Number(route.params.id))
const hasCommittableGames = computed(() => (data.value?.game_summary.finished || 0) > 0)
const activeGames = computed(() => (
  (data.value?.game_summary.assigned || 0) + (data.value?.game_summary.live || 0)
))
const resultsLocked = computed(() => data.value?.commits.some(
  (item) => ['pending', 'claimed', 'applied'].includes(item.status),
) ?? false)
const resultFilterLabel = computed(() => selectedResultTypes.value.length
  ? `Game result (${selectedResultTypes.value.length})`
  : 'Game result')
const lifecycleStartAt = computed(() => data.value?.tournament.started_at ?? data.value?.tournament.scheduled_start_at)
const lifecycleStartLabel = computed(() => data.value?.tournament.started_at ? 'Started' : 'Scheduled start')
const completedPercent = computed(() => data.value?.game_summary.total
  ? Math.min(100, Math.round(data.value.game_summary.finished / data.value.game_summary.total * 100))
  : 0)
const settingsRows = computed(() => {
  if (!data.value?.settings) return []
  return Array.isArray(data.value.settings)
    ? data.value.settings.map((row) => Array.isArray(row) ? row : [row.label, row.value] as [string, string])
    : Object.entries(data.value.settings)
})
const estimateBasis = computed(() => {
  const estimate = data.value?.estimate
  if (!estimate?.median_game_seconds) return 'Waiting for completed games with comparable settings.'
  const source = estimate.basis === 'historical'
    ? `${estimate.sample_size} comparable historical game${estimate.sample_size === 1 ? '' : 's'}`
    : `${estimate.sample_size} completed game${estimate.sample_size === 1 ? '' : 's'}`
  return `Based on a ${formatDuration(estimate.median_game_seconds)} median across ${source} at concurrency ${estimate.concurrency}.`
})
const engineLabels = computed<Record<number, string>>(() => {
  if (!data.value) return {}
  const labels: Record<number, string> = {}
  for (const engineId of data.value.tournament.config.participants) labels[engineId] = engineName(engineId)
  for (const engine of data.value.roster.available_engines) {
    labels[engine.id] = [engine.name, engine.version].filter(Boolean).join(' ')
  }
  return labels
})

function engineName(engineId: number): string {
  const engines = data.value?.engines
  if (Array.isArray(engines)) return engines.find((engine) => (engine.id ?? engine.engine_id) === engineId)?.name ?? `Engine ${engineId}`
  const engine = engines?.[String(engineId)]
  return typeof engine === 'string' ? engine : engine?.name ?? `Engine ${engineId}`
}

async function load(): Promise<void> {
  const sequence = ++loadSequence
  const resultTypes = [...selectedResultTypes.value]
  loading.value = !data.value
  gamesLoading.value = true
  error.value = ''
  try {
    let response = await api.get<Response>(`/api/admin/tournaments/${id.value}`, {
      query: { page: gamePage.value, result_type: resultTypes },
    })
    if (sequence !== loadSequence) return
    if (gamePage.value > response.game_pagination.pages) {
      gamePage.value = response.game_pagination.pages
      response = await api.get<Response>(`/api/admin/tournaments/${id.value}`, {
        query: { page: gamePage.value, result_type: resultTypes },
      })
      if (sequence !== loadSequence) return
    }
    const previousName = data.value?.tournament.name
    const previousConcurrency = data.value?.tournament.config.concurrency
    if (!data.value && response.tournament.status === 'draft') activePanel.value = 'settings'
    data.value = response
    if (!previousName || tournamentName.value === previousName) tournamentName.value = response.tournament.name
    if (previousConcurrency === undefined || concurrency.value === previousConcurrency) concurrency.value = response.tournament.config.concurrency
  }
  catch (cause) {
    if (sequence === loadSequence) error.value = errorText(cause)
  }
  finally {
    if (sequence === loadSequence) {
      loading.value = false
      gamesLoading.value = false
    }
  }
}

async function setGamePage(page: number): Promise<void> {
  const pages = data.value?.game_pagination.pages || 1
  if (page < 1 || page > pages || page === gamePage.value) return
  gamePage.value = page
  await load()
}

async function loadViewers(): Promise<void> {
  if (!data.value || viewersController) return
  const tournamentId = id.value
  viewersController = new AbortController()
  try {
    const response = await api.get<{ viewers: TournamentViewer[] }>(`/api/admin/tournaments/${tournamentId}/viewers`, { signal: viewersController.signal })
    if (id.value === tournamentId && data.value) {
      data.value.viewers = response.viewers
      viewersError.value = ''
    }
  } catch (cause) {
    if ((cause as { name?: string }).name !== 'AbortError') viewersError.value = 'Viewer list could not refresh. Retrying…'
  } finally {
    viewersController = null
  }
}

async function applyResultFilters(): Promise<void> {
  gamePage.value = 1
  await load()
}

async function clearResultFilters(): Promise<void> {
  selectedResultTypes.value = []
  await applyResultFilters()
}

async function changeStatus(action: string): Promise<void> {
  if (action === 'abort') {
    const accepted = await confirm({ title: 'Abort tournament?', message: `Abort “${data.value?.tournament.name}”? Unfinished games will not be played.`, confirmLabel: 'Abort tournament', tone: 'danger' })
    if (!accepted) return
  }
  pending.value = action
  try {
    const response = await api.post<{ message: string }>(`/api/admin/tournaments/${id.value}/status`, { body: { action } })
    toast.success(response.message)
    await load()
  } catch (cause) { error.value = errorText(cause); toast.error(cause) }
  finally { pending.value = '' }
}

async function saveDraft(payload: { name: string; config: TournamentConfig }): Promise<void> {
  pending.value = 'save'
  try {
    const response = await api.put<{ message: string }>(`/api/admin/tournaments/${id.value}`, { body: payload })
    toast.success(response.message)
    await load()
  } catch (cause) { error.value = errorText(cause); toast.error(cause) }
  finally { pending.value = '' }
}

async function renameTournament(): Promise<void> {
  if (!data.value) return
  const name = tournamentName.value.trim()
  if (!name) { error.value = 'Enter a tournament name.'; return }
  pending.value = 'name'
  try {
    const response = await api.put<{ message: string }>(`/api/admin/tournaments/${id.value}/name`, { body: { name } })
    tournamentName.value = name
    toast.success(response.message)
    await load()
    showRename.value = false
  } catch (cause) { error.value = errorText(cause); toast.error(cause) }
  finally { pending.value = '' }
}

async function beginRename(): Promise<void> {
  tournamentName.value = data.value?.tournament.name ?? ''
  showRename.value = true
  await nextTick()
  nameInput.value?.focus()
  nameInput.value?.select()
}

function switchTab(event: KeyboardEvent): void {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  const tabs = Array.from((event.currentTarget as HTMLElement).querySelectorAll<HTMLButtonElement>('[role="tab"]'))
  const current = tabs.indexOf(event.target as HTMLButtonElement)
  if (current < 0) return
  event.preventDefault()
  const index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
    : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length
  tabs[index]?.focus()
  tabs[index]?.click()
}

async function saveSchedule(scheduledStartAt: string): Promise<void> {
  if (!data.value) return
  pending.value = 'schedule'
  error.value = ''
  try {
    const path = `/api/admin/tournaments/${id.value}/schedule`
    const options = { body: { scheduled_start_at: scheduledStartAt } }
    const response = data.value.tournament.status === 'scheduled'
      ? await api.patch<{ message: string }>(path, options)
      : await api.post<{ message: string }>(path, options)
    toast.success(response.message)
    showSchedule.value = false
    await load()
  } catch (cause) { error.value = errorText(cause); toast.error(cause) }
  finally { pending.value = '' }
}

async function startTournament(): Promise<void> {
  pending.value = 'start'
  error.value = ''
  try {
    const response = await api.post<{ message: string }>(`/api/admin/tournaments/${id.value}/start`)
    toast.success(response.message)
    showSchedule.value = false
    await load()
  } catch (cause) { error.value = errorText(cause); toast.error(cause) }
  finally { pending.value = '' }
}

async function unschedule(): Promise<void> {
  if (!data.value) return
  const accepted = await confirm({
    title: 'Return tournament to draft?',
    message: `Unschedule “${data.value.tournament.name}”? Its unplayed generated schedule will be cleared so the draft can be edited.`,
    confirmLabel: 'Return to draft',
  })
  if (!accepted) return
  pending.value = 'unschedule'
  try {
    const response = await api.delete<{ message: string }>(`/api/admin/tournaments/${id.value}/schedule`)
    toast.success(response.message)
    showSchedule.value = false
    await load()
    activePanel.value = 'settings'
  } catch (cause) { error.value = errorText(cause); toast.error(cause) }
  finally { pending.value = '' }
}

async function saveConcurrency(): Promise<void> {
  if (!data.value || !['running', 'paused'].includes(data.value.tournament.status)) return
  if (!Number.isInteger(concurrency.value) || concurrency.value < 1) {
    error.value = 'Concurrent games must be a whole number of at least 1.'
    return
  }
  pending.value = 'concurrency'
  try {
    const tournament = data.value.tournament
    const response = await api.put<{ message: string }>(`/api/admin/tournaments/${id.value}`, {
      body: {
        name: tournament.name,
        config: { ...tournament.config, concurrency: concurrency.value },
      },
    })
    toast.success(response.message)
    await load()
  } catch (cause) { error.value = errorText(cause); toast.error(cause) }
  finally { pending.value = '' }
}

async function addParticipant(engineId: number): Promise<void> {
  pending.value = 'participant-add'
  try {
    const response = await api.post<{ message: string }>(`/api/admin/tournaments/${id.value}/participants`, {
      body: { engine_id: engineId },
    })
    toast.success(response.message)
    await load()
  } catch (cause) { error.value = errorText(cause); toast.error(cause) }
  finally { pending.value = '' }
}

async function removeParticipant(participant: LiveParticipantSummary): Promise<void> {
  if (!data.value) return
  const engineId = participant.engine_id
  const label = engineName(engineId)
  const active = participant.assigned + participant.live
  const queued = participant.pending
  const finished = participant.finished
  const isHero = data.value.roster.hero_engine_id === engineId
  const replacementId = data.value.tournament.config.participants.find((item) => item !== engineId)
  const heroImpact = isHero && replacementId
    ? ` ${engineName(replacementId)} will become the gauntlet hero and receive a new schedule against every remaining opponent.`
    : ''
  const accepted = await confirm({
    title: `Remove ${label}?`,
    message: `Remove this engine from the live field? ${finished} completed result${finished === 1 ? '' : 's'} will be invalidated, ${queued} queued game${queued === 1 ? '' : 's'} canceled, and ${active} active game${active === 1 ? '' : 's'} stopped.${heroImpact}`,
    confirmLabel: 'Remove participant',
    tone: 'danger',
  })
  if (!accepted) return
  pending.value = `participant-remove-${engineId}`
  try {
    const response = await api.delete<{ message: string }>(`/api/admin/tournaments/${id.value}/participants/${engineId}`)
    toast.success(response.message)
    await load()
  } catch (cause) { error.value = errorText(cause); toast.error(cause) }
  finally { pending.value = '' }
}

async function commitRatings(): Promise<void> {
  if (!selectedLists.value.length) { error.value = 'Choose at least one rating list.'; return }
  pending.value = 'commit'
  try {
    const response = await api.post<{ message: string }>(`/api/admin/tournaments/${id.value}/commit-results`, { body: { rating_list_ids: selectedLists.value } })
    toast.success(response.message)
    await load()
    showCommit.value = false
    selectedLists.value = []
  } catch (cause) { error.value = errorText(cause); toast.error(cause) }
  finally { pending.value = '' }
}

async function remove(): Promise<void> {
  if (!data.value) return
  const accepted = await confirm({ title: 'Delete tournament?', message: `Delete “${data.value.tournament.name}” and every associated game? This cannot be undone.`, confirmLabel: 'Delete tournament', tone: 'danger' })
  if (!accepted) return
  pending.value = 'delete'
  try {
    const response = await api.delete<{ message: string }>(`/api/admin/tournaments/${id.value}`)
    toast.success(response.message)
    await router.push('/admin/tournaments')
  } catch (cause) { error.value = errorText(cause); toast.error(cause) }
  finally { pending.value = '' }
}

async function copyDetails(): Promise<void> {
  if (!data.value) return
  copyingDetails.value = true
  try {
    const tournament = data.value.tournament
    const details = buildTournamentDetailsMarkdown({
      tournament,
      settings: settingsRows.value.filter(([label]) => ![
        'Concurrent games',
        'Worker hash required',
        'Lag compensation',
      ].includes(label)),
      engines: tournament.config.participants.map(engineName),
      publicUrl: `${window.location.origin}/tournaments/${encodeURIComponent(String(tournament.id))}`,
    })
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(details)
    else fallbackCopy(details)
    detailsCopied.value = true
    window.setTimeout(() => { detailsCopied.value = false }, 2000)
    toast.success('Tournament details copied as Markdown.')
  } catch (cause) {
    toast.error(cause)
  } finally {
    copyingDetails.value = false
  }
}

function fallbackCopy(value: string): void {
  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.append(textarea)
  textarea.select()
  const copied = document.execCommand('copy')
  textarea.remove()
  if (!copied) throw new Error('Copy failed')
}

async function replayGame(game: Game): Promise<void> {
  const accepted = await confirm({ title: 'Replay game?', message: `Delete the completed result for ${engineName(game.white_engine_id)} vs ${engineName(game.black_engine_id)} and return this game to pending?`, confirmLabel: 'Replay game', tone: 'danger' })
  if (!accepted) return
  pending.value = `replay-${game.id}`
  try {
    const response = await api.post<{ message: string }>(`/api/admin/tournaments/${id.value}/games/${game.id}/replay`)
    toast.success(response.message)
    await load()
  } catch (cause) { error.value = errorText(cause); toast.error(cause) }
  finally { pending.value = '' }
}

async function invalidateGame(game: Game): Promise<void> {
  const accepted = await confirm({ title: 'Invalidate game pair?', message: `Permanently exclude ${engineName(game.white_engine_id)} vs ${engineName(game.black_engine_id)} and its paired reverse-colour game from this tournament? They will no longer be viewable or included in ratings.`, confirmLabel: 'Invalidate pair', tone: 'danger' })
  if (!accepted) return
  pending.value = `invalidate-${game.id}`
  try {
    const response = await api.post<{ message: string }>(`/api/admin/tournaments/${id.value}/games/${game.id}/invalidate`)
    toast.success(response.message)
    await load()
  } catch (cause) { error.value = errorText(cause); toast.error(cause) }
  finally { pending.value = '' }
}

onMounted(() => {
  void load()
  viewersTimer = window.setInterval(() => { void loadViewers() }, 5_000)
  clockTimer = window.setInterval(() => {
    clock.value = Date.now()
    if (data.value && ['scheduled', 'running', 'paused'].includes(data.value.tournament.status)) void load()
  }, 10_000)
})
onBeforeUnmount(() => {
  window.clearInterval(clockTimer)
  window.clearInterval(viewersTimer)
  viewersController?.abort()
})
</script>

<template>
  <div class="admin-page tournament-page">
    <div v-if="loading" class="panel detail-loading" role="status">Loading tournament…</div>
    <template v-else-if="data">
      <InlineFeedback :message="error" />
      <section class="panel tournament-workspace">
        <header class="tournament-heading">
          <div class="tournament-heading__navigation">
            <RouterLink to="/admin/tournaments">← All tournaments</RouterLink>
            <div><a :href="`/api/pgn?tournament_id=${encodeURIComponent(id)}`" download>Download PGN</a><RouterLink :to="`/tournaments/${id}`">Public page <span aria-hidden="true">↗</span></RouterLink></div>
          </div>
          <div class="tournament-heading__title">
            <form v-if="showRename" class="rename-form" @submit.prevent="renameTournament" @keydown.esc="showRename = false">
              <input ref="nameInput" v-model="tournamentName" class="input" required maxlength="160" aria-label="Tournament name">
              <button class="button button--primary button--small" type="submit" :disabled="!!pending || !tournamentName.trim() || tournamentName.trim() === data.tournament.name">{{ pending === 'name' ? 'Saving…' : 'Save' }}</button>
              <button class="button button--ghost button--small" type="button" :disabled="!!pending" @click="showRename = false">Cancel</button>
            </form>
            <template v-else>
              <h1>{{ data.tournament.name }}</h1>
              <button v-if="data.tournament.status !== 'draft'" class="rename-trigger" type="button" @click="beginRename">Rename</button>
            </template>
            <StatusBadge :status="data.tournament.status" />
          </div>
          <div class="tournament-heading__meta"><span>{{ humanize(data.tournament.config.format) }}</span><span>{{ formatTimeControl(data.tournament.config.time_control) }}</span><span>{{ data.tournament.config.participants.length }} engines</span><span>{{ data.tournament.config.rated ? 'Rated' : 'Unrated' }}</span><span class="created-at">Created {{ formatDate(data.tournament.created_at) }}</span></div>
        </header>

        <div v-if="data.tournament.status !== 'draft'" class="tournament-overview">
          <div class="tournament-progress">
            <div><strong>{{ data.game_summary.finished.toLocaleString() }} <span>/ {{ data.game_summary.total.toLocaleString() }} completed</span></strong><span>{{ completedPercent }}%</span></div>
            <div class="progress-track" role="progressbar" aria-label="Completed games" :aria-valuenow="completedPercent" :aria-valuemin="0" :aria-valuemax="100"><span :style="{ width: `${completedPercent}%` }" /></div>
            <small>{{ activeGames }} active · {{ data.game_summary.pending.toLocaleString() }} queued<template v-if="data.game_summary.abandoned"> · {{ data.game_summary.abandoned.toLocaleString() }} abandoned</template></small>
          </div>
          <div class="tournament-timing"><span>{{ lifecycleStartLabel }}</span><strong>{{ formatDate(lifecycleStartAt) }}</strong><small v-if="data.tournament.status === 'scheduled' && data.tournament.scheduled_start_at">{{ formatRelativeDate(data.tournament.scheduled_start_at, clock) }}</small></div>
          <div class="tournament-timing" :title="estimateBasis" tabindex="0" :aria-label="`${['finished', 'aborted'].includes(data.tournament.status) ? 'Finished' : 'Estimated finish'}. ${estimateBasis}`">
            <span>{{ ['finished', 'aborted'].includes(data.tournament.status) ? 'Finished' : 'Estimated finish' }}</span>
            <strong v-if="['finished', 'aborted'].includes(data.tournament.status)">{{ formatDate(data.tournament.finished_at) }}</strong>
            <strong v-else-if="data.estimate.estimated_finish_at">{{ formatDate(data.estimate.estimated_finish_at) }}</strong>
            <strong v-else-if="data.estimate.state === 'paused'">{{ formatDuration(data.estimate.estimated_remaining_seconds) }} after resume</strong>
            <strong v-else>Calculating</strong>
            <small v-if="data.estimate.estimated_finish_at">{{ formatRelativeDate(data.estimate.estimated_finish_at, clock) }}</small>
          </div>
        </div>

        <div class="tournament-toolbar">
          <div class="tournament-toolbar__actions">
            <button class="button button--secondary button--small" type="button" :disabled="copyingDetails" @click="copyDetails"><AppIcon name="copy" :size="14" />{{ copyingDetails ? 'Copying…' : detailsCopied ? 'Copied' : 'Copy details' }}</button>
            <button v-if="data.capabilities.schedulable" class="button button--primary button--small" type="button" :disabled="!!pending" @click="showSchedule = !showSchedule">{{ data.tournament.status === 'scheduled' ? 'Reschedule' : 'Schedule' }}</button>
            <button v-if="data.capabilities.unschedulable" class="button button--secondary button--small" type="button" :disabled="!!pending" @click="unschedule">{{ pending === 'unschedule' ? 'Working…' : 'Return to draft' }}</button>
            <button v-for="(_, action) in data.actions" :key="action" class="button button--small" :class="action === 'abort' ? 'button--danger' : 'button--primary'" type="button" :disabled="!!pending" @click="changeStatus(String(action))">{{ pending === action ? (action === 'pause' ? 'Finishing current moves…' : 'Working…') : humanize(String(action)) }}</button>
            <button v-if="['finished', 'aborted'].includes(data.tournament.status) && hasCommittableGames && data.tournament.config.rated" class="button button--primary button--small" type="button" :disabled="!!pending || !data.rating_lists.length" @click="showCommit = !showCommit; activePanel = 'settings'">Commit ratings</button>
            <button v-if="!['scheduled', 'running'].includes(data.tournament.status) && !data.commits.some((item) => ['pending','claimed','applied'].includes(item.status))" class="button button--danger button--small" type="button" :disabled="!!pending" @click="remove">{{ pending === 'delete' ? 'Deleting…' : 'Delete' }}</button>
          </div>
          <form v-if="['running', 'paused'].includes(data.tournament.status)" class="concurrency-form" @submit.prevent="saveConcurrency">
            <label for="tournament-concurrency">Concurrency</label><input id="tournament-concurrency" v-model.number="concurrency" class="input" type="number" min="1" step="1" required>
            <button v-if="concurrency !== data.tournament.config.concurrency" class="button button--primary button--small" type="submit" :disabled="!!pending">{{ pending === 'concurrency' ? 'Saving…' : 'Save' }}</button>
          </form>
        </div>

        <TournamentScheduleEditor v-if="showSchedule && data.capabilities.schedulable" class="inline-schedule" :initial-value="data.tournament.scheduled_start_at" :pending="['schedule', 'start'].includes(pending)" @submit="saveSchedule" @start="startTournament" @cancel="showSchedule = false" />

        <div class="viewer-strip" aria-label="Current viewers">
          <span class="viewer-strip__label"><AppIcon name="user" :size="14" />Viewing now <strong>{{ data.viewers.length }}</strong></span>
          <ul v-if="data.viewers.length" class="viewer-list">
            <li v-for="viewer in data.viewers" :key="viewer.id" :title="viewer.name_source === 'history' ? 'Last saved chat name' : viewer.name_source === 'anonymous' ? 'No saved chat name' : 'Current chat name'">{{ viewer.name }}<small v-if="viewer.name_source === 'history'">saved</small><small v-if="viewer.connections > 1">{{ viewer.connections }} tabs</small></li>
          </ul>
          <span v-else class="viewer-strip__empty">No viewers</span>
          <span v-if="viewersError" class="viewer-strip__error" role="status">{{ viewersError }}</span>
        </div>

        <nav class="workspace-tabs" role="tablist" aria-label="Tournament sections" @keydown="switchTab">
          <button id="games-tab" role="tab" type="button" :aria-selected="activePanel === 'games'" :tabindex="activePanel === 'games' ? 0 : -1" aria-controls="tournament-games" @click="activePanel = 'games'">Games <span>{{ data.game_summary.total.toLocaleString() }}</span></button>
          <button id="participants-tab" role="tab" type="button" :aria-selected="activePanel === 'participants'" :tabindex="activePanel === 'participants' ? 0 : -1" aria-controls="tournament-participants" @click="activePanel = 'participants'">Participants <span>{{ data.tournament.config.participants.length }}</span></button>
          <button id="settings-tab" role="tab" type="button" :aria-selected="activePanel === 'settings'" :tabindex="activePanel === 'settings' ? 0 : -1" aria-controls="tournament-settings" @click="activePanel = 'settings'">Settings<template v-if="data.commits.length"> &amp; ratings</template></button>
        </nav>
      <section id="tournament-games" v-show="activePanel === 'games'" class="games-panel" role="tabpanel" aria-labelledby="games-tab" :aria-busy="gamesLoading">
        <div class="games-panel__heading">
          <div>
            <h2>Games</h2>
            <p v-if="selectedResultTypes.length">{{ data.game_pagination.total }} matching game{{ data.game_pagination.total === 1 ? '' : 's' }} of {{ data.game_summary.total }} generated</p>
            <p v-else>{{ data.game_pagination.total }} generated game{{ data.game_pagination.total === 1 ? '' : 's' }} · {{ data.game_summary.pairs }} game pair{{ data.game_summary.pairs === 1 ? '' : 's' }}</p>
          </div>
          <details class="result-filter">
            <summary class="button button--secondary button--small">{{ resultFilterLabel }}</summary>
            <div class="result-filter__menu">
              <div class="result-filter__topline">
                <strong>Result type</strong>
                <button v-if="selectedResultTypes.length" class="button button--ghost button--small" type="button" @click="clearResultFilters">Clear</button>
              </div>
              <fieldset v-for="group in resultFilterGroups" :key="group.label">
                <legend>{{ group.label }}</legend>
                <label v-for="option in group.options" :key="option.value">
                  <input v-model="selectedResultTypes" type="checkbox" :value="option.value" @change="applyResultFilters">
                  <span>{{ group.label === 'Wins' ? 'Win' : 'Draw' }} - {{ option.label }}</span>
                </label>
              </fieldset>
            </div>
          </details>
        </div>
        <div v-if="data.games.length" class="game-table-wrap">
          <table class="data-table"><thead><tr><th>Round</th><th>White</th><th>Result</th><th>Black</th><th>Status</th><th>Finished</th><th>Actions</th></tr></thead><tbody>
            <tr v-for="game in data.games" :key="game.id">
              <td>{{ game.round }}</td><td><RouterLink :to="`/tournaments/${id}?game_id=${game.id}`">{{ engineName(game.white_engine_id) }}</RouterLink></td><td><strong>{{ game.result ?? 'vs' }}</strong></td><td>{{ engineName(game.black_engine_id) }}</td><td><StatusBadge :status="game.status" /></td><td>{{ formatDate(game.finished_at) }}</td><td><div class="game-actions"><button v-if="game.status === 'finished' && game.result" class="button button--secondary button--small" type="button" :disabled="!!pending || resultsLocked" :title="resultsLocked ? 'Uncommit tournament ratings first.' : 'Replay this completed game'" @click="replayGame(game)">{{ pending === `replay-${game.id}` ? 'Resetting…' : 'Replay' }}</button><button class="button button--danger button--small" type="button" :disabled="!!pending || resultsLocked" :title="resultsLocked ? 'Uncommit tournament ratings first.' : 'Invalidate this game pair'" @click="invalidateGame(game)">{{ pending === `invalidate-${game.id}` ? 'Invalidating…' : 'Invalidate' }}</button></div></td>
            </tr>
          </tbody></table>
        </div>
        <AdminEmptyState v-else :title="selectedResultTypes.length ? 'No games match these result types' : 'No games generated'" />
        <nav v-if="data.game_pagination.pages > 1" class="pagination" aria-label="Game pages">
          <button class="button button--secondary button--small" type="button" :disabled="gamePage <= 1 || gamesLoading" @click="setGamePage(gamePage - 1)">Previous</button>
          <span>Page {{ gamePage.toLocaleString() }} of {{ data.game_pagination.pages.toLocaleString() }}</span>
          <button class="button button--secondary button--small" type="button" :disabled="gamePage >= data.game_pagination.pages || gamesLoading" @click="setGamePage(gamePage + 1)">Next</button>
        </nav>
      </section>
        <section id="tournament-settings" v-show="activePanel === 'settings'" class="configuration-panel" role="tabpanel" aria-labelledby="settings-tab">
          <TournamentConfigForm v-if="data.tournament.status === 'draft' && data.form" :seed="data.form" :pending="pending === 'save'" submit-label="Save draft" @submit="saveDraft" @cancel="router.push('/admin/tournaments')" />
          <div v-else class="settings-panel">
        <dl v-if="settingsRows.length" class="definition-list">
          <div v-for="([label, value], index) in settingsRows" :key="`${label}-${index}`"><dt>{{ label }}</dt><dd>{{ value }}</dd></div>
        </dl>
        <dl v-else class="definition-list">
          <div><dt>Format</dt><dd>{{ humanize(data.tournament.config.format) }}</dd></div>
          <div><dt>Time control</dt><dd>{{ formatTimeControl(data.tournament.config.time_control) }}</dd></div>
          <div><dt>Participants</dt><dd>{{ data.tournament.config.participants.length }}</dd></div>
          <div><dt>Concurrency</dt><dd>{{ data.tournament.config.concurrency }}</dd></div>
          <div><dt>Engine threads</dt><dd>{{ data.tournament.config.engine_threads }}</dd></div>
          <div><dt>Hash per engine</dt><dd>{{ data.tournament.config.engine_hash_mb }} MB</dd></div>
          <div><dt>Ratings</dt><dd>{{ data.tournament.config.rated ? 'Rated' : 'Unrated' }}</dd></div>
        </dl>
            <p class="estimate-basis">{{ estimateBasis }}</p>
          </div>
      <section v-if="showCommit" class="commit-picker">
        <div><h2>Commit results to rating lists</h2><p>Select one or more independent lists.</p></div>
        <label v-for="ratingList in data.rating_lists" :key="ratingList.id"><input v-model="selectedLists" type="checkbox" :value="ratingList.id" :disabled="data.commits.some((item) => item.rating_list_id === ratingList.id && ['pending','claimed','applied'].includes(item.status))"><span>{{ ratingList.name }}</span></label>
        <button class="button button--primary button--small" :disabled="pending === 'commit' || !selectedLists.length" @click="commitRatings">{{ pending === 'commit' ? 'Requesting…' : 'Commit selected lists' }}</button>
      </section>

      <section v-for="item in data.commits" :key="item.rating_list_id" class="commit-panel">
        <div><h2>{{ data.rating_lists.find((list) => list.id === item.rating_list_id)?.name ?? 'Rating list' }}</h2><p>Requested {{ formatDate(item.requested_at) }}<template v-if="item.applied_at"> · Applied {{ formatDate(item.applied_at) }}</template></p></div>
        <StatusBadge :status="item.status" />
        <p v-if="item.error" class="commit-panel__error" role="alert">{{ item.error }}</p>
      </section>

        </section>
        <section id="tournament-participants" v-show="activePanel === 'participants'" class="participants-panel" role="tabpanel" aria-labelledby="participants-tab">
          <LiveParticipantManager :roster="data.roster" :config="data.tournament.config" :tournament-status="data.tournament.status" :engine-labels="engineLabels" :pending="pending" @add="addParticipant" @remove="removeParticipant" />
        </section>
      </section>
    </template>
    <InlineFeedback v-else :message="error" />
  </div>
</template>

<style scoped>
.tournament-page { display: grid; gap: .75rem; min-width: 0; }
.detail-loading { color: var(--color-text-muted); min-height: 18rem; padding: 2rem; }
.tournament-workspace { min-width: 0; padding: 0; }
.tournament-heading { padding: 1rem 1.15rem .8rem; }
.tournament-heading__navigation, .tournament-heading__navigation > div { align-items: center; display: flex; gap: 1.1rem; }
.tournament-heading__navigation { justify-content: space-between; margin-bottom: .8rem; }
.tournament-heading__navigation a { color: var(--color-text-muted); font-size: .72rem; font-weight: 600; }
.tournament-heading__navigation a:hover { color: var(--color-accent); }
.tournament-heading__title { align-items: center; display: flex; flex-wrap: wrap; gap: .5rem .75rem; min-width: 0; }
.tournament-heading h1 { font-size: clamp(1.15rem, 2vw, 1.65rem); letter-spacing: -.025em; line-height: 1.3; margin: 0; min-width: 0; overflow-wrap: anywhere; }
.rename-trigger { background: transparent; border: 0; color: var(--color-text-muted); cursor: pointer; font-size: .68rem; padding: .2rem 0; }
.rename-trigger:hover { color: var(--color-accent); text-decoration: underline; }
.tournament-heading__meta { align-items: center; color: var(--color-text-muted); display: flex; flex-wrap: wrap; font-size: .7rem; gap: .3rem .6rem; margin-top: .45rem; }
.tournament-heading__meta > span + span::before { content: '·'; margin-right: .6rem; opacity: .6; }
.tournament-heading__meta .created-at { margin-left: auto; }
.tournament-heading__meta .created-at::before { content: none; }
.rename-form { align-items: center; display: flex; flex: 1; flex-wrap: wrap; gap: .4rem; min-width: 0; }
.rename-form .input { flex: 1 1 15rem; min-width: 0; }
.tournament-overview { align-items: start; display: grid; gap: 1.5rem; grid-template-columns: minmax(12rem, 1.3fr) repeat(2, minmax(10rem, 1fr)); padding: .5rem 1.15rem .9rem; }
.tournament-progress > div:first-child { align-items: baseline; display: flex; gap: .6rem; justify-content: space-between; }
.tournament-progress strong { font-size: .86rem; font-variant-numeric: tabular-nums; }
.tournament-progress strong span, .tournament-progress > div > span { color: var(--color-text-muted); font-size: .7rem; font-weight: 500; }
.progress-track { background: var(--color-surface-subtle); border-radius: 999px; height: .3rem; margin: .5rem 0 .35rem; overflow: hidden; }
.progress-track > span { background: var(--color-accent); border-radius: inherit; display: block; height: 100%; transition: width .25s ease; }
.tournament-progress small, .tournament-timing small { color: var(--color-text-muted); font-size: .66rem; }
.tournament-timing { display: grid; gap: .2rem; }
.tournament-timing > span { color: var(--color-text-muted); font-size: .64rem; }
.tournament-timing strong { font-size: .76rem; font-weight: 650; }
.tournament-timing small { color: var(--color-accent); }
.tournament-toolbar { align-items: center; border-top: 1px solid var(--color-border); display: flex; flex-wrap: wrap; gap: .65rem 1rem; justify-content: space-between; padding: .65rem 1.15rem; }
.tournament-toolbar__actions { display: flex; flex-wrap: wrap; gap: .4rem; }
.tournament-toolbar .button { font-size: .72rem; min-height: 1.95rem; padding: .35rem .65rem; }
.concurrency-form { align-items: center; display: flex; gap: .5rem; }
.concurrency-form label { color: var(--color-text-muted); font-size: .7rem; }
.concurrency-form .input { font-size: .75rem; min-height: 1.95rem; padding: .3rem .5rem; width: 4.5rem; }
.inline-schedule { border: 0; border-radius: 0; border-top: 1px solid var(--color-border); box-shadow: none; }
.viewer-strip { align-items: baseline; border-top: 1px solid var(--color-border); display: flex; flex-wrap: wrap; gap: .4rem .75rem; padding: .55rem 1.15rem; }
.viewer-strip__label { align-items: center; color: var(--color-text-muted); display: inline-flex; flex: 0 0 auto; font-size: .66rem; gap: .35rem; }
.viewer-strip__label strong { color: var(--color-text); font-variant-numeric: tabular-nums; }
.viewer-list { display: flex; flex: 1 1 10rem; flex-wrap: wrap; gap: .3rem; list-style: none; margin: 0; max-height: 5rem; overflow-y: auto; padding: 0; }
.viewer-list li { align-items: baseline; background: var(--color-surface-subtle); border: 1px solid var(--color-border); border-radius: .3rem; display: inline-flex; font-size: .67rem; gap: .3rem; max-width: 100%; overflow-wrap: anywhere; padding: .18rem .4rem; }
.viewer-list small { color: var(--color-text-muted); font-size: .6rem; white-space: nowrap; }
.viewer-strip__empty, .viewer-strip__error { color: var(--color-text-muted); font-size: .66rem; }
.viewer-strip__error { flex-basis: 100%; }
.workspace-tabs { border-bottom: 1px solid var(--color-border); border-top: 1px solid var(--color-border); display: flex; gap: .3rem; padding: 0 .9rem; }
.workspace-tabs button { align-items: center; background: transparent; border: 0; border-bottom: 2px solid transparent; color: var(--color-text-muted); cursor: pointer; display: inline-flex; font-size: .75rem; font-weight: 650; gap: .4rem; margin-bottom: -1px; padding: .75rem .7rem; white-space: nowrap; }
.workspace-tabs button[aria-selected='true'] { border-bottom-color: var(--color-accent); color: var(--color-accent); }
.workspace-tabs button:hover { color: var(--color-text); }
.workspace-tabs button span { color: var(--color-text-muted); font-size: .63rem; font-variant-numeric: tabular-nums; font-weight: 500; }
.games-panel { min-width: 0; }
.games-panel h2 { font-size: .82rem; margin: 0; }
.games-panel__heading { align-items: center; display: flex; flex-wrap: wrap; gap: .65rem; justify-content: space-between; padding: .8rem 1.15rem; }
.games-panel__heading p { color: var(--color-text-muted); font-size: .68rem; margin: .2rem 0 0; }
.configuration-panel, .participants-panel { min-width: 0; }
.participants-panel :deep(.live-roster) { border: 0; border-radius: 0; box-shadow: none; }
.participants-panel :deep(.participant-roster) { max-height: 35rem; }
.definition-list { display: grid; gap: 0 1.25rem; grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr)); margin: 0; padding: .35rem 1.15rem .8rem; }
.definition-list > div { border-bottom: 1px solid var(--color-border); padding: .65rem 0; }
.definition-list dt { color: var(--color-text-muted); font-size: .65rem; }
.definition-list dd { font-size: .76rem; font-weight: 650; margin: .2rem 0 0; overflow-wrap: anywhere; }
.estimate-basis { color: var(--color-text-muted); font-size: .68rem; margin: 0; padding: 0 1.15rem .9rem; }
.commit-picker { align-items: center; border-top: 1px solid var(--color-border); display: flex; flex-wrap: wrap; gap: .65rem 1rem; padding: 1rem 1.15rem; }
.commit-picker > div { flex-basis: 100%; }
.commit-picker h2, .commit-panel h2 { font-size: .82rem; margin: 0; }
.commit-picker p, .commit-panel p { color: var(--color-text-muted); font-size: .68rem; margin: .2rem 0 0; }
.commit-picker label { align-items: center; display: flex; font-size: .75rem; gap: .4rem; }
.commit-panel { align-items: center; border-top: 1px solid var(--color-border); display: grid; gap: .65rem; grid-template-columns: minmax(0, 1fr) auto; padding: .75rem 1.15rem; }
.commit-panel .commit-panel__error { color: var(--color-danger); grid-column: 1 / -1; }
.result-filter { position: relative; }
.result-filter > summary { cursor: pointer; list-style: none; }
.result-filter > summary::-webkit-details-marker { display: none; }
.result-filter__menu { background: var(--color-surface, #fff); border: 1px solid var(--color-border, #d9e0ea); border-radius: .5rem; box-shadow: 0 .75rem 2rem rgb(15 23 42 / 18%); display: grid; gap: .75rem; grid-template-columns: repeat(2, minmax(12rem, 1fr)); padding: .8rem; position: absolute; right: 0; top: calc(100% + .4rem); width: min(31rem, calc(100vw - 2rem)); z-index: 10; }
.result-filter__topline { align-items: center; display: flex; grid-column: 1 / -1; justify-content: space-between; }
.result-filter__topline > strong, .result-filter legend { font-size: .7rem; }
.result-filter fieldset { border: 0; display: grid; gap: .45rem; margin: 0; min-width: 0; padding: 0; }
.result-filter legend { color: var(--color-text-muted, #64748b); font-weight: 700; margin-bottom: .45rem; padding: 0; text-transform: uppercase; }
.result-filter label { align-items: center; display: flex; font-size: .74rem; gap: .45rem; }
.result-filter input { accent-color: var(--color-accent, #2563eb); }
.game-table-wrap { overflow-x: auto; }
.data-table { border-collapse: collapse; min-width: 48rem; width: 100%; }
.data-table th { color: var(--color-text-muted, #64748b); font-size: .65rem; letter-spacing: .04em; padding: .65rem .8rem; text-align: left; text-transform: uppercase; }
.data-table td { border-top: 1px solid var(--color-border, #d9e0ea); font-size: .76rem; padding: .7rem .8rem; }
.game-actions { display: flex; gap: .4rem; }
.pagination { align-items: center; border-top: 1px solid var(--color-border, #d9e0ea); display: flex; gap: .75rem; justify-content: flex-end; padding: .75rem 1rem; }
.pagination span { color: var(--color-text-muted, #64748b); font-size: .72rem; }

.data-table { min-width: 45rem; }
.data-table th { font-size: .6rem; padding: .55rem .85rem; }
.data-table td { font-size: .72rem; padding: .5rem .85rem; }
.game-actions .button { font-size: .66rem; min-height: 1.75rem; padding: .3rem .5rem; }
@media (max-width: 65rem) {
  .tournament-heading__meta .created-at { margin-left: 0; }
  .tournament-heading__meta .created-at::before { content: '·'; }
  .tournament-overview { gap: 1rem; grid-template-columns: minmax(10rem, 1.1fr) repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 42rem) {
  .tournament-heading { padding: .85rem; }
  .tournament-heading__navigation, .tournament-heading__navigation > div { gap: .65rem; }
  .tournament-heading__navigation a { font-size: .65rem; }
  .tournament-heading__title { gap: .4rem .6rem; }
  .tournament-heading h1 { flex-basis: 100%; font-size: 1.15rem; }
  .tournament-heading__meta { font-size: .65rem; }
  .tournament-heading__meta .created-at { flex-basis: 100%; }
  .tournament-heading__meta .created-at::before { content: none; }
  .tournament-overview { gap: .85rem; grid-template-columns: repeat(2, minmax(0, 1fr)); padding: .2rem .85rem .8rem; }
  .tournament-progress { grid-column: 1 / -1; }
  .tournament-timing strong { font-size: .7rem; }
  .tournament-toolbar { align-items: stretch; gap: .65rem; padding: .65rem .85rem; }
  .tournament-toolbar__actions { flex: 1 1 100%; }
  .concurrency-form { justify-content: flex-end; margin-left: auto; }
  .viewer-strip { padding: .55rem .85rem; }
  .workspace-tabs { gap: 0; padding: 0 .35rem; }
  .workspace-tabs button { font-size: .69rem; gap: .3rem; padding: .7rem .5rem; }
  .workspace-tabs button span { font-size: .6rem; }
  .games-panel__heading { padding: .75rem .85rem; }
  .result-filter__menu { grid-template-columns: 1fr; }
  .result-filter__topline { grid-column: auto; }
  .pagination { flex-wrap: wrap; gap: .5rem; justify-content: center; }
}
</style>
