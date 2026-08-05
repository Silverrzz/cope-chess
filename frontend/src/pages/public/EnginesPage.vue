<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import ContentState from '@/components/public/ContentState.vue'
import { errorMessage } from '@/components/public/format'
import StatusPill from '@/components/public/StatusPill.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseInput from '@/components/ui/BaseInput.vue'

interface EngineRecordSummary {
  wins: number
  draws: number
  losses: number
  games: number
}

interface EngineListRecord {
  id: number
  engine_id: number
  name: string
  author: string
  version: string
  repository_full_name: string
  source_ref: string
  source_kind: 'release' | 'commit'
  active: boolean
  created_at: string
  record: EngineRecordSummary
}

interface EngineStats {
  families: number
  versions: number
  available: number
  games: number
}

interface EnginesResponse {
  engines: EngineListRecord[]
  stats: EngineStats
}

type AvailabilityFilter = 'all' | 'available' | 'unavailable'
type SourceFilter = 'all' | 'release' | 'commit'
type SortOption = 'name' | 'games' | 'score' | 'newest'

const data = ref<EnginesResponse | null>(null)
const loading = ref(true)
const loadError = ref('')
const query = ref('')
const availability = ref<AvailabilityFilter>('all')
const source = ref<SourceFilter>('all')
const sort = ref<SortOption>('name')
let controller: AbortController | null = null

const filtersActive = computed(() => Boolean(query.value.trim()) || availability.value !== 'all' || source.value !== 'all')
const displayedEngines = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase()
  const engines = (data.value?.engines ?? []).filter((engine) => {
    if (availability.value === 'available' && !engine.active) return false
    if (availability.value === 'unavailable' && engine.active) return false
    if (source.value !== 'all' && engine.source_kind !== source.value) return false
    if (!needle) return true
    return [engine.name, engine.author, engine.version, engine.repository_full_name, engine.source_ref]
      .join(' ')
      .toLocaleLowerCase()
      .includes(needle)
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
    if ((error as { name?: string })?.name !== 'AbortError') {
      loadError.value = errorMessage(error, 'Engines could not be loaded.')
    }
  } finally {
    loading.value = false
  }
}

function compareNames(left: EngineListRecord, right: EngineListRecord): number {
  return left.name.localeCompare(right.name) || right.version.localeCompare(left.version, undefined, { numeric: true })
}

function score(engine: EngineListRecord): number {
  if (!engine.record.games) return -1
  return ((engine.record.wins + engine.record.draws * 0.5) / engine.record.games) * 100
}

function scoreLabel(engine: EngineListRecord): string {
  const value = score(engine)
  return value < 0 ? '—' : `${Math.round(value)}%`
}

function recordLabel(engine: EngineListRecord): string {
  return `${engine.record.wins}–${engine.record.draws}–${engine.record.losses}`
}

function sourceLabel(engine: EngineListRecord): string {
  return engine.source_kind === 'release' ? 'Release' : 'Commit'
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
          <p>Explore every engine version that has competed on COPE.</p>
        </div>
      </header>

      <dl class="engine-stats" aria-label="Engine statistics">
        <div>
          <dt>Engine families</dt>
          <dd>{{ data.stats.families.toLocaleString() }}</dd>
        </div>
        <div>
          <dt>Total versions</dt>
          <dd>{{ data.stats.versions.toLocaleString() }}</dd>
        </div>
        <div>
          <dt>Available now</dt>
          <dd>{{ data.stats.available.toLocaleString() }}</dd>
        </div>
        <div>
          <dt>Completed games</dt>
          <dd>{{ data.stats.games.toLocaleString() }}</dd>
        </div>
      </dl>

      <section class="directory" aria-labelledby="engine-directory-title">
        <div class="directory__heading">
          <div>
            <h2 id="engine-directory-title">All engine versions</h2>
            <p>{{ displayedEngines.length.toLocaleString() }} of {{ data.engines.length.toLocaleString() }} shown</p>
          </div>
          <button v-if="filtersActive" class="clear-button" type="button" @click="clearFilters">Clear filters</button>
        </div>

        <div class="filters">
          <div class="search-field">
            <BaseInput
              v-model="query"
              type="search"
              icon="search"
              label="Search engines"
              placeholder="Name, author, version, or repository"
              autocomplete="off"
            />
          </div>
          <label class="filter-field">
            <span>Availability</span>
            <select v-model="availability">
              <option value="all">All engines</option>
              <option value="available">Available</option>
              <option value="unavailable">Unavailable</option>
            </select>
          </label>
          <label class="filter-field">
            <span>Source</span>
            <select v-model="source">
              <option value="all">All sources</option>
              <option value="release">Releases</option>
              <option value="commit">Commits</option>
            </select>
          </label>
          <label class="filter-field">
            <span>Sort by</span>
            <select v-model="sort">
              <option value="name">Name</option>
              <option value="games">Most games</option>
              <option value="score">Best score</option>
              <option value="newest">Newest</option>
            </select>
          </label>
        </div>

        <div v-if="displayedEngines.length" class="engine-grid">
          <article v-for="engine in displayedEngines" :key="engine.id" class="engine-card">
            <RouterLink
              class="engine-card__link"
              :to="`/engines/${engine.id}`"
              :aria-label="`View ${engine.name} ${engine.version}`"
            />

            <header class="engine-card__heading">
              <span class="engine-card__mark" aria-hidden="true"><AppIcon name="engine" :size="21" /></span>
              <div>
                <h3>{{ engine.name }}</h3>
                <p>Version {{ engine.version }}</p>
              </div>
              <StatusPill :status="engine.active ? 'ready' : 'unavailable'" />
            </header>

            <p class="engine-card__author">{{ engine.author ? `By ${engine.author}` : 'Unknown author' }}</p>

            <dl class="engine-card__stats">
              <div>
                <dt>Games</dt>
                <dd>{{ engine.record.games.toLocaleString() }}</dd>
              </div>
              <div>
                <dt>Score</dt>
                <dd>{{ scoreLabel(engine) }}</dd>
              </div>
              <div>
                <dt>W–D–L</dt>
                <dd>{{ recordLabel(engine) }}</dd>
              </div>
            </dl>

            <div class="engine-card__source">
              <span>{{ sourceLabel(engine) }}</span>
              <code :title="engine.source_ref">{{ engine.source_ref || 'Unknown reference' }}</code>
            </div>

            <footer class="engine-card__footer">
              <span :title="engine.repository_full_name">{{ engine.repository_full_name || 'Repository unavailable' }}</span>
              <span class="engine-card__open">View engine <AppIcon name="arrow-right" :size="16" /></span>
            </footer>
          </article>
        </div>

        <ContentState
          v-else
          kind="empty"
          compact
          title="No matching engines"
          message="Try a different search or clear the filters."
          action-label="Clear filters"
          @action="clearFilters"
        />
      </section>
    </template>
  </div>
</template>

<style scoped>
.engines-page {
  display: grid;
  gap: var(--space-8);
  padding-block: clamp(1.2rem, 2.5vw, 2.25rem) 3rem;
}

.engines-heading {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: var(--space-8);
  padding-block-end: var(--space-6);
  border-block-end: 1px solid var(--color-border);
}

.engines-heading h1,
.engines-heading p,
.directory__heading h2,
.directory__heading p,
.engine-card h3,
.engine-card p,
.engine-card dl {
  margin: 0;
}

.engines-heading h1 {
  font-size: clamp(2rem, 5vw, 3.5rem);
  letter-spacing: -0.04em;
  line-height: 1;
}

.engines-heading > div > p:last-child {
  max-width: 40rem;
  margin-block-start: 0.7rem;
  color: var(--color-text-muted);
  font-size: 0.95rem;
}

.eyebrow {
  margin-block-end: 0.45rem !important;
  color: var(--color-accent);
  font-size: 0.68rem;
  font-weight: 780;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.engine-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  overflow: hidden;
  margin: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-border);
  gap: 1px;
}

.engine-stats > div {
  min-width: 0;
  padding: clamp(1rem, 2vw, 1.35rem);
  background: var(--color-surface);
}

.engine-stats dt {
  color: var(--color-text-muted);
  font-size: 0.68rem;
  font-weight: 720;
  letter-spacing: 0.045em;
  text-transform: uppercase;
}

.engine-stats dd {
  margin: 0.35rem 0 0;
  font-size: clamp(1.35rem, 2.8vw, 2rem);
  font-weight: 760;
  letter-spacing: -0.035em;
  line-height: 1;
}

.directory {
  display: grid;
  gap: var(--space-5);
}

.directory__heading {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: var(--space-4);
}

.directory__heading h2 {
  font-size: 1.2rem;
}

.directory__heading p {
  margin-block-start: 0.25rem;
  color: var(--color-text-muted);
  font-size: 0.75rem;
}

.clear-button {
  border: 0;
  background: transparent;
  color: var(--color-accent);
  font: inherit;
  font-size: 0.78rem;
  font-weight: 720;
  cursor: pointer;
}

.clear-button:hover {
  text-decoration: underline;
  text-underline-offset: 0.18em;
}

.filters {
  display: grid;
  grid-template-columns: minmax(16rem, 2fr) repeat(3, minmax(9rem, 0.7fr));
  align-items: end;
  gap: var(--space-3);
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
}

.filter-field {
  display: grid;
  gap: var(--space-2);
  color: var(--color-text);
  font-size: 0.8125rem;
  font-weight: 640;
}

.filter-field select {
  width: 100%;
  min-width: 0;
  min-height: var(--control-height);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
  padding: 0.6rem 2rem 0.6rem 0.75rem;
  color: var(--color-text);
  font: inherit;
  font-weight: 500;
}

.filter-field select:focus {
  border-color: var(--color-focus);
  outline: 0;
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-focus) 20%, transparent);
}

.engine-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 20rem), 1fr));
  gap: var(--space-4);
}

.engine-card {
  position: relative;
  display: grid;
  align-content: start;
  gap: var(--space-4);
  min-width: 0;
  padding: var(--space-5);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  box-shadow: var(--shadow-xs);
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast), transform var(--transition-fast);
}

.engine-card:hover {
  border-color: color-mix(in srgb, var(--color-accent) 55%, var(--color-border));
  box-shadow: var(--shadow-sm);
  transform: translateY(-2px);
}

.engine-card:has(.engine-card__link:focus-visible) {
  border-color: var(--color-focus);
  outline: 3px solid color-mix(in srgb, var(--color-focus) 24%, transparent);
  outline-offset: 2px;
}

.engine-card__link {
  position: absolute;
  z-index: 1;
  inset: 0;
  border-radius: inherit;
}

.engine-card__heading {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--space-3);
}

.engine-card__mark {
  display: grid;
  width: 2.6rem;
  height: 2.6rem;
  place-items: center;
  border-radius: var(--radius-md);
  background: var(--color-accent-soft);
  color: var(--color-accent);
}

.engine-card__heading h3 {
  overflow: hidden;
  font-size: 1rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.engine-card__heading p {
  overflow: hidden;
  margin-block-start: 0.18rem;
  color: var(--color-text-muted);
  font-size: 0.72rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.engine-card__author {
  overflow: hidden;
  color: var(--color-text-secondary);
  font-size: 0.78rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.engine-card__stats {
  display: grid;
  grid-template-columns: 0.8fr 0.75fr 1.2fr;
  padding-block: var(--space-3);
  border-block: 1px solid var(--color-border);
}

.engine-card__stats > div {
  min-width: 0;
  padding-inline: var(--space-3);
}

.engine-card__stats > div:first-child {
  padding-inline-start: 0;
}

.engine-card__stats > div + div {
  border-inline-start: 1px solid var(--color-border);
}

.engine-card__stats dt {
  color: var(--color-text-muted);
  font-size: 0.6rem;
  font-weight: 720;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.engine-card__stats dd {
  overflow: hidden;
  margin: 0.25rem 0 0;
  font-size: 0.85rem;
  font-weight: 760;
  font-variant-numeric: tabular-nums;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.engine-card__source {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: var(--space-2);
}

.engine-card__source > span {
  flex: 0 0 auto;
  padding: 0.2rem 0.45rem;
  border-radius: var(--radius-round);
  background: var(--color-surface-sunken);
  color: var(--color-text-muted);
  font-size: 0.62rem;
  font-weight: 740;
  letter-spacing: 0.035em;
  text-transform: uppercase;
}

.engine-card__source code {
  overflow: hidden;
  min-width: 0;
  border: 0;
  background: transparent;
  padding: 0;
  color: var(--color-text-secondary);
  font-size: 0.7rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.engine-card__footer {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-block-start: auto;
  color: var(--color-text-muted);
  font-size: 0.68rem;
}

.engine-card__footer > span:first-child {
  overflow: hidden;
  min-width: 0;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.engine-card__open {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: var(--space-1);
  color: var(--color-accent);
  font-weight: 720;
}

@media (max-width: 62rem) {
  .filters {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .search-field {
    grid-column: 1 / -1;
  }
}

@media (max-width: 46rem) {
  .engine-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .filters {
    grid-template-columns: 1fr;
  }

  .search-field {
    grid-column: auto;
  }
}

@media (max-width: 30rem) {
  .engine-card__heading {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .engine-card__heading .status-pill {
    grid-column: 1 / -1;
    justify-self: start;
  }
}
</style>
