<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '@/api/client'
import ContentState from '@/components/public/ContentState.vue'
import EngineGameFiltersModal from '@/components/public/EngineGameFiltersModal.vue'
import GameTable from '@/components/public/GameTable.vue'
import { errorMessage, formatDate } from '@/components/public/format'
import StatusPill from '@/components/public/StatusPill.vue'
import type { EngineGameFilterOptions, EngineGameFilters, EngineRecord, GameRecord } from '@/components/public/types'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import OptionPicker from '@/components/ui/OptionPicker.vue'

interface EngineRecordSummary {
  wins: number
  draws: number
  losses: number
  games: number
}

interface EngineFamily {
  id: number
  name: string
  author: string
  active: boolean
}

interface EngineOption {
  id: number
  engine_id: number
  name: string
  author?: string
  version: string
  source_kind?: string
  distribution?: 'managed' | 'worker_local'
  active?: boolean
}

interface RatingHistoryPoint {
  elo: number
  change: number
  at: string
}

interface EngineRating {
  rating_list: { id: number; name: string }
  elo: number
  rank: number
  field_size: number
  games_played: number
  error_margin: number | null
  updated_at: string | null
  peak_elo: number
  history: RatingHistoryPoint[]
}

interface EngineResponse {
  engine: EngineRecord
  family: EngineFamily
  versions: EngineRecord[]
  games: GameRecord[]
  engines: Record<string, string>
  engine_options: EngineOption[]
  record: EngineRecordSummary
  filter_options: EngineGameFilterOptions
  ratings: EngineRating[]
  badges: Array<{ id: number; name: string; emoji: string; description: string }>
}

type FilterKey = keyof EngineGameFilters

const route = useRoute()
const router = useRouter()
const data = ref<EngineResponse | null>(null)
const loading = ref(true)
const gamesLoading = ref(false)
const loadError = ref('')
const gameFilters = ref<EngineGameFilters>(emptyFilters())
const filterModalOpen = ref(false)
const selectedRatingListId = ref('')
let controller: AbortController | null = null

const engineId = computed(() => String(route.params.id || ''))
const scorePercent = computed(() => {
  if (!data.value?.record.games) return 0
  return Math.round(((data.value.record.wins + data.value.record.draws * 0.5) / data.value.record.games) * 100)
})
const uciOptions = computed(() => Object.entries(data.value?.engine.uci_options || {}))
const activeFilterCount = computed(() => Object.values(gameFilters.value).filter(Boolean).length)
const versionOptions = computed(() => (data.value?.versions ?? []).map((version, index) => ({
  value: String(version.id),
  label: version.version || `Version ${version.id}`,
  description: `${version.distribution === 'worker_local' ? 'Worker-local' : version.source_kind === 'commit' ? 'Commit' : 'Release'}${index === 0 ? ' · latest' : ''}`,
})))
const ratingListOptions = computed(() => (data.value?.ratings ?? []).map((rating) => ({
  value: String(rating.rating_list.id),
  label: rating.rating_list.name,
  description: `#${rating.rank} of ${rating.field_size}`,
})))
const selectedRating = computed(() => data.value?.ratings.find((rating) => String(rating.rating_list.id) === selectedRatingListId.value) ?? data.value?.ratings[0] ?? null)
const ratingDelta = computed(() => {
  const history = selectedRating.value?.history ?? []
  if (history.length < 2) return 0
  return history[history.length - 1]!.elo - history[0]!.elo
})
const ratingChart = computed(() => {
  const history = selectedRating.value?.history ?? []
  if (!history.length) return null
  const values = history.map((point) => point.elo)
  const low = Math.floor((Math.min(...values) - 15) / 25) * 25
  const highCandidate = Math.ceil((Math.max(...values) + 15) / 25) * 25
  const high = highCandidate === low ? low + 50 : highCandidate
  const width = 700
  const height = 220
  const left = 46
  const right = 16
  const top = 15
  const bottom = 30
  const plotWidth = width - left - right
  const plotHeight = height - top - bottom
  const points = history.map((point, index) => ({
    ...point,
    x: left + (history.length === 1 ? plotWidth / 2 : (index / (history.length - 1)) * plotWidth),
    y: top + ((high - point.elo) / (high - low)) * plotHeight,
  }))
  const ticks = Array.from({ length: 5 }, (_, index) => ({
    value: Math.round(high - ((high - low) * index) / 4),
    y: top + (plotHeight * index) / 4,
  }))
  return {
    width,
    height,
    points,
    ticks,
    polyline: points.map((point) => `${point.x},${point.y}`).join(' '),
    area: `${left},${top + plotHeight} ${points.map((point) => `${point.x},${point.y}`).join(' ')} ${left + plotWidth},${top + plotHeight}`,
    start: formatDate(history[0]?.at),
    end: formatDate(history[history.length - 1]?.at),
  }
})
const recentForm = computed(() => (data.value?.games ?? []).slice(0, 10).map((game) => resultForEngine(game)))
const streakLabel = computed(() => {
  const first = recentForm.value[0]
  if (!first) return 'No recent games'
  const count = recentForm.value.findIndex((result) => result !== first)
  const total = count === -1 ? recentForm.value.length : count
  const labels = { W: ['win', 'wins'], D: ['draw', 'draws'], L: ['loss', 'losses'] }
  return `${total} ${labels[first][total === 1 ? 0 : 1]}`
})
const mostPlayedOpponent = computed(() => {
  if (!data.value) return null
  const counts = new Map<string, number>()
  for (const game of data.value.games) {
    const opponent = String(String(game.white_engine_id) === engineId.value ? game.black_engine_id : game.white_engine_id)
    counts.set(opponent, (counts.get(opponent) ?? 0) + 1)
  }
  const top = [...counts.entries()].sort((left, right) => right[1] - left[1])[0]
  return top ? { name: data.value.engines[top[0]] || `Engine ${top[0]}`, games: top[1] } : null
})
const pgnDownloadUrl = computed(() => {
  const parameters = new URLSearchParams({ engine_id: engineId.value })
  if (gameFilters.value.result) parameters.set('result', gameFilters.value.result)
  if (gameFilters.value.ratingListId) parameters.set('rating_list_id', gameFilters.value.ratingListId)
  if (gameFilters.value.opponentId) parameters.set('opponent_id', gameFilters.value.opponentId)
  if (gameFilters.value.side) parameters.set('side', gameFilters.value.side)
  return `/api/pgn?${parameters.toString()}`
})
const pgnDownloadLabel = computed(() => activeFilterCount.value ? 'Download filtered PGN' : 'Download all PGNs')
const activeFilterChips = computed<Array<{ key: FilterKey; label: string }>>(() => {
  const chips: Array<{ key: FilterKey; label: string }> = []
  const filters = gameFilters.value
  if (filters.result) {
    const labels = { win: 'Wins', draw: 'Draws', loss: 'Losses' }
    chips.push({ key: 'result', label: `Result: ${labels[filters.result]}` })
  }
  if (filters.ratingListId) {
    const label = data.value?.filter_options.rating_lists.find((item) => item.value === filters.ratingListId)?.label
    chips.push({ key: 'ratingListId', label: `Rating list: ${label || 'Selected list'}` })
  }
  if (filters.opponentId) chips.push({ key: 'opponentId', label: `Opponent: ${data.value?.engines[filters.opponentId] || `Engine ${filters.opponentId}`}` })
  if (filters.side) chips.push({ key: 'side', label: `Playing as ${filters.side === 'white' ? 'White' : 'Black'}` })
  return chips
})
const gamesDescription = computed(() => {
  if (!data.value) return ''
  if (!activeFilterCount.value) return `${data.value.record.games} completed game${data.value.record.games === 1 ? '' : 's'} in this version’s record.`
  const shown = data.value.games.length
  return `${shown === 50 ? 'Showing the 50 most recent matches' : `${shown} matching game${shown === 1 ? '' : 's'}`} from ${data.value.record.games} total.`
})

watch(engineId, () => {
  data.value = null
  gameFilters.value = emptyFilters()
  filterModalOpen.value = false
  selectedRatingListId.value = ''
  void load()
}, { immediate: true })

watch(data, (response) => {
  if (!response?.ratings.length) selectedRatingListId.value = ''
  else if (!response.ratings.some((rating) => String(rating.rating_list.id) === selectedRatingListId.value)) selectedRatingListId.value = String(response.ratings[0]!.rating_list.id)
})

onBeforeUnmount(() => controller?.abort())

function emptyFilters(): EngineGameFilters {
  return { result: '', ratingListId: '', opponentId: '', side: '' }
}

function resultForEngine(game: GameRecord): 'W' | 'D' | 'L' {
  if (game.result === '1/2-1/2') return 'D'
  const won = (game.result === '1-0' && String(game.white_engine_id) === engineId.value) || (game.result === '0-1' && String(game.black_engine_id) === engineId.value)
  return won ? 'W' : 'L'
}

function selectVersion(value: string | number): void {
  const target = String(value)
  if (target !== engineId.value) void router.push(`/engines/${target}`)
}

function applyFilters(filters: EngineGameFilters): void {
  filterModalOpen.value = false
  if (JSON.stringify(filters) === JSON.stringify(gameFilters.value)) return
  gameFilters.value = filters
  void load()
}

function clearFilters(): void {
  if (!activeFilterCount.value) return
  gameFilters.value = emptyFilters()
  void load()
}

function removeFilter(key: FilterKey): void {
  gameFilters.value = { ...gameFilters.value, [key]: '' }
  void load()
}

async function load(): Promise<void> {
  controller?.abort()
  const requestController = new AbortController()
  controller = requestController
  loading.value = !data.value
  gamesLoading.value = true
  loadError.value = ''
  try {
    const response = await api.get<EngineResponse>(`/api/engines/${encodeURIComponent(engineId.value)}`, {
      query: {
        result: gameFilters.value.result || undefined,
        rating_list_id: gameFilters.value.ratingListId || undefined,
        opponent_id: gameFilters.value.opponentId || undefined,
        side: gameFilters.value.side || undefined,
      },
      signal: requestController.signal,
    })
    if (controller === requestController) data.value = response
  } catch (error) {
    if (controller === requestController && (error as { name?: string })?.name !== 'AbortError') loadError.value = errorMessage(error, 'This engine could not be loaded.')
  } finally {
    if (controller === requestController) {
      loading.value = false
      gamesLoading.value = false
    }
  }
}
</script>

<template>
  <div class="page-container engine-page">
    <ContentState v-if="loading" kind="loading" title="Loading engine" />
    <ContentState v-else-if="loadError" kind="error" :message="loadError" action-label="Try again" @action="load" />

    <template v-else-if="data">
      <header class="engine-heading">
        <div class="engine-heading__identity">
          <RouterLink class="back-link" to="/engines"><AppIcon name="arrow-left" :size="14" /> All engines</RouterLink>
          <div class="engine-title-row"><h1>{{ data.family?.name || data.engine.name }}</h1><StatusPill :status="data.family?.active && data.engine.active ? 'ready' : 'unavailable'" /></div>
          <p v-if="data.family?.author || data.engine.author">By {{ data.family?.author || data.engine.author }}</p>
        </div>
        <label class="version-picker">
          <span>Engine version</span>
          <OptionPicker :model-value="engineId" :options="versionOptions" label="Engine version" icon="tag" @update:model-value="selectVersion" />
        </label>
      </header>

      <section class="overview-grid" aria-label="Performance overview">
        <article class="overview-card overview-card--rating">
          <span class="overview-card__icon"><AppIcon name="trophy" :size="18" /></span>
          <div><span>Current rating</span><strong>{{ selectedRating ? Math.round(selectedRating.elo).toLocaleString() : 'Unrated' }}</strong><small>{{ selectedRating?.rating_list.name || 'No published rating yet' }}</small></div>
        </article>
        <article class="overview-card">
          <span class="overview-card__icon"><AppIcon name="gauge" :size="18" /></span>
          <div><span>Score</span><strong>{{ scorePercent }}%</strong><small>{{ data.record.games.toLocaleString() }} completed games</small></div>
        </article>
        <article class="overview-card">
          <span class="overview-card__icon"><AppIcon name="activity" :size="18" /></span>
          <div><span>Recent streak</span><strong>{{ streakLabel }}</strong><small>Across the latest results</small></div>
        </article>
        <article class="overview-card">
          <span class="overview-card__icon"><AppIcon name="engine" :size="18" /></span>
          <div><span>Family versions</span><strong>{{ data.versions.length }}</strong><small>Latest is {{ data.versions[0]?.version }}</small></div>
        </article>
      </section>

      <div class="insight-layout">
        <section class="panel rating-panel" aria-labelledby="rating-history-title">
          <header>
            <div><p class="section-kicker">Performance</p><h2 id="rating-history-title">Elo over time</h2></div>
            <OptionPicker v-if="data.ratings.length > 1" v-model="selectedRatingListId" class="rating-list-picker" :options="ratingListOptions" label="Rating list" />
            <span v-else-if="selectedRating" class="rating-list-label">{{ selectedRating.rating_list.name }}</span>
          </header>
          <div v-if="selectedRating && ratingChart" class="rating-chart">
            <div class="rating-chart__summary">
              <div><strong>{{ Math.round(selectedRating.elo).toLocaleString() }}</strong><span>Current Elo</span></div>
              <div><strong>#{{ selectedRating.rank }}</strong><span>of {{ selectedRating.field_size }}</span></div>
              <div><strong>{{ Math.round(selectedRating.peak_elo).toLocaleString() }}</strong><span>Peak Elo</span></div>
              <div><strong :class="{ positive: ratingDelta > 0, negative: ratingDelta < 0 }">{{ ratingDelta > 0 ? '+' : '' }}{{ Math.round(ratingDelta) }}</strong><span>All-time change</span></div>
            </div>
            <svg class="elo-chart" :viewBox="`0 0 ${ratingChart.width} ${ratingChart.height}`" role="img" :aria-label="`${selectedRating.rating_list.name} Elo history`">
              <g v-for="tick in ratingChart.ticks" :key="tick.value">
                <line x1="46" x2="684" :y1="tick.y" :y2="tick.y" />
                <text x="38" :y="tick.y + 4" text-anchor="end">{{ tick.value }}</text>
              </g>
              <polygon class="elo-chart__area" :points="ratingChart.area" />
              <polyline class="elo-chart__line" :points="ratingChart.polyline" />
              <circle v-for="(point, index) in ratingChart.points" :key="`${point.at}-${index}`" :cx="point.x" :cy="point.y" r="3.5"><title>{{ Math.round(point.elo) }} Elo · {{ formatDate(point.at) }}</title></circle>
              <text x="46" y="213">{{ ratingChart.start }}</text>
              <text x="684" y="213" text-anchor="end">{{ ratingChart.end }}</text>
            </svg>
          </div>
          <ContentState v-else kind="empty" compact title="No rating history yet" message="This version will chart its Elo once rated games are committed." />
        </section>

        <aside class="insight-column">
          <section class="panel form-panel" aria-labelledby="recent-form-title">
            <header><div><p class="section-kicker">Momentum</p><h2 id="recent-form-title">Recent form</h2></div><span>Last {{ recentForm.length }}</span></header>
            <div v-if="recentForm.length" class="form-content">
              <div class="form-dots" aria-label="Recent results"><span v-for="(result, index) in recentForm" :key="index" :data-result="result">{{ result }}</span></div>
              <div class="form-facts">
                <div><span>Current run</span><strong>{{ streakLabel }}</strong></div>
                <div><span>Most faced lately</span><strong>{{ mostPlayedOpponent ? `${mostPlayedOpponent.name} · ${mostPlayedOpponent.games}` : '—' }}</strong></div>
              </div>
            </div>
            <p v-else class="panel-empty">No completed games yet.</p>
          </section>

          <section class="panel badges-panel" aria-labelledby="badges-title">
            <header><div><p class="section-kicker">Milestones</p><h2 id="badges-title">Badges</h2></div><span>{{ data.badges.length }}</span></header>
            <div v-if="data.badges.length" class="badge-list"><article v-for="badge in data.badges" :key="badge.id"><span aria-hidden="true">{{ badge.emoji }}</span><div><strong>{{ badge.name }}</strong><small v-if="badge.description">{{ badge.description }}</small></div></article></div>
            <div v-else class="badges-empty"><span><AppIcon name="trophy" :size="20" /></span><div><strong>Ready for future achievements</strong><p>Milestones and tournament awards will appear here.</p></div></div>
          </section>
        </aside>
      </div>

      <details class="panel technical-panel">
        <summary><span><AppIcon name="info" :size="17" /><strong>Version details</strong><small>{{ data.engine.distribution === 'worker_local' ? 'Worker-local availability' : 'Source and build' }}, and {{ uciOptions.length }} custom UCI option{{ uciOptions.length === 1 ? '' : 's' }}</small></span><AppIcon name="chevron-down" :size="17" /></summary>
        <div class="technical-content">
          <dl>
            <div><dt>Version</dt><dd>{{ data.engine.version || '—' }}</dd></div>
            <div><dt>Source</dt><dd v-if="data.engine.distribution === 'worker_local'">Worker-local private binary</dd><dd v-else>{{ data.engine.source_kind || '—' }} · <code>{{ data.engine.source_ref || '—' }}</code></dd></div>
            <div v-if="data.engine.distribution !== 'worker_local'"><dt>Repository</dt><dd><a v-if="data.engine.repository_url" :href="data.engine.repository_url.replace(/\.git$/, '')" target="_blank" rel="noopener">{{ data.engine.repository_full_name || data.engine.repository_url }}</a><span v-else>—</span></dd></div>
            <div><dt>Added</dt><dd>{{ formatDate(data.engine.created_at) }}</dd></div>
          </dl>
          <div v-if="uciOptions.length" class="uci-options"><span v-for="([name, value]) in uciOptions" :key="name"><strong>{{ name }}</strong><code>{{ String(value) }}</code></span></div>
        </div>
      </details>

      <section class="panel games-panel" aria-labelledby="engine-games-title">
        <header>
          <div><h2 id="engine-games-title">Recent games</h2><p>{{ gamesDescription }}</p></div>
          <div class="games-panel__actions">
            <BaseButton :href="pgnDownloadUrl" size="small" :disabled="!data.record.games || (Boolean(activeFilterCount) && !data.games.length)" download><template #icon><AppIcon name="download" :size="15" /></template>{{ pgnDownloadLabel }}</BaseButton>
            <BaseButton class="filter-trigger" size="small" :disabled="gamesLoading" :aria-expanded="filterModalOpen" aria-haspopup="dialog" @click="filterModalOpen = true"><template #icon><AppIcon name="filter" :size="15" /></template>Filters<template v-if="activeFilterCount" #trailing><span class="filter-trigger__count">{{ activeFilterCount }}</span></template></BaseButton>
          </div>
        </header>

        <div v-if="activeFilterChips.length" class="active-filters" aria-label="Active game filters">
          <span>Filtered by</span>
          <button v-for="chip in activeFilterChips" :key="chip.key" type="button" :aria-label="`Remove ${chip.label} filter`" :disabled="gamesLoading" @click="removeFilter(chip.key)">{{ chip.label }}<AppIcon name="close" :size="12" /></button>
          <button class="active-filters__clear" type="button" :disabled="gamesLoading" @click="clearFilters">Clear all</button>
          <span v-if="gamesLoading" class="active-filters__loading" role="status">Updating…</span>
        </div>

        <div class="games-results" :class="{ 'games-results--loading': gamesLoading }" :aria-busy="gamesLoading">
          <GameTable v-if="data.games.length" :games="data.games" :engines="data.engines" :show-round="false" caption="Recent games for this engine" />
          <ContentState v-else kind="empty" compact :title="activeFilterCount ? 'No games match these filters' : 'No games yet'" />
        </div>
      </section>

      <EngineGameFiltersModal :open="filterModalOpen" :filters="gameFilters" :options="data.filter_options" :engines="data.engines" :engine-options="data.engine_options" :engine-id="engineId" :record="data.record" @close="filterModalOpen = false" @apply="applyFilters" />
    </template>
  </div>
</template>

<style scoped>
.engine-page { display: grid; gap: 1rem; padding-block: clamp(1.2rem, 2.5vw, 2.25rem) 3rem; }
.engine-page h1, .engine-page h2, .engine-page p, .engine-page dl { margin: 0; }
.engine-heading { align-items: end; border-block-end: 1px solid var(--color-border); display: flex; gap: 2rem; justify-content: space-between; padding-block-end: 1.35rem; }
.back-link { align-items: center; color: var(--color-text-muted); display: inline-flex; font-size: .72rem; font-weight: 700; gap: .3rem; margin-block-end: .9rem; text-decoration: none; }
.back-link:hover { color: var(--color-accent); }
.engine-title-row { align-items: center; display: flex; flex-wrap: wrap; gap: .75rem; }
.engine-heading h1 { font-size: clamp(2.25rem, 5vw, 3.7rem); letter-spacing: -.045em; line-height: .95; }
.engine-heading__identity > p { color: var(--color-text-muted); font-size: .86rem; margin-top: .5rem; }
.version-picker { display: grid; flex: 0 1 18rem; gap: .35rem; min-width: min(18rem, 100%); }
.version-picker > span { color: var(--color-text-muted); font-size: .63rem; font-weight: 720; letter-spacing: .05em; text-transform: uppercase; }
.overview-grid { display: grid; gap: .65rem; grid-template-columns: repeat(4, minmax(0, 1fr)); }
.overview-card { align-items: center; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-lg); display: flex; gap: .75rem; min-width: 0; padding: .85rem; }
.overview-card--rating { background: color-mix(in srgb, var(--color-accent-soft) 45%, var(--color-surface)); border-color: color-mix(in srgb, var(--color-accent) 25%, var(--color-border)); }
.overview-card__icon { background: var(--color-surface-sunken); border-radius: var(--radius-md); color: var(--color-accent); display: grid; flex: 0 0 auto; height: 2.4rem; place-items: center; width: 2.4rem; }
.overview-card > div { display: grid; min-width: 0; }
.overview-card div > span { color: var(--color-text-muted); font-size: .61rem; font-weight: 720; letter-spacing: .04em; text-transform: uppercase; }
.overview-card strong { font-size: 1rem; margin-top: .08rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.overview-card small { color: var(--color-text-muted); font-size: .64rem; margin-top: .04rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.insight-layout { align-items: stretch; display: grid; gap: 1rem; grid-template-columns: minmax(0, 1.65fr) minmax(16.5rem, .7fr); }
.panel { overflow: hidden; padding: 0; }
.panel > header { align-items: center; border-block-end: 1px solid var(--color-border); display: flex; gap: 1rem; justify-content: space-between; padding: .9rem 1rem; }
.panel h2 { font-size: .96rem; }
.section-kicker { color: var(--color-accent); font-size: .59rem; font-weight: 760; letter-spacing: .065em; margin-bottom: .1rem !important; text-transform: uppercase; }
.rating-list-picker { width: min(15rem, 50%); }
.rating-list-label, .form-panel header > span, .badges-panel header > span { color: var(--color-text-muted); font-size: .68rem; font-weight: 650; }
.rating-chart { display: grid; gap: .55rem; padding: .9rem 1rem .7rem; }
.rating-chart__summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); }
.rating-chart__summary > div { display: grid; min-width: 0; padding-inline: .8rem; }
.rating-chart__summary > div:first-child { padding-inline-start: 0; }
.rating-chart__summary > div + div { border-inline-start: 1px solid var(--color-border); }
.rating-chart__summary strong { font-size: 1rem; font-variant-numeric: tabular-nums; }
.rating-chart__summary span { color: var(--color-text-muted); font-size: .61rem; margin-top: .06rem; }
.positive { color: var(--color-success); }
.negative { color: var(--color-danger); }
.elo-chart { display: block; height: auto; overflow: visible; width: 100%; }
.elo-chart line { stroke: var(--color-border); stroke-width: 1; }
.elo-chart text { fill: var(--color-text-muted); font-size: 10px; }
.elo-chart__area { fill: color-mix(in srgb, var(--color-accent) 10%, transparent); }
.elo-chart__line { fill: none; stroke: var(--color-accent); stroke-linecap: round; stroke-linejoin: round; stroke-width: 3; }
.elo-chart circle { fill: var(--color-surface-raised); stroke: var(--color-accent); stroke-width: 2; }
.insight-column { display: grid; gap: 1rem; grid-template-rows: auto 1fr; }
.form-content { display: grid; gap: .9rem; padding: 1rem; }
.form-dots { display: flex; flex-wrap: wrap; gap: .35rem; }
.form-dots span { background: var(--color-surface-sunken); border-radius: 50%; display: grid; font-size: .64rem; font-weight: 800; height: 1.75rem; place-items: center; width: 1.75rem; }
.form-dots span[data-result='W'] { background: var(--color-success-soft); color: var(--color-success); }
.form-dots span[data-result='D'] { background: var(--color-warning-soft); color: var(--color-warning); }
.form-dots span[data-result='L'] { background: var(--color-danger-soft); color: var(--color-danger); }
.form-facts { display: grid; gap: .65rem; }
.form-facts div { display: grid; gap: .12rem; }
.form-facts span { color: var(--color-text-muted); font-size: .61rem; text-transform: uppercase; }
.form-facts strong { font-size: .73rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.badges-empty { align-items: center; display: flex; gap: .75rem; padding: 1rem; }
.badges-empty > span { background: var(--color-surface-sunken); border: 1px dashed var(--color-border-strong); border-radius: 50%; color: var(--color-text-muted); display: grid; flex: 0 0 auto; height: 2.8rem; place-items: center; width: 2.8rem; }
.badges-empty div { display: grid; gap: .12rem; }
.badges-empty strong { font-size: .73rem; }
.badges-empty p { color: var(--color-text-muted); font-size: .66rem; line-height: 1.45; }
.badge-list { display: grid; gap: .5rem; padding: .75rem; }
.badge-list article { align-items:center; background:linear-gradient(135deg,color-mix(in srgb,var(--color-warning-soft) 70%,var(--color-surface)) 0%,var(--color-surface-sunken) 100%); border:1px solid color-mix(in srgb,var(--color-warning) 18%,var(--color-border)); border-radius:var(--radius-md); display:grid; gap:.6rem; grid-template-columns:auto minmax(0,1fr); padding:.6rem .7rem; }
.badge-list article>span { align-items:center; background:var(--color-surface-raised); border:1px solid var(--color-border); border-radius:.65rem; display:flex; font-family:"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif; font-size:1.3rem; height:2.25rem; justify-content:center; width:2.25rem; }
.badge-list article>div { display:grid; gap:.1rem; min-width:0; }
.badge-list strong { color: var(--color-text); font-size: .7rem; }
.badge-list small { color:var(--color-text-muted); font-size:.62rem; line-height:1.35; }
.panel-empty { color: var(--color-text-muted); font-size: .74rem; padding: 1.5rem 1rem; text-align: center; }
.technical-panel summary { align-items: center; cursor: pointer; display: flex; justify-content: space-between; list-style: none; padding: .85rem 1rem; }
.technical-panel summary::-webkit-details-marker { display: none; }
.technical-panel summary > span { align-items: center; display: flex; gap: .45rem; min-width: 0; }
.technical-panel summary strong { font-size: .76rem; }
.technical-panel summary small { color: var(--color-text-muted); font-size: .65rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.technical-panel[open] summary > .app-icon { transform: rotate(180deg); }
.technical-content { border-block-start: 1px solid var(--color-border); display: grid; gap: 1rem; grid-template-columns: minmax(0, 1fr) minmax(14rem, .7fr); padding: 1rem; }
.technical-content dl { display: grid; gap: .7rem; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.technical-content dt { color: var(--color-text-muted); font-size: .59rem; text-transform: uppercase; }
.technical-content dd { font-size: .72rem; margin: .15rem 0 0; overflow-wrap: anywhere; }
.technical-content a { color: var(--color-accent); font-weight: 650; text-decoration: none; }
.uci-options { display: flex; flex-wrap: wrap; gap: .35rem; }
.uci-options span { background: var(--color-surface-sunken); border-radius: var(--radius-sm); display: grid; gap: .08rem; padding: .4rem .5rem; }
.uci-options strong { font-size: .59rem; }
.uci-options code { font-size: .63rem; }
.games-panel > header { align-items: start; }
.games-panel header p { color: var(--color-text-muted); font-size: .7rem; margin-top: .15rem; }
.games-panel__actions { align-items: center; display: flex; gap: .45rem; }
.filter-trigger__count { background: var(--color-accent); border-radius: 999px; color: var(--color-on-accent); display: inline-grid; font-size: .62rem; height: 1.25rem; min-width: 1.25rem; place-items: center; }
.active-filters { align-items: center; background: color-mix(in srgb, var(--color-accent-soft) 45%, var(--color-surface)); border-block-end: 1px solid var(--color-border); display: flex; gap: .4rem; min-height: 3rem; overflow-x: auto; padding: .5rem 1rem; }
.active-filters > span:first-child { color: var(--color-text-muted); flex: 0 0 auto; font-size: .66rem; font-weight: 700; }
.active-filters button { align-items: center; background: var(--color-surface-raised); border: 1px solid color-mix(in srgb, var(--color-accent) 28%, var(--color-border)); border-radius: 999px; color: var(--color-text-secondary); cursor: pointer; display: inline-flex; flex: 0 0 auto; font-size: .67rem; font-weight: 650; gap: .3rem; padding: .34rem .55rem; white-space: nowrap; }
.active-filters .active-filters__clear { background: transparent; border-color: transparent; color: var(--color-accent); }
.active-filters__loading { color: var(--color-text-muted); font-size: .67rem; margin-inline-start: auto; }
.games-results { min-height: 4rem; transition: opacity var(--transition-fast); }
.games-results--loading { opacity: .52; pointer-events: none; }
@media (max-width: 68rem) { .overview-grid { grid-template-columns: repeat(2, 1fr); } .insight-layout { grid-template-columns: 1fr; } .insight-column { grid-template-columns: repeat(2, 1fr); grid-template-rows: auto; } }
@media (max-width: 46rem) { .engine-heading { align-items: stretch; flex-direction: column; gap: 1rem; } .version-picker { flex-basis: auto; } .overview-grid, .insight-column { grid-template-columns: 1fr; } .rating-chart__summary { grid-template-columns: repeat(2, 1fr); gap: .7rem 0; } .rating-chart__summary > div:nth-child(3) { border-inline-start: 0; padding-inline-start: 0; } .technical-content { grid-template-columns: 1fr; } .games-panel > header { align-items: stretch; flex-direction: column; } .games-panel__actions { align-items: stretch; flex-direction: column; } }
@media (max-width: 32rem) { .overview-grid { grid-template-columns: 1fr; } .rating-panel > header { align-items: stretch; flex-direction: column; } .rating-list-picker { width: 100%; } .technical-content dl { grid-template-columns: 1fr; } }
</style>
