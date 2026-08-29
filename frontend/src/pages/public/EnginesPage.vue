<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import ContentState from '@/components/public/ContentState.vue'
import { errorMessage } from '@/components/public/format'
import StatusPill from '@/components/public/StatusPill.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import OptionPicker from '@/components/ui/OptionPicker.vue'

interface EngineRecordSummary {
  wins: number
  draws: number
  losses: number
  games: number
}

interface EngineVersionSummary {
  id: number
  version: string
  distribution: 'managed' | 'worker_local'
  source_kind: 'release' | 'commit'
  source_ref: string
  repository_full_name: string
  active: boolean
  created_at: string
}

interface EngineFamilyRecord {
  id: number
  name: string
  author: string
  active: boolean
  latest_version_id: number
  latest_version: string
  created_at: string
  version_count: number
  versions: EngineVersionSummary[]
  record: EngineRecordSummary
}

interface EnginesResponse {
  engines: EngineFamilyRecord[]
  stats: { families: number; versions: number; available: number; games: number }
}

type AvailabilityFilter = 'all' | 'available' | 'unavailable'
type SourceFilter = 'all' | 'release' | 'commit' | 'worker_local'
type SortOption = 'name' | 'games' | 'score' | 'newest'

const data = ref<EnginesResponse | null>(null)
const loading = ref(true)
const loadError = ref('')
const query = ref('')
const availability = ref<AvailabilityFilter>('all')
const source = ref<SourceFilter>('all')
const sort = ref<SortOption>('name')
let controller: AbortController | null = null

const availabilityOptions = [
  { value: 'all', label: 'All families' },
  { value: 'available', label: 'Available' },
  { value: 'unavailable', label: 'Unavailable' },
]
const sourceOptions = [
  { value: 'all', label: 'All sources' },
  { value: 'release', label: 'Releases' },
  { value: 'commit', label: 'Commits' },
  { value: 'worker_local', label: 'Worker-local' },
]
const sortOptions = [
  { value: 'name', label: 'Name' },
  { value: 'games', label: 'Most games' },
  { value: 'score', label: 'Best score' },
  { value: 'newest', label: 'Newest version' },
]

const filtersActive = computed(() => Boolean(query.value.trim()) || availability.value !== 'all' || source.value !== 'all')
const displayedEngines = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase()
  const engines = (data.value?.engines ?? []).filter((engine) => {
    if (availability.value === 'available' && !engine.active) return false
    if (availability.value === 'unavailable' && engine.active) return false
    if (source.value !== 'all' && !engine.versions.some((version) => source.value === 'worker_local' ? version.distribution === 'worker_local' : version.distribution === 'managed' && version.source_kind === source.value)) return false
    if (!needle) return true
    return [
      engine.name,
      engine.author,
      ...engine.versions.flatMap((version) => [version.version, version.repository_full_name, version.source_ref]),
    ].join(' ').toLocaleLowerCase().includes(needle)
  })

  return engines.sort((left, right) => {
    if (sort.value === 'games') return right.record.games - left.record.games || compareNames(left, right)
    if (sort.value === 'score') return score(right) - score(left) || right.record.games - left.record.games || compareNames(left, right)
    if (sort.value === 'newest') return Date.parse(right.created_at) - Date.parse(left.created_at) || compareNames(left, right)
    return compareNames(left, right)
  })
})

onMounted(load)
onBeforeUnmount(() => controller?.abort())

async function load(): Promise<void> {
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  loadError.value = ''
  try {
    data.value = await api.get<EnginesResponse>('/api/engines', { signal: controller.signal })
  } catch (error) {
    if ((error as { name?: string })?.name !== 'AbortError') loadError.value = errorMessage(error, 'Engines could not be loaded.')
  } finally {
    loading.value = false
  }
}

function compareNames(left: EngineFamilyRecord, right: EngineFamilyRecord): number {
  return left.name.localeCompare(right.name)
}

function score(engine: EngineFamilyRecord): number {
  if (!engine.record.games) return -1
  return ((engine.record.wins + engine.record.draws * 0.5) / engine.record.games) * 100
}

function scoreLabel(engine: EngineFamilyRecord): string {
  const value = score(engine)
  return value < 0 ? '—' : `${Math.round(value)}%`
}

function recordLabel(engine: EngineFamilyRecord): string {
  return `${engine.record.wins}–${engine.record.draws}–${engine.record.losses}`
}

function clearFilters(): void {
  query.value = ''
  availability.value = 'all'
  source.value = 'all'
}
</script>

<template>
  <div class="page-container engines-page">
    <ContentState v-if="loading && !data" kind="loading" title="Loading engines" />
    <ContentState v-else-if="loadError && !data" kind="error" :message="loadError" action-label="Try again" @action="load" />

    <template v-else-if="data">
      <header class="engines-heading">
        <div>
          <p class="eyebrow">Engine directory</p>
          <h1>Engines</h1>
          <p>Explore engine families, then choose the version you want to inspect.</p>
        </div>
      </header>

      <dl class="engine-stats" aria-label="Engine statistics">
        <div><dt>Engine families</dt><dd>{{ data.stats.families.toLocaleString() }}</dd></div>
        <div><dt>Total versions</dt><dd>{{ data.stats.versions.toLocaleString() }}</dd></div>
        <div><dt>Available now</dt><dd>{{ data.stats.available.toLocaleString() }}</dd></div>
        <div><dt>Completed games</dt><dd>{{ data.stats.games.toLocaleString() }}</dd></div>
      </dl>

      <section class="directory" aria-labelledby="engine-directory-title">
        <div class="directory__heading">
          <div>
            <h2 id="engine-directory-title">Engine families</h2>
            <p>{{ displayedEngines.length.toLocaleString() }} of {{ data.engines.length.toLocaleString() }} shown</p>
          </div>
          <button v-if="filtersActive" class="clear-button" type="button" @click="clearFilters">Clear filters</button>
        </div>

        <div class="filters">
          <BaseInput v-model="query" type="search" icon="search" label="Search engines" placeholder="Name, author, version, or repository" autocomplete="off" />
          <label class="filter-field"><span>Availability</span><OptionPicker v-model="availability" :options="availabilityOptions" label="Filter by availability" /></label>
          <label class="filter-field"><span>Source</span><OptionPicker v-model="source" :options="sourceOptions" label="Filter by source" /></label>
          <label class="filter-field"><span>Sort by</span><OptionPicker v-model="sort" :options="sortOptions" label="Sort engine families" /></label>
        </div>

        <div v-if="displayedEngines.length" class="engine-grid">
          <article v-for="engine in displayedEngines" :key="engine.id" class="engine-card">
            <RouterLink class="engine-card__link" :to="`/engines/${engine.latest_version_id}`" :aria-label="`View ${engine.name}`" />
            <header class="engine-card__heading">
              <span class="engine-card__mark" aria-hidden="true"><AppIcon name="engine" :size="21" /></span>
              <div>
                <h3>{{ engine.name }}</h3>
                <p>{{ engine.version_count }} {{ engine.version_count === 1 ? 'version' : 'versions' }} · latest {{ engine.latest_version }}</p>
              </div>
              <StatusPill :status="engine.active ? 'ready' : 'unavailable'" />
            </header>
            <p class="engine-card__author">{{ engine.author ? `By ${engine.author}` : 'Unknown author' }}</p>
            <dl class="engine-card__stats">
              <div><dt>Games</dt><dd>{{ engine.record.games.toLocaleString() }}</dd></div>
              <div><dt>Score</dt><dd>{{ scoreLabel(engine) }}</dd></div>
              <div><dt>W–D–L</dt><dd>{{ recordLabel(engine) }}</dd></div>
            </dl>
            <div class="engine-card__versions">
              <span v-for="version in engine.versions.slice(0, 3)" :key="version.id">{{ version.version }}</span>
              <span v-if="engine.versions.length > 3">+{{ engine.versions.length - 3 }}</span>
            </div>
            <footer class="engine-card__footer">
              <span>{{ engine.versions[0]?.distribution === 'worker_local' ? 'Worker-local binary' : engine.versions[0]?.repository_full_name || 'Repository unavailable' }}</span>
              <span class="engine-card__open">View family <AppIcon name="arrow-right" :size="16" /></span>
            </footer>
          </article>
        </div>
        <ContentState v-else kind="empty" compact title="No matching engines" message="Try a different search or clear the filters." action-label="Clear filters" @action="clearFilters" />
      </section>
    </template>
  </div>
</template>

<style scoped>
.engines-page { display: grid; gap: var(--space-xl); padding-block: clamp(1.4rem, 3vw, 2.5rem) 3rem; }
.engines-heading h1, .engines-heading p, .engine-stats, .directory h2, .directory p, .engine-card h3, .engine-card p, .engine-card dl { margin: 0; }
.engines-heading { display: flex; align-items: end; justify-content: space-between; gap: var(--space-xl); }
.engines-heading .eyebrow { color: var(--color-accent); font-size: .68rem; font-weight: 760; letter-spacing: .08em; text-transform: uppercase; }
.engines-heading h1 { margin-top: .25rem; font-size: clamp(2rem, 5vw, 3.4rem); letter-spacing: -.04em; line-height: 1; }
.engines-heading div > p:last-child { margin-top: .55rem; color: var(--color-text-muted); font-size: .86rem; }
.engine-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); overflow: hidden; border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-border); gap: 1px; }
.engine-stats div { background: var(--color-surface); padding: .9rem 1rem; }
.engine-stats dt { color: var(--color-text-muted); font-size: .62rem; font-weight: 720; letter-spacing: .045em; text-transform: uppercase; }
.engine-stats dd { margin: .22rem 0 0; font-size: 1.2rem; font-weight: 800; font-variant-numeric: tabular-nums; }
.directory { display: grid; gap: 1rem; }
.directory__heading { align-items: end; display: flex; justify-content: space-between; gap: 1rem; }
.directory__heading h2 { font-size: 1rem; }
.directory__heading p { color: var(--color-text-muted); font-size: .72rem; margin-top: .15rem; }
.clear-button { background: transparent; color: var(--color-accent); cursor: pointer; font-size: .72rem; font-weight: 700; padding: .4rem; }
.filters { align-items: end; display: grid; gap: .65rem; grid-template-columns: minmax(14rem, 1.7fr) repeat(3, minmax(9rem, 1fr)); }
.filter-field { display: grid; gap: .35rem; min-width: 0; }
.filter-field > span { color: var(--color-text-muted); font-size: .64rem; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }
.engine-grid { display: grid; gap: .8rem; grid-template-columns: repeat(auto-fill, minmax(min(100%, 19rem), 1fr)); }
.engine-card { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-lg); display: grid; gap: .8rem; min-width: 0; padding: 1rem; position: relative; transition: border-color var(--transition-fast), transform var(--transition-fast), box-shadow var(--transition-fast); }
.engine-card:hover { border-color: color-mix(in srgb, var(--color-accent) 58%, var(--color-border)); box-shadow: var(--shadow-sm); transform: translateY(-2px); }
.engine-card:has(.engine-card__link:focus-visible) { border-color: var(--color-focus); box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-focus) 20%, transparent); }
.engine-card__link { border-radius: inherit; inset: 0; position: absolute; z-index: 1; }
.engine-card__heading { align-items: center; display: grid; gap: .65rem; grid-template-columns: auto minmax(0, 1fr) auto; }
.engine-card__mark { background: var(--color-accent-soft); border-radius: var(--radius-md); color: var(--color-accent); display: grid; height: 2.5rem; place-items: center; width: 2.5rem; }
.engine-card__heading h3 { overflow: hidden; font-size: 1rem; text-overflow: ellipsis; white-space: nowrap; }
.engine-card__heading p { color: var(--color-text-muted); font-size: .69rem; margin-top: .15rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.engine-card__author { color: var(--color-text-secondary); font-size: .76rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.engine-card__stats { border-block: 1px solid var(--color-border); display: grid; grid-template-columns: .8fr .75fr 1.2fr; padding-block: .75rem; }
.engine-card__stats > div { min-width: 0; padding-inline: .75rem; }
.engine-card__stats > div:first-child { padding-inline-start: 0; }
.engine-card__stats > div + div { border-inline-start: 1px solid var(--color-border); }
.engine-card__stats dt { color: var(--color-text-muted); font-size: .59rem; font-weight: 720; letter-spacing: .04em; text-transform: uppercase; }
.engine-card__stats dd { font-size: .84rem; font-weight: 760; margin: .22rem 0 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.engine-card__versions { display: flex; flex-wrap: wrap; gap: .32rem; }
.engine-card__versions span { background: var(--color-surface-sunken); border: 1px solid var(--color-border); border-radius: 999px; color: var(--color-text-muted); font-size: .62rem; font-weight: 650; padding: .22rem .45rem; }
.engine-card__footer { align-items: center; color: var(--color-text-muted); display: flex; font-size: .67rem; gap: .7rem; justify-content: space-between; margin-top: auto; min-width: 0; }
.engine-card__footer > span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.engine-card__open { align-items: center; color: var(--color-accent); display: inline-flex; flex: 0 0 auto; font-weight: 720; gap: .25rem; }
@media (max-width: 62rem) { .filters { grid-template-columns: repeat(3, minmax(0, 1fr)); } .filters > :first-child { grid-column: 1 / -1; } }
@media (max-width: 46rem) { .engine-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); } .filters { grid-template-columns: 1fr; } .filters > :first-child { grid-column: auto; } }
@media (max-width: 30rem) { .engine-card__heading { grid-template-columns: auto minmax(0, 1fr); } .engine-card__heading .status-pill { grid-column: 1 / -1; justify-self: start; } }
</style>
