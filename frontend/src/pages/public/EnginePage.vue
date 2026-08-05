<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '@/api/client'
import ContentState from '@/components/public/ContentState.vue'
import EngineGameFiltersModal from '@/components/public/EngineGameFiltersModal.vue'
import GameTable from '@/components/public/GameTable.vue'
import { errorMessage } from '@/components/public/format'
import type {
  EngineGameFilterOptions,
  EngineGameFilters,
  EngineRecord,
  GameRecord,
} from '@/components/public/types'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'

interface EngineRecordSummary {
  wins: number
  draws: number
  losses: number
  games: number
}

interface EngineResponse {
  engine: EngineRecord
  games: GameRecord[]
  engines: Record<string, string>
  record: EngineRecordSummary
  filter_options: EngineGameFilterOptions
}

type FilterKey = keyof EngineGameFilters

const route = useRoute()
const data = ref<EngineResponse | null>(null)
const loading = ref(true)
const gamesLoading = ref(false)
const loadError = ref('')
const gameFilters = ref<EngineGameFilters>(emptyFilters())
const filterModalOpen = ref(false)
let controller: AbortController | null = null

const engineId = computed(() => String(route.params.id || ''))
const scorePercent = computed(() => {
  if (!data.value?.record.games) return 0
  return Math.round(((data.value.record.wins + data.value.record.draws * 0.5) / data.value.record.games) * 100)
})
const uciOptions = computed(() => Object.entries(data.value?.engine.uci_options || {}))
const activeFilterCount = computed(() => Object.values(gameFilters.value).filter(Boolean).length)
const pgnDownloadUrl = computed(() => {
  const parameters = new URLSearchParams({ engine_id: engineId.value })
  if (gameFilters.value.result) parameters.set('result', gameFilters.value.result)
  if (gameFilters.value.timeControl) parameters.set('time_control', gameFilters.value.timeControl)
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
  if (filters.timeControl) {
    const label = data.value?.filter_options.time_controls.find((item) => item.value === filters.timeControl)?.label
    chips.push({ key: 'timeControl', label: `Time: ${label || 'Selected control'}` })
  }
  if (filters.opponentId) {
    const name = data.value?.engines[filters.opponentId] || `Engine ${filters.opponentId}`
    chips.push({ key: 'opponentId', label: `Opponent: ${name}` })
  }
  if (filters.side) {
    chips.push({ key: 'side', label: `Playing as ${filters.side === 'white' ? 'White' : 'Black'}` })
  }
  return chips
})
const gamesDescription = computed(() => {
  if (!data.value) return ''
  if (!activeFilterCount.value) {
    return `${data.value.record.games} completed game${data.value.record.games === 1 ? '' : 's'} in the full record.`
  }
  const shown = data.value.games.length
  return `${shown === 50 ? 'Showing the 50 most recent matches' : `${shown} matching game${shown === 1 ? '' : 's'}`} from ${data.value.record.games} total.`
})

watch(engineId, () => {
  data.value = null
  gameFilters.value = emptyFilters()
  filterModalOpen.value = false
  void load()
}, { immediate: true })
onBeforeUnmount(() => controller?.abort())

function emptyFilters(): EngineGameFilters {
  return { result: '', timeControl: '', opponentId: '', side: '' }
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
        time_control: gameFilters.value.timeControl || undefined,
        opponent_id: gameFilters.value.opponentId || undefined,
        side: gameFilters.value.side || undefined,
      },
      signal: requestController.signal,
    })
    if (controller === requestController) data.value = response
  } catch (error) {
    if (controller === requestController && (error as { name?: string })?.name !== 'AbortError') {
      loadError.value = errorMessage(error, 'This engine could not be loaded.')
    }
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
        <div>
          <RouterLink class="back-link" to="/engines">Back to engines</RouterLink>
          <h1>{{ data.engine.name }}</h1>
          <p v-if="data.engine.author">By {{ data.engine.author }}</p>
        </div>

        <dl class="record-stats" aria-label="Game record">
          <div><dt>Wins</dt><dd>{{ data.record.wins }}</dd></div>
          <div><dt>Draws</dt><dd>{{ data.record.draws }}</dd></div>
          <div><dt>Losses</dt><dd>{{ data.record.losses }}</dd></div>
          <div><dt>Score</dt><dd>{{ scorePercent }}%</dd></div>
        </dl>
      </header>

      <div class="engine-layout">
        <section class="panel build-panel" aria-labelledby="build-title">
          <header>
            <div>
              <h2 id="build-title">Source version</h2>
            </div>
          </header>
          <dl class="build-details">
            <div><dt>Version</dt><dd>{{ data.engine.version || '-' }}</dd></div>
            <div><dt>Source type</dt><dd>{{ data.engine.source_kind || '-' }}</dd></div>
            <div><dt>Reference</dt><dd><code>{{ data.engine.source_ref || '-' }}</code></dd></div>
            <div class="detail-wide"><dt>Repository</dt><dd><a v-if="data.engine.repository_url" class="source-link" :href="data.engine.repository_url.replace(/\.git$/, '')" target="_blank" rel="noopener">{{ data.engine.repository_full_name || data.engine.repository_url }}</a><span v-else>-</span></dd></div>
            <div class="detail-wide"><dt>Build cache key</dt><dd><code :title="data.engine.build_hash || undefined">{{ data.engine.build_hash || '-' }}</code></dd></div>
          </dl>
        </section>

        <section class="panel options-panel" aria-labelledby="options-title">
          <header>
            <div>
              <h2 id="options-title">UCI options</h2>
            </div>
            <span>{{ uciOptions.length }}</span>
          </header>
          <dl v-if="uciOptions.length" class="option-list">
            <div v-for="([name, value]) in uciOptions" :key="name">
              <dt>{{ name }}</dt>
              <dd><code>{{ String(value) }}</code></dd>
            </div>
          </dl>
          <p v-else class="panel-empty">No custom UCI options.</p>
        </section>
      </div>

      <section class="panel games-panel" aria-labelledby="engine-games-title">
        <header>
          <div>
            <h2 id="engine-games-title">Recent games</h2>
            <p>{{ gamesDescription }}</p>
          </div>
          <div class="games-panel__actions">
            <BaseButton
              :href="pgnDownloadUrl"
              size="small"
              :disabled="!data.record.games || (Boolean(activeFilterCount) && !data.games.length)"
              download
            >
              <template #icon><AppIcon name="download" :size="15" /></template>
              {{ pgnDownloadLabel }}
            </BaseButton>
            <BaseButton
              class="filter-trigger"
              size="small"
              :disabled="gamesLoading"
              :aria-expanded="filterModalOpen"
              aria-haspopup="dialog"
              @click="filterModalOpen = true"
            >
              <template #icon><AppIcon name="filter" :size="15" /></template>
              Filters
              <template v-if="activeFilterCount" #trailing>
                <span class="filter-trigger__count">{{ activeFilterCount }}</span>
              </template>
            </BaseButton>
          </div>
        </header>

        <div v-if="activeFilterChips.length" class="active-filters" aria-label="Active game filters">
          <span>Filtered by</span>
          <button
            v-for="chip in activeFilterChips"
            :key="chip.key"
            type="button"
            :aria-label="`Remove ${chip.label} filter`"
            :disabled="gamesLoading"
            @click="removeFilter(chip.key)"
          >
            {{ chip.label }}
            <AppIcon name="close" :size="12" />
          </button>
          <button class="active-filters__clear" type="button" :disabled="gamesLoading" @click="clearFilters">Clear all</button>
          <span v-if="gamesLoading" class="active-filters__loading" role="status">Updating…</span>
        </div>

        <div class="games-results" :class="{ 'games-results--loading': gamesLoading }" :aria-busy="gamesLoading">
          <GameTable v-if="data.games.length" :games="data.games" :engines="data.engines" :show-round="false" caption="Recent games for this engine" />
          <ContentState v-else kind="empty" compact :title="activeFilterCount ? 'No games match these filters' : 'No games yet'" />
        </div>
      </section>

      <EngineGameFiltersModal
        :open="filterModalOpen"
        :filters="gameFilters"
        :options="data.filter_options"
        :engines="data.engines"
        :engine-id="engineId"
        :record="data.record"
        @close="filterModalOpen = false"
        @apply="applyFilters"
      />
    </template>
  </div>
</template>

<style scoped>
.engine-page {
  display: grid;
  gap: var(--space-xl, 2rem);
  padding-block: clamp(1.2rem, 2.5vw, 2.25rem) 3rem;
}

.engine-heading {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: var(--space-xl, 2rem);
  padding-block-end: var(--space-xl, 2rem);
  border-block-end: 1px solid var(--color-border, #d5dbe1);
}

.engine-heading h1,
.engine-heading p,
.record-stats,
.panel h2,
.panel p,
.panel dl {
  margin: 0;
}

.back-link {
  display: inline-block;
  margin-block-end: 1rem;
  color: var(--color-text-muted, #607080);
  font-size: 0.75rem;
  font-weight: 700;
  text-decoration: none;
}

.back-link:hover { color: var(--color-accent, #2f78c4); }

.engine-heading h1 {
  margin-block-start: 0.2rem;
  font-size: clamp(2rem, 5vw, 3.6rem);
  letter-spacing: -0.04em;
  line-height: 1;
}

.engine-heading > div > p {
  margin-block-start: 0.5rem;
  color: var(--color-text-muted, #607080);
}

.source-link {
  display: inline-block;
  color: var(--color-accent, #2f78c4);
  font-size: 0.76rem;
  font-weight: 700;
  text-decoration: none;
}

.source-link:hover { text-decoration: underline; text-underline-offset: 0.18em; }

.record-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(5rem, 1fr));
  gap: 1px;
  overflow: hidden;
  border: 1px solid var(--color-border, #d5dbe1);
  border-radius: var(--radius-md, 0.5rem);
  background: var(--color-border, #d5dbe1);
}

.record-stats div {
  min-width: 5rem;
  padding: 0.75rem 0.9rem;
  background: var(--color-surface, #fff);
}

.record-stats dt,
.build-details dt,
.option-list dt {
  color: var(--color-text-muted, #607080);
  font-size: 0.62rem;
  font-weight: 750;
  letter-spacing: 0.045em;
  text-transform: uppercase;
}

.record-stats dd {
  margin: 0.18rem 0 0;
  font-size: 1.25rem;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
}

.engine-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(17rem, 0.65fr);
  gap: var(--space-md, 1rem);
  align-items: start;
}

.panel {
  overflow: hidden;
  padding: 0;
}

.panel > header {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 1rem;
  padding: var(--space-md, 1rem);
  border-block-end: 1px solid var(--color-border, #d5dbe1);
}

.panel h2 { font-size: 1rem; }
.panel header p { margin-block-start: 0.2rem; color: var(--color-text-muted, #607080); font-size: 0.72rem; }
.options-panel header > span { color: var(--color-text-muted, #607080); font-size: 0.72rem; }

.games-panel__actions {
  display: flex;
  align-items: center;
  gap: 0.45rem;
}

.filter-trigger__count {
  min-width: 1.3rem;
  height: 1.3rem;
  display: inline-grid;
  place-items: center;
  border-radius: 999px;
  background: var(--color-accent);
  color: var(--color-on-accent);
  font-size: 0.64rem;
  font-variant-numeric: tabular-nums;
}

.active-filters {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  min-height: 3.1rem;
  overflow-x: auto;
  padding: 0.55rem 1rem;
  border-block-end: 1px solid var(--color-border);
  background: color-mix(in srgb, var(--color-accent-soft) 45%, var(--color-surface));
  scrollbar-width: thin;
}

.active-filters > span:first-child {
  flex: 0 0 auto;
  color: var(--color-text-muted);
  font-size: 0.67rem;
  font-weight: 700;
}

.active-filters button {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 0.3rem;
  border: 1px solid color-mix(in srgb, var(--color-accent) 28%, var(--color-border));
  border-radius: 999px;
  background: var(--color-surface-raised);
  color: var(--color-text-secondary);
  cursor: pointer;
  padding: 0.35rem 0.55rem;
  font-size: 0.68rem;
  font-weight: 650;
  white-space: nowrap;
}

.active-filters button:hover:not(:disabled) {
  border-color: var(--color-accent);
  color: var(--color-text);
}

.active-filters button:disabled {
  cursor: wait;
  opacity: 0.6;
}

.active-filters .active-filters__clear {
  border-color: transparent;
  background: transparent;
  color: var(--color-accent);
}

.active-filters .active-filters__loading {
  margin-inline-start: auto;
  color: var(--color-text-muted);
  font-size: 0.68rem;
  white-space: nowrap;
}

.games-results {
  min-height: 4rem;
  transition: opacity var(--transition-fast);
}

.games-results--loading {
  opacity: 0.52;
  pointer-events: none;
}

.build-details {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  background: var(--color-border, #d5dbe1);
}

.build-details div {
  min-width: 0;
  padding: 1rem;
  background: var(--color-surface, #fff);
}

.build-details .detail-wide { grid-column: span 3; }
.build-details dd,
.option-list dd { margin: 0.25rem 0 0; overflow-wrap: anywhere; }
.build-details code,
.option-list code { color: inherit; font-size: 0.76rem; }

.option-list {
  display: grid;
  max-height: 21rem;
  overflow: auto;
  padding: 0.3rem 1rem;
}

.option-list div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
  padding-block: 0.65rem;
  border-block-end: 1px solid var(--color-border, #d5dbe1);
}

.option-list div:last-child { border-block-end: 0; }
.option-list dd { text-align: end; }
.panel-empty { padding: 2.5rem 1rem; color: var(--color-text-muted, #607080); font-size: 0.8rem; text-align: center; }

@media (max-width: 62rem) {
  .engine-heading { align-items: stretch; flex-direction: column; }
  .record-stats { align-self: stretch; }
  .record-stats div { min-width: 0; }
  .engine-layout { grid-template-columns: 1fr; }
}

@media (max-width: 38rem) {
  .record-stats { grid-template-columns: repeat(2, 1fr); }
  .build-details { grid-template-columns: 1fr 1fr; }
  .build-details .detail-wide { grid-column: span 2; }
  .games-panel > header { align-items: stretch; flex-direction: column; }
  .games-panel__actions { align-items: stretch; flex-direction: column; }
}
</style>
