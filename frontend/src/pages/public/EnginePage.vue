<script setup lang="ts">
import { enginePath, ratingPath } from '@/router/publicUrls'
import { engineVersionLabel } from '@/components/public/format'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '@/api/client'
import EngineVersionTags from '@/components/engines/EngineVersionTags.vue'
import ContentState from '@/components/public/ContentState.vue'
import EngineGameFiltersModal from '@/components/public/EngineGameFiltersModal.vue'
import GameTable from '@/components/public/GameTable.vue'
import { errorMessage, formatDate } from '@/components/public/format'
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
  game_export_formats: Array<'pgn' | 'ptn'>
  engines: Record<string, string>
  engine_options: EngineOption[]
  record: EngineRecordSummary
  filter_options: EngineGameFilterOptions
  ratings: EngineRating[]
  version_ratings: Array<{ id: number; name: string; versions: Array<{ id: number; elo: number }> }>
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
const ratingMode = ref<'family' | 'version'>('family')
const focusedPoint = ref<number | null>(null)
const pinnedPoint = ref<number | null>(null)
let controller: AbortController | null = null

const engineId = computed(() => String(route.meta.publicResourceId ?? route.params.id ?? ''))
const scorePercent = computed(() => {
  if (!data.value?.record.games) return 0
  return Math.round(((data.value.record.wins + data.value.record.draws * 0.5) / data.value.record.games) * 100)
})
const results = computed(() => {
  const record = data.value?.record
  return record ? [
    { label: 'Wins', value: record.wins, kind: 'win' },
    { label: 'Draws', value: record.draws, kind: 'draw' },
    { label: 'Losses', value: record.losses, kind: 'loss' },
  ] : []
})
const repositoryUrl = computed(() => data.value?.engine.distribution === 'worker_local'
  ? ''
  : data.value?.engine.repository_url?.replace(/\.git$/, '') ?? '')
const uciOptions = computed(() => Object.entries(data.value?.engine.uci_options || {}))
const activeFilterCount = computed(() => Object.values(gameFilters.value).filter(Boolean).length)
const versionOptions = computed(() => (data.value?.versions ?? []).map((version, index) => ({
  value: String(version.id),
  label: engineVersionLabel(version.version) || version.name,
  description: `${version.distribution === 'worker_local' ? 'Worker-local' : version.source_kind === 'commit' ? 'Commit' : 'Release'}${index === 0 ? ' · latest' : ''}`,
})))
const ratingListOptions = computed(() => (data.value?.version_ratings ?? []).map((list) => ({
  value: String(list.id),
  label: list.name,
  description: `${list.versions.length} rated version${list.versions.length === 1 ? '' : 's'}`,
})))
const selectedRating = computed(() => data.value?.ratings.find((rating) => String(rating.rating_list.id) === selectedRatingListId.value) ?? null)
const chartHistory = computed(() => {
  if (ratingMode.value === 'version') {
    const rating = selectedRating.value
    return rating?.history.map((point) => ({
      ...point, id: engineId.value, label: formatDate(point.at), selected: false,
    })) ?? []
  }
  const list = data.value?.version_ratings.find((item) => String(item.id) === selectedRatingListId.value)
  const ratings = new Map(list?.versions.map((version) => [String(version.id), version.elo]))
  return (data.value?.versions ?? []).flatMap((version) => {
    const id = String(version.id)
    const elo = ratings.get(id)
    if (elo === undefined) return []
    return [{
      id, elo, at: version.source_date || version.created_at || '',
      label: engineVersionLabel(version.version) || version.name, selected: id === engineId.value,
    }]
  }).sort((left, right) => dateValue(left.at) - dateValue(right.at))
})
const ratingChart = computed(() => {
  const history = chartHistory.value
  if (!history.length) return null
  const values = history.map((point) => point.elo)
  const low = Math.floor((Math.min(...values) - 20) / 20) * 20
  const high = Math.ceil((Math.max(...values) + 20) / 20) * 20
  const width = 760
  const height = 236
  const left = 49
  const right = 18
  const top = 14
  const bottom = 33
  const plotWidth = width - left - right
  const plotHeight = height - top - bottom
  const firstDate = dateValue(history[0]!.at)
  const lastDate = dateValue(history.at(-1)!.at)
  const points = history.map((point, index) => ({
    ...point,
    x: left + plotWidth * (history.length === 1 ? .5
      : lastDate > firstDate && firstDate > 0 ? (dateValue(point.at) - firstDate) / (lastDate - firstDate)
      : index / (history.length - 1)),
    y: top + ((high - point.elo) / (high - low)) * plotHeight,
  }))
  const ticks = Array.from({ length: 5 }, (_, index) => ({
    value: Math.round(high - ((high - low) * index) / 4),
    y: top + (plotHeight * index) / 4,
  }))
  return {
    width,
    height,
    left,
    right: width - right,
    bottom: top + plotHeight,
    points,
    ticks,
    polyline: points.map((point) => `${point.x},${point.y}`).join(' '),
    area: `${points[0]!.x},${top + plotHeight} ${points.map((point) => `${point.x},${point.y}`).join(' ')} ${points.at(-1)!.x},${top + plotHeight}`,
    start: formatDate(history[0]!.at),
    end: points.length > 1 ? formatDate(history.at(-1)!.at) : '',
  }
})
const activePoint = computed(() => {
  const points = ratingChart.value?.points ?? []
  if (focusedPoint.value !== null && points[focusedPoint.value]) return points[focusedPoint.value]
  if (pinnedPoint.value !== null && points[pinnedPoint.value]) return points[pinnedPoint.value]
  return points.find((point) => point.selected) ?? points.at(-1)
})
const gameExports = computed(() => {
  const parameters = new URLSearchParams({ engine_id: engineId.value })
  if (gameFilters.value.result) parameters.set('result', gameFilters.value.result)
  if (gameFilters.value.ratingListId) parameters.set('rating_list_id', gameFilters.value.ratingListId)
  if (gameFilters.value.opponentId) parameters.set('opponent_id', gameFilters.value.opponentId)
  if (gameFilters.value.side) parameters.set('side', gameFilters.value.side)
  return (data.value?.game_export_formats ?? []).map((format) => ({
    href: `/api/${format}?${parameters.toString()}`,
    label: activeFilterCount.value ? `Download filtered ${format.toUpperCase()}` : `Download all ${format.toUpperCase()}s`,
  }))
})
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
  void load()
}, { immediate: true })

watch(data, (response) => {
  if (!response) return
  if (!response.version_ratings.some((list) => String(list.id) === selectedRatingListId.value)) {
    selectedRatingListId.value = String(response.ratings[0]?.rating_list.id ?? response.version_ratings[0]?.id ?? '')
  }
})
watch([ratingMode, selectedRatingListId, engineId], () => {
  focusedPoint.value = null
  pinnedPoint.value = null
})

onBeforeUnmount(() => controller?.abort())

function emptyFilters(): EngineGameFilters {
  return { result: '', ratingListId: '', opponentId: '', side: '' }
}

function dateValue(value: string): number {
  return Date.parse(value) || 0
}

function selectVersion(value: string | number): void {
  const target = String(value)
  if (target !== engineId.value) void router.push(enginePath(target))
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
      <div class="engine-topline">
        <RouterLink class="back-link" to="/engines"><AppIcon name="arrow-left" :size="15" /> All engines</RouterLink>
        <a v-if="repositoryUrl" class="back-link" :href="repositoryUrl" target="_blank" rel="noopener">Source repository <AppIcon name="external-link" :size="14" /></a>
      </div>

      <header class="engine-heading">
        <div class="engine-heading__identity">
          <h1>{{ data.family?.name || data.engine.name }}</h1>
          <p v-if="data.family?.author || data.engine.author">By {{ data.family?.author || data.engine.author }}</p>
          <div class="engine-meta">
            <EngineVersionTags :tags="data.engine.tags ?? []" />
            <span v-if="!data.family?.active || !data.engine.active" class="engine-availability">Unavailable</span>
          </div>
        </div>
        <div v-if="versionOptions.length" class="version-picker">
          <span>Engine version</span>
          <OptionPicker :model-value="engineId" :options="versionOptions" label="Engine version" icon="tag" :searchable="versionOptions.length > 7" @update:model-value="selectVersion" />
        </div>
      </header>

      <section class="panel performance-panel" aria-labelledby="rating-history-title">
        <header class="performance-header">
          <div>
            <h2 id="rating-history-title">Rating history</h2>
          </div>
          <div class="performance-controls">
            <div class="rating-mode" role="group" aria-label="Rating history view">
              <button type="button" :aria-pressed="ratingMode === 'family'" @click="ratingMode = 'family'">By version</button>
              <button type="button" :aria-pressed="ratingMode === 'version'" @click="ratingMode = 'version'">Over time</button>
            </div>
            <OptionPicker v-if="ratingListOptions.length > 1" v-model="selectedRatingListId" class="rating-list-picker" :options="ratingListOptions" label="Rating list" />
            <span v-else-if="ratingListOptions[0]" class="rating-list-label">{{ ratingListOptions[0].label }}</span>
          </div>
        </header>

        <div v-if="ratingChart && activePoint" class="rating-chart">
          <div class="chart-summary">
            <div class="chart-reading" aria-live="polite">
              <strong>{{ Math.round(activePoint.elo).toLocaleString() }} <small>Elo</small></strong>
              <RouterLink v-if="ratingMode === 'family' && activePoint.id !== engineId" :to="enginePath(activePoint.id)">{{ activePoint.label }}</RouterLink>
              <span v-else>{{ activePoint.label }}</span>
              <span v-if="ratingMode === 'family' && activePoint.at" class="chart-date">{{ formatDate(activePoint.at) }}</span>
            </div>
            <dl v-if="selectedRating" class="rating-facts">
              <div><dt>Current</dt><dd>{{ Math.round(selectedRating.elo).toLocaleString() }}<small v-if="selectedRating.error_margin != null"> ±{{ Math.round(selectedRating.error_margin) }}</small></dd></div>
              <div><dt>Rank</dt><dd>#{{ selectedRating.rank }} <small>of {{ selectedRating.field_size }}</small></dd></div>
              <div><dt>Rated games</dt><dd>{{ selectedRating.games_played.toLocaleString() }}</dd></div>
              <div v-if="ratingMode === 'version'"><dt>Peak</dt><dd>{{ Math.round(selectedRating.peak_elo).toLocaleString() }}</dd></div>
            </dl>
          </div>
          <svg class="elo-chart" :viewBox="'0 0 ' + ratingChart.width + ' ' + ratingChart.height" role="group" :aria-label="ratingMode === 'family' ? 'Elo by engine version' : 'Elo over time for this version'" @mouseleave="focusedPoint = null">
            <g v-for="tick in ratingChart.ticks" :key="tick.value">
              <line :x1="ratingChart.left" :x2="ratingChart.right" :y1="tick.y" :y2="tick.y" />
              <text :x="ratingChart.left - 8" :y="tick.y + 4" text-anchor="end">{{ tick.value }}</text>
            </g>
            <polygon class="elo-chart__area" :points="ratingChart.area" />
            <polyline class="elo-chart__line" :points="ratingChart.polyline" />
            <line class="elo-chart__guide" :x1="activePoint.x" :x2="activePoint.x" :y1="activePoint.y" :y2="ratingChart.bottom" />
            <circle v-for="(point, index) in ratingChart.points" :key="index" :cx="point.x" :cy="point.y" :r="point === activePoint ? 5 : 3" :class="{ 'chart-point--active': point === activePoint }" tabindex="0" role="button" :aria-pressed="pinnedPoint === index" :aria-label="point.label + ', ' + Math.round(point.elo) + ' Elo'" @mouseenter="focusedPoint = index" @click="pinnedPoint = index" @focus="focusedPoint = index" @blur="focusedPoint = null" @keydown.enter.prevent="pinnedPoint = index" @keydown.space.prevent="pinnedPoint = index" />
            <text :x="ratingChart.left" :y="ratingChart.height - 9">{{ ratingChart.start }}</text>
            <text :x="ratingChart.right" :y="ratingChart.height - 9" text-anchor="end">{{ ratingChart.end }}</text>
          </svg>
          <RouterLink v-if="selectedRatingListId" class="rating-link" :to="ratingPath(selectedRatingListId)">View rating list <AppIcon name="arrow-right" :size="13" /></RouterLink>
        </div>
        <ContentState v-else kind="empty" compact embedded :title="ratingMode === 'family' ? 'No rated versions yet' : 'No rating history for this version'" />
      </section>

      <div class="engine-main">
        <section class="panel games-panel" aria-labelledby="engine-games-title">
          <header>
            <div><h2 id="engine-games-title">Recent games</h2><p>{{ gamesDescription }}</p></div>
            <div class="games-panel__actions">
              <BaseButton v-for="download in gameExports" :key="download.href" :href="download.href" size="small" :disabled="gamesLoading" download><template #icon><AppIcon name="download" :size="15" /></template>{{ download.label }}</BaseButton>
              <BaseButton class="filter-trigger" size="small" :disabled="gamesLoading" :aria-expanded="filterModalOpen" aria-haspopup="dialog" @click="filterModalOpen = true"><template #icon><AppIcon name="filter" :size="15" /></template>Filters<template v-if="activeFilterCount" #trailing><span class="filter-trigger__count">{{ activeFilterCount }}</span></template></BaseButton>
            </div>
          </header>

          <div v-if="activeFilterChips.length" class="active-filters" aria-label="Active game filters">
            <button v-for="chip in activeFilterChips" :key="chip.key" type="button" :aria-label="`Remove ${chip.label} filter`" :disabled="gamesLoading" @click="removeFilter(chip.key)">{{ chip.label }}<AppIcon name="close" :size="12" /></button>
            <button class="active-filters__clear" type="button" :disabled="gamesLoading" @click="clearFilters">Clear all</button>
            <span v-if="gamesLoading" class="active-filters__loading" role="status">Updating…</span>
          </div>

          <div class="games-results" :class="{ 'games-results--loading': gamesLoading }" :aria-busy="gamesLoading">
            <GameTable v-if="data.games.length" :games="data.games" :engines="data.engines" :show-round="false" caption="Recent games for this engine" />
            <ContentState v-else kind="empty" compact embedded :title="activeFilterCount ? 'No games match these filters' : 'No games yet'" />
          </div>
        </section>

        <aside class="engine-sidebar" aria-label="Selected version details">
          <section v-if="data.record.games" class="panel">
            <header><h2>Game record</h2></header>
            <div class="record-content">
              <p><strong>{{ scorePercent }}%</strong> score across {{ data.record.games.toLocaleString() }} games</p>
              <div class="result-bar" aria-hidden="true">
                <span v-for="result in results.filter((item) => item.value)" :key="result.kind" :data-result="result.kind" :style="{ flexGrow: result.value }"></span>
              </div>
              <dl class="result-counts">
                <div v-for="result in results" :key="result.kind"><dt>{{ result.label }}</dt><dd>{{ result.value.toLocaleString() }}</dd></div>
              </dl>
            </div>
          </section>
          <section v-if="data.badges.length" class="panel">
            <header><h2>Badges</h2></header>
            <div class="badge-list">
              <article v-for="badge in data.badges" :key="badge.id"><span aria-hidden="true">{{ badge.emoji }}</span><div><strong>{{ badge.name }}</strong><small v-if="badge.description">{{ badge.description }}</small></div></article>
            </div>
          </section>
          <details class="panel technical-panel">
            <summary><strong>Build details</strong><AppIcon name="chevron-down" :size="16" /></summary>
            <div class="technical-content">
              <dl>
                <div v-if="engineVersionLabel(data.engine.version)"><dt>Version</dt><dd>{{ engineVersionLabel(data.engine.version) }}</dd></div>
                <div><dt>Source</dt><dd v-if="data.engine.distribution === 'worker_local'">Worker-local binary</dd><dd v-else>{{ data.engine.source_kind || '—' }} <code v-if="data.engine.source_ref">{{ data.engine.source_ref }}</code></dd></div>
                <div v-if="data.engine.source_date"><dt>Source date</dt><dd>{{ formatDate(data.engine.source_date) }}</dd></div>
                <div v-if="data.engine.created_at"><dt>Added to COPE</dt><dd>{{ formatDate(data.engine.created_at) }}</dd></div>
              </dl>
              <div v-if="uciOptions.length" class="uci-options"><span v-for="([name, value]) in uciOptions" :key="name"><strong>{{ name }}</strong><code>{{ String(value) }}</code></span></div>
            </div>
          </details>
        </aside>
      </div>

      <EngineGameFiltersModal :open="filterModalOpen" :filters="gameFilters" :options="data.filter_options" :engines="data.engines" :engine-options="data.engine_options" :engine-id="engineId" :record="data.record" @close="filterModalOpen = false" @apply="applyFilters" />
    </template>
  </div>
</template>

<style scoped>
.engine-page { display: grid; gap: 1rem; padding-block: clamp(1.2rem, 2.5vw, 2.25rem) 3rem; }
.engine-page h1, .engine-page h2, .engine-page h3, .engine-page p, .engine-page dl { margin: 0; }
.engine-topline { display: flex; justify-content: space-between; align-items: center; gap: .75rem; }
.back-link, .rating-link { display: inline-flex; align-items: center; gap: .35rem; color: var(--color-text-muted); font-size: .76rem; font-weight: 650; text-decoration: none; }
.back-link:hover, .rating-link:hover { color: var(--color-accent); }
.engine-heading { display: flex; align-items: end; justify-content: space-between; gap: 1.5rem; padding: .2rem 0 1rem; border-block-end: 1px solid var(--color-border); }
.engine-heading__identity { min-width: 0; }
.engine-heading h1 { font-size: clamp(2.4rem, 6vw, 4.35rem); letter-spacing: -.055em; line-height: .97; overflow-wrap: anywhere; }
.engine-heading p { color: var(--color-text-muted); margin-block-start: .6rem; }
.engine-meta { display: flex; align-items: center; flex-wrap: wrap; gap: .5rem; }
.engine-meta:not(:empty) { margin-block-start: .45rem; }
.engine-availability { color: var(--color-text-muted); font-size: .75rem; }
.version-picker { display: grid; flex: 0 1 16rem; gap: .35rem; min-width: 11rem; }
.version-picker > span { color: var(--color-text-muted); font-size: .72rem; font-weight: 650; }
.panel { min-width: 0; overflow: hidden; padding: 0; }
.panel > header { display: flex; align-items: center; justify-content: space-between; gap: .75rem; padding: .8rem 1rem; border-block-end: 1px solid var(--color-border); }
.panel h2 { font-size: 1rem; }
.performance-controls { display: flex; align-items: center; gap: .65rem; }
.rating-mode { display: flex; flex: 0 0 auto; gap: .15rem; padding: .18rem; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface-sunken); }
.rating-mode button { padding: .38rem .65rem; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--color-text-muted); cursor: pointer; font-size: .76rem; font-weight: 650; }
.rating-mode button[aria-pressed='true'] { background: var(--color-surface-raised); color: var(--color-accent); box-shadow: var(--shadow-sm); }
.rating-list-picker { width: 13rem; }
.rating-list-label { color: var(--color-text-muted); font-size: .76rem; font-weight: 650; }
.rating-chart { display: grid; gap: .6rem; padding: clamp(.7rem, 2vw, 1.4rem); }
.chart-summary { display: flex; align-items: end; justify-content: space-between; gap: .85rem; flex-wrap: wrap; }
.chart-reading { display: flex; align-items: baseline; flex-wrap: wrap; gap: .2rem .55rem; min-width: 0; }
.chart-reading > strong { font-size: clamp(1.5rem, 3vw, 2.5rem); font-variant-numeric: tabular-nums; letter-spacing: -.045em; line-height: 1; }
.chart-reading > strong small { color: var(--color-text-muted); font-size: .55em; }
.chart-reading > a, .chart-reading > span { font-size: .82rem; font-weight: 650; overflow-wrap: anywhere; }
.chart-reading > a { color: var(--color-accent); }
.chart-reading .chart-date { color: var(--color-text-muted); font-size: .72rem; font-weight: 500; }
.rating-facts { display: flex; flex-wrap: wrap; gap: .6rem 1.1rem; font-variant-numeric: tabular-nums; }
.rating-facts dt { color: var(--color-text-muted); font-size: .7rem; }
.rating-facts dd { margin: 0; font-size: .92rem; font-weight: 700; }
.rating-facts small { color: var(--color-text-muted); font-size: .74em; font-weight: 550; }
.elo-chart { display: block; width: 100%; max-height: 20rem; overflow: visible; }
.elo-chart line { stroke: var(--color-border); stroke-width: 1; vector-effect: non-scaling-stroke; }
.elo-chart text { fill: var(--color-text-muted); font-size: 12px; }
.elo-chart__area { fill: color-mix(in srgb, var(--color-accent) 12%, transparent); }
.elo-chart__line { fill: none; stroke: var(--color-accent); stroke-linecap: round; stroke-linejoin: round; stroke-width: 2; vector-effect: non-scaling-stroke; }
.elo-chart .elo-chart__guide { stroke: var(--color-accent); stroke-dasharray: 3 3; }
.elo-chart circle { fill: var(--color-surface-raised); stroke: var(--color-accent); stroke-width: 1.5; cursor: pointer; vector-effect: non-scaling-stroke; }
.elo-chart .chart-point--active { fill: var(--color-accent); }
.rating-link { justify-self: start; }
.engine-main { display: grid; align-items: start; grid-template-columns: minmax(0, 1fr) minmax(15rem, 19rem); gap: 1rem; }
.engine-sidebar { display: grid; gap: .75rem; min-width: 0; }
.record-content { display: grid; gap: .7rem; padding: .8rem 1rem; }
.record-content > p { display: flex; align-items: baseline; gap: .45rem; color: var(--color-text-muted); font-size: .75rem; }
.record-content > p strong { color: var(--color-text); font-size: 1.4rem; }
.result-bar { display: flex; gap: 2px; height: .42rem; overflow: hidden; border-radius: 999px; }
.result-bar > span[data-result='win'] { background: var(--color-success); }
.result-bar > span[data-result='draw'] { background: var(--color-border-strong); }
.result-bar > span[data-result='loss'] { background: var(--color-danger); }
.result-counts { display: grid; grid-template-columns: repeat(3, 1fr); gap: .35rem; }
.result-counts dt { color: var(--color-text-muted); font-size: .68rem; }
.result-counts dd { margin: 0; font-size: .85rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.badge-list { display: grid; gap: .55rem; padding: .75rem 1rem; }
.badge-list article { display: flex; align-items: start; gap: .55rem; }
.badge-list article > span { font-size: 1.05rem; }
.badge-list article > div { display: grid; gap: .08rem; }
.badge-list strong { font-size: .78rem; }
.badge-list small { color: var(--color-text-muted); font-size: .7rem; }
.technical-panel summary { display: flex; align-items: center; justify-content: space-between; gap: .4rem; padding: .75rem 1rem; cursor: pointer; list-style: none; font-size: .85rem; }
.technical-panel summary::-webkit-details-marker { display: none; }
.technical-panel[open] summary > .app-icon { transform: rotate(180deg); }
.technical-content { display: grid; gap: .7rem; padding: .75rem 1rem; border-block-start: 1px solid var(--color-border); }
.technical-content dl { display: grid; gap: .55rem; }
.technical-content dt { color: var(--color-text-muted); font-size: .7rem; }
.technical-content dd { margin: .08rem 0 0; font-size: .75rem; overflow-wrap: anywhere; }
.uci-options { display: grid; gap: .35rem; }
.uci-options span { display: flex; align-items: baseline; justify-content: space-between; gap: .4rem; padding-block-start: .35rem; border-block-start: 1px solid var(--color-border); font-size: .72rem; overflow-wrap: anywhere; }
.games-panel header p { margin-top: .15rem; color: var(--color-text-muted); font-size: .74rem; }
.games-panel__actions { display: flex; align-items: center; gap: .4rem; flex-wrap: wrap; }
.filter-trigger__count { display: inline-grid; place-items: center; min-width: 1.25rem; height: 1.25rem; border-radius: 999px; background: var(--color-accent); color: var(--color-on-accent); font-size: .67rem; }
.active-filters { display: flex; align-items: center; gap: .4rem; flex-wrap: wrap; padding: .5rem 1rem; border-block-end: 1px solid var(--color-border); }
.active-filters button { display: inline-flex; align-items: center; gap: .3rem; padding: .29rem .5rem; border: 1px solid var(--color-border); border-radius: 999px; background: var(--color-surface-sunken); color: var(--color-text); cursor: pointer; font-size: .72rem; }
.active-filters .active-filters__clear { border-color: transparent; background: transparent; color: var(--color-accent); }
.active-filters__loading { color: var(--color-text-muted); font-size: .7rem; }
.games-results { min-height: 4rem; transition: opacity var(--transition-fast); }
.games-results--loading { opacity: .55; pointer-events: none; }
@media (max-width: 58rem) {
  .engine-main { grid-template-columns: minmax(0, 1fr); }
  .engine-sidebar { grid-row: 1; grid-template-columns: repeat(auto-fit, minmax(min(100%, 15rem), 1fr)); align-items: start; }
}
@media (max-width: 38rem) {
  .engine-heading { align-items: stretch; flex-direction: column; gap: .8rem; }
  .version-picker { flex-basis: auto; }
  .performance-panel > header, .games-panel > header { align-items: start; flex-direction: column; }
  .performance-controls { flex-wrap: wrap; width: 100%; }
  .rating-list-picker { width: auto; min-width: 10rem; flex: 1; }
  .elo-chart text { font-size: 16px; }
}
</style>
