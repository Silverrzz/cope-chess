<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import LiveParticipantManager from '@/components/admin/LiveParticipantManager.vue'
import StatusBadge from '@/components/admin/StatusBadge.vue'
import TournamentConfigForm from '@/components/admin/TournamentConfigForm.vue'
import { errorText, formatDate, formatTimeControl, humanize } from '@/components/admin/format'
import { buildTournamentDetailsMarkdown } from '@/components/public/tournamentSummary'
import AppIcon from '@/components/ui/AppIcon.vue'
import type { Engine, FormSeed, Game, Tournament, TournamentConfig } from '@/components/admin/types'

interface Commit { rating_list_id: number; status: string; requested_at: string; applied_at?: string | null; error?: string | null }
interface RatingList { id: number; name: string }
interface GamePagination { page: number; page_size: number; total: number; pages: number }
interface GameSummary { total: number; pairs: number; pending: number; assigned: number; live: number; finished: number; abandoned: number }
interface LiveParticipantSummary { engine_id: number; total: number; pending: number; assigned: number; live: number; finished: number; abandoned: number }
interface LiveTournamentRoster { editable: boolean; reason: string; available_engines: Engine[]; participants: LiveParticipantSummary[]; hero_engine_id: number | null }
interface ResultFilterOption { value: string; label: string }
interface Response {
  tournament: Tournament
  games: Game[]
  engines: Engine[] | Record<string, Engine | string>
  settings?: Array<[string, string] | { label: string; value: string }> | Record<string, string>
  commits: Commit[]
  rating_lists: RatingList[]
  actions: Record<string, string>
  game_pagination: GamePagination
  game_summary: GameSummary
  roster: LiveTournamentRoster
  form?: FormSeed
}

const resultFilterGroups: Array<{ label: string; options: ResultFilterOption[] }> = [
  {
    label: 'Wins',
    options: [
      { value: 'win_checkmate', label: 'Checkmate' },
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
const showCommit = ref(false)
const selectedLists = ref<number[]>([])
const selectedResultTypes = ref<string[]>([])
const gamePage = ref(1)
let loadSequence = 0
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
const settingsRows = computed(() => {
  if (!data.value?.settings) return []
  return Array.isArray(data.value.settings)
    ? data.value.settings.map((row) => Array.isArray(row) ? row : [row.label, row.value] as [string, string])
    : Object.entries(data.value.settings)
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
    data.value = response
    concurrency.value = response.tournament.config.concurrency
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

onMounted(load)
</script>

<template>
  <div class="admin-page page-stack">
    <div v-if="loading" class="panel detail-loading" role="status">Loading tournament…</div>
    <template v-else-if="data">
      <AdminPageHeader :title="data.tournament.name" :description="`Created ${formatDate(data.tournament.created_at)}`">
        <template #actions><RouterLink class="button button--ghost" to="/admin/tournaments">All tournaments</RouterLink><a class="button button--secondary" :href="`/api/admin/tournaments/${id}/pgn`">Download tournament PGN</a><RouterLink class="button button--secondary" :to="`/tournaments/${id}`">Public page</RouterLink></template>
      </AdminPageHeader>
      <InlineFeedback :message="error" />

      <section class="panel control-bar">
        <div class="control-bar__status"><span>Current state</span><StatusBadge :status="data.tournament.status" /></div>
        <div class="control-bar__actions">
          <button class="button button--secondary" type="button" :disabled="copyingDetails" @click="copyDetails"><AppIcon name="copy" :size="16" />{{ copyingDetails ? 'Copying…' : detailsCopied ? 'Copied' : 'Copy Details' }}</button>
          <button v-for="(_, action) in data.actions" :key="action" class="button" :class="action === 'abort' ? 'button--danger' : 'button--primary'" type="button" :disabled="!!pending" @click="changeStatus(String(action))">{{ pending === action ? 'Working…' : humanize(String(action)) }}</button>
          <button v-if="['finished', 'aborted'].includes(data.tournament.status) && hasCommittableGames && data.tournament.config.rated" class="button button--primary" type="button" :disabled="!!pending || !data.rating_lists.length" @click="showCommit = !showCommit">Commit ratings</button>
          <button v-if="!['scheduled', 'running'].includes(data.tournament.status) && !data.commits.some((item) => ['pending','claimed','applied'].includes(item.status))" class="button button--danger" type="button" :disabled="!!pending" @click="remove">{{ pending === 'delete' ? 'Deleting…' : 'Delete' }}</button>
        </div>
      </section>

      <section v-if="['running', 'paused'].includes(data.tournament.status)" class="panel concurrency-panel">
        <div>
          <h2>Game concurrency</h2>
          <p>Set the maximum number of games this tournament may run at once. {{ activeGames }} currently active.</p>
        </div>
        <form class="concurrency-form" @submit.prevent="saveConcurrency">
          <label><span>Maximum concurrent games</span><input v-model.number="concurrency" class="input" type="number" min="1" step="1" required></label>
          <button class="button button--primary" type="submit" :disabled="!!pending || concurrency === data.tournament.config.concurrency">{{ pending === 'concurrency' ? 'Saving…' : 'Update concurrency' }}</button>
        </form>
      </section>

      <LiveParticipantManager
        v-if="['running', 'paused'].includes(data.tournament.status)"
        :roster="data.roster"
        :config="data.tournament.config"
        :tournament-status="data.tournament.status"
        :engine-labels="engineLabels"
        :pending="pending"
        @add="addParticipant"
        @remove="removeParticipant"
      />

      <section v-if="showCommit" class="panel commit-picker">
        <div><h2>Commit results to rating lists</h2><p>Select one or more independent lists.</p></div>
        <label v-for="ratingList in data.rating_lists" :key="ratingList.id"><input v-model="selectedLists" type="checkbox" :value="ratingList.id" :disabled="data.commits.some((item) => item.rating_list_id === ratingList.id && ['pending','claimed','applied'].includes(item.status))"><span>{{ ratingList.name }}</span></label>
        <button class="button button--primary button--small" :disabled="pending === 'commit' || !selectedLists.length" @click="commitRatings">{{ pending === 'commit' ? 'Requesting…' : 'Commit selected lists' }}</button>
      </section>

      <section v-for="item in data.commits" :key="item.rating_list_id" class="panel commit-panel">
        <div><h2>{{ data.rating_lists.find((list) => list.id === item.rating_list_id)?.name ?? 'Rating list' }}</h2><p>Requested {{ formatDate(item.requested_at) }}<template v-if="item.applied_at"> · Applied {{ formatDate(item.applied_at) }}</template></p></div>
        <StatusBadge :status="item.status" />
        <p v-if="item.error" class="commit-panel__error" role="alert">{{ item.error }}</p>
      </section>

      <TournamentConfigForm v-if="data.tournament.status === 'draft' && data.form" :seed="data.form" :pending="pending === 'save'" submit-label="Save draft" @submit="saveDraft" @cancel="router.push('/admin/tournaments')" />

      <section v-else class="panel settings-panel">
        <div class="settings-panel__heading"><div><h2>Settings</h2></div></div>
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
      </section>

      <section class="panel games-panel">
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
    </template>
    <InlineFeedback v-else :message="error" />
  </div>
</template>

<style scoped>
.detail-loading { color: var(--color-text-muted, #64748b); min-height: 18rem; padding: 2rem; }
.control-bar { align-items: center; display: flex; gap: 1rem; justify-content: space-between; padding: .8rem 1rem; }
.control-bar__status { align-items: center; display: flex; gap: .65rem; }
.control-bar__status > span { color: var(--color-text-muted, #64748b); font-size: .72rem; font-weight: 650; }
.control-bar__actions { display: flex; flex-wrap: wrap; gap: .5rem; justify-content: flex-end; }
.control-bar__actions .button { align-items: center; display: inline-flex; gap: .4rem; }
.concurrency-panel { align-items: end; display: flex; gap: 1.5rem; justify-content: space-between; padding: 1rem; }
.concurrency-panel h2 { font-size: .92rem; margin: 0; }
.concurrency-panel p { color: var(--color-text-muted, #64748b); font-size: .73rem; margin: .2rem 0 0; }
.commit-panel { align-items: center; display: grid; gap: 1rem; grid-template-columns: minmax(0, 1fr) auto; padding: 1rem; }
.commit-picker { display: flex; flex-wrap: wrap; gap: .75rem 1rem; padding: 1rem; }
.commit-picker > div { flex: 1 0 100%; }
.commit-picker h2 { font-size: .92rem; margin: 0; }
.commit-picker p { color: var(--color-text-muted); font-size: .73rem; margin: .2rem 0 0; }
.commit-picker label { align-items: center; display: flex; gap: .4rem; font-size: .78rem; }
.commit-panel h2, .settings-panel h2, .games-panel h2 { font-size: .92rem; margin: 0; }
.commit-panel p, .settings-panel__heading p, .games-panel__heading p { color: var(--color-text-muted, #64748b); font-size: .73rem; margin: .2rem 0 0; }
.commit-panel__error { background: color-mix(in srgb, var(--color-danger, #b42318) 8%, transparent); border-radius: .4rem; color: var(--color-danger, #b42318) !important; grid-column: 1 / -1; padding: .65rem; }
.settings-panel { overflow: hidden; padding: 0; }
.games-panel { overflow: visible; padding: 0; }
.settings-panel__heading, .games-panel__heading { align-items: center; border-bottom: 1px solid var(--color-border, #d9e0ea); display: flex; flex-wrap: wrap; gap: .75rem; justify-content: space-between; padding: .85rem 1rem; }
.settings-panel__heading > span { background: var(--color-surface-subtle, #f1f5f9); border-radius: 999px; color: var(--color-text-muted, #64748b); font-size: .68rem; font-weight: 650; padding: .3rem .5rem; }
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
.concurrency-form { align-items: end; display: flex; gap: .5rem; }
.concurrency-form label { display: grid; gap: .25rem; }
.concurrency-form label span { color: var(--color-text-muted, #64748b); font-size: .68rem; }
.concurrency-form .input { min-width: 0; width: 9rem; }
.definition-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); margin: 0; padding: .35rem 1rem 1rem; }
.definition-list div { border-bottom: 1px solid var(--color-border, #d9e0ea); padding: .7rem 0; }
.definition-list dt { color: var(--color-text-muted, #64748b); font-size: .68rem; }
.definition-list dd { font-size: .78rem; font-weight: 650; margin: .2rem 0 0; }
.game-table-wrap { overflow-x: auto; }
.data-table { border-collapse: collapse; min-width: 48rem; width: 100%; }
.data-table th { color: var(--color-text-muted, #64748b); font-size: .65rem; letter-spacing: .04em; padding: .65rem .8rem; text-align: left; text-transform: uppercase; }
.data-table td { border-top: 1px solid var(--color-border, #d9e0ea); font-size: .76rem; padding: .7rem .8rem; }
.game-actions { display: flex; gap: .4rem; }
.pagination { align-items: center; border-top: 1px solid var(--color-border, #d9e0ea); display: flex; gap: .75rem; justify-content: flex-end; padding: .75rem 1rem; }
.pagination span { color: var(--color-text-muted, #64748b); font-size: .72rem; }
@media (max-width: 42rem) { .control-bar, .concurrency-panel { align-items: stretch; flex-direction: column; } .control-bar__actions { justify-content: flex-start; } .concurrency-form { align-items: end; } .concurrency-form label { flex: 1; } .concurrency-form .input { width: 100%; } .result-filter__menu { grid-template-columns: 1fr; } .result-filter__topline { grid-column: auto; } }
</style>
