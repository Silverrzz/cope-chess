<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '@/api/client'
import ContentState from '@/components/public/ContentState.vue'
import { errorMessage, formatDate } from '@/components/public/format'
import RatingListPgnModal from '@/components/public/RatingListPgnModal.vue'
import type { EngineRecord, Identifier } from '@/components/public/types'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { bestVersionRatings } from '@/utils/ratings'

interface RatingListRecord {
  id: Identifier
  name: string
}

interface RatingRecord {
  engine: EngineRecord
  elo?: number | null
  error_margin?: number | null
  games_played: number
  average_opponent_elo_delta?: number | null
  updated_at?: string | null
}

interface RatingsResponse {
  rating_list: RatingListRecord | null
  rating_lists: RatingListRecord[]
  ratings: RatingRecord[]
  filter_options: { opponent_ids_by_engine: Record<string, number[]> }
}

const route = useRoute()
const router = useRouter()
const data = ref<RatingsResponse | null>(null)
const loading = ref(true)
const loadError = ref('')
const onlyBestVersions = ref(true)
const pgnModalOpen = ref(false)
let controller: AbortController | null = null

const routeRatingList = computed(() => {
  const value = Array.isArray(route.query.rating_list_id) ? route.query.rating_list_id[0] : route.query.rating_list_id
  return value ? String(value) : ''
})
const selectedRatingList = computed(() => routeRatingList.value || String(data.value?.rating_list?.id || ''))
const displayedRatings = computed(() => {
  const ratings = data.value?.ratings ?? []
  return onlyBestVersions.value ? bestVersionRatings(ratings) : ratings
})
const representedGames = computed(() => Math.max(0, Math.round((data.value?.ratings ?? []).reduce((sum, row) => sum + row.games_played, 0) / 2)))
const pgnEngineOptions = computed(() => (data.value?.ratings ?? []).map((row) => ({
  id: engineId(row.engine),
  name: row.engine.name,
  version: row.engine.version || '',
  games: row.games_played,
})))

onMounted(load)
onBeforeUnmount(() => controller?.abort())
watch(routeRatingList, (next, previous) => {
  if (next !== previous) {
    pgnModalOpen.value = false
    void load()
  }
})

async function load(): Promise<void> {
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  loadError.value = ''
  try {
    data.value = await api.get<RatingsResponse>('/api/ratings', {
      query: routeRatingList.value ? { rating_list_id: routeRatingList.value } : {},
      signal: controller.signal,
    })
  } catch (error) {
    if ((error as { name?: string })?.name !== 'AbortError') {
      loadError.value = errorMessage(error, 'Ratings could not be loaded.')
    }
  } finally {
    loading.value = false
  }
}

function engineId(engine: EngineRecord): Identifier {
  return engine.engine_id ?? engine.id ?? ''
}

function ratingLabel(value?: number | null): string {
  return value === null || value === undefined ? '-' : Math.round(value).toLocaleString()
}

function signedRating(value?: number | null): string {
  if (value === null || value === undefined) return '-'
  const rounded = Math.round(value)
  return `${rounded > 0 ? '+' : ''}${rounded.toLocaleString()}`
}

function selectRatingList(event: Event): void {
  const ratingListId = (event.target as HTMLSelectElement).value
  void router.push({ path: '/ratings', query: ratingListId ? { rating_list_id: ratingListId } : {} })
}
</script>

<template>
  <div class="page-container ratings-page">
    <ContentState v-if="loading && !data" kind="loading" title="Loading ratings" />
    <ContentState v-else-if="loadError && !data" kind="error" :message="loadError" action-label="Try again" @action="load" />

    <template v-else-if="data">
      <header class="ratings-heading">
        <div>
          <h1>Ratings</h1>
        </div>
        <div class="ratings-controls">
          <BaseButton size="small" :disabled="!data.rating_list || !representedGames" @click="pgnModalOpen = true"><template #icon><AppIcon name="download" :size="15" /></template>Download PGNs</BaseButton>
          <label class="best-versions-filter"><input v-model="onlyBestVersions" type="checkbox"> Only Best Versions</label>
          <label v-if="data.rating_lists.length" class="category-picker">
            <span>Rating list</span>
            <select :value="selectedRatingList" @change="selectRatingList">
              <option v-for="ratingList in data.rating_lists" :key="ratingList.id" :value="String(ratingList.id)">{{ ratingList.name }}</option>
            </select>
          </label>
        </div>
      </header>

      <section v-if="data.rating_list" class="category-overview" aria-label="Selected rating list summary">
        <div class="category-overview__copy">
          <span>Selected list</span>
          <h2>{{ data.rating_list.name }}</h2>
        </div>
        <dl>
          <div><dt>Rating list</dt><dd>{{ data.rating_list.name }}</dd></div>
          <div><dt>Rated engines</dt><dd>{{ displayedRatings.length }}</dd></div>
          <div><dt>Games represented</dt><dd>{{ representedGames }}</dd></div>
        </dl>
      </section>

      <section class="ratings-panel" :aria-busy="loading">
        <div v-if="loading" class="refreshing" role="status"><span aria-hidden="true"></span>Updating ratings</div>
        <div v-if="loadError" class="inline-error" role="alert">{{ loadError }} <button type="button" @click="load">Try again</button></div>

        <div v-if="displayedRatings.length" class="ratings-table-wrap">
          <table class="ratings-table">
            <caption class="sr-only">{{ data.rating_list?.name }} engine ratings</caption>
            <thead>
              <tr>
                <th class="rank-column">Rank</th>
                <th>Engine</th>
                <th>Rating</th>
                <th>95% error</th>
                <th>Opponent delta</th>
                <th>Games</th>
                <th>Updated</th>
                <th><span class="sr-only">View engine</span></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, index) in displayedRatings" :key="engineId(row.engine)">
                <td class="rank-column" data-label="Rank"><span>{{ index + 1 }}</span></td>
                <td data-label="Engine"><RouterLink :to="`/engines/${engineId(row.engine)}`">{{ row.engine.name }} <small>{{ row.engine.version }}</small></RouterLink></td>
                <td class="rating-column" data-label="Rating">{{ ratingLabel(row.elo) }}</td>
                <td class="error-column" data-label="95% error">
                  <span v-if="row.error_margin !== null && row.error_margin !== undefined" class="error-estimate" :title="`Approximate 95% interval: ${ratingLabel((row.elo ?? 0) - row.error_margin)} to ${ratingLabel((row.elo ?? 0) + row.error_margin)}`">
                    <strong>±{{ ratingLabel(row.error_margin) }}</strong>
                  </span>
                  <span v-else>-</span>
                </td>
                <td class="delta-column" data-label="Opponent delta" :class="{ 'delta-column--positive': (row.average_opponent_elo_delta ?? 0) > 0, 'delta-column--negative': (row.average_opponent_elo_delta ?? 0) < 0 }">{{ signedRating(row.average_opponent_elo_delta) }}</td>
                <td data-label="Games">{{ row.games_played.toLocaleString() }}</td>
                <td class="date-column" data-label="Updated">{{ formatDate(row.updated_at) }}</td>
                <td class="action-column"><RouterLink :to="`/engines/${engineId(row.engine)}`" :aria-label="`View ${row.engine.name}`">View</RouterLink></td>
              </tr>
            </tbody>
          </table>
        </div>
        <ContentState
          v-else
          kind="empty"
          compact
          :title="data.rating_list ? 'No ratings yet' : 'No rating lists'"
        />
      </section>

      <RatingListPgnModal v-if="data.rating_list" :open="pgnModalOpen" :rating-list="data.rating_list" :engines="pgnEngineOptions" :opponent-ids-by-engine="data.filter_options.opponent_ids_by_engine" @close="pgnModalOpen = false" />
    </template>
  </div>
</template>

<style scoped>
.ratings-page {
  display: grid;
  gap: var(--space-xl, 2rem);
  padding-block: clamp(1.2rem, 2.5vw, 2.25rem) 3rem;
}

.ratings-heading {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: var(--space-xl, 2rem);
  padding-block-end: var(--space-lg, 1.5rem);
  border-block-end: 1px solid var(--color-border, #d5dbe1);
}

.ratings-heading h1,
.ratings-heading p,
.category-overview h2,
.category-overview p,
.category-overview dl {
  margin: 0;
}

.ratings-heading h1 {
  font-size: clamp(2rem, 5vw, 3.5rem);
  letter-spacing: -0.04em;
  line-height: 1;
}

.ratings-controls {
  display: flex;
  align-items: end;
  justify-content: flex-end;
  gap: 0.9rem;
}

.best-versions-filter {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  min-height: 2.55rem;
  color: var(--color-text-muted, #607080);
  cursor: pointer;
  font-size: 0.72rem;
  font-weight: 700;
  white-space: nowrap;
}

.category-picker {
  display: grid;
  gap: 0.3rem;
  min-width: min(22rem, 38vw);
  color: var(--color-text-muted, #607080);
  font-size: 0.66rem;
  font-weight: 750;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.category-picker select {
  width: 100%;
  min-height: 2.55rem;
  padding: 0.5rem 2rem 0.5rem 0.7rem;
  border: 1px solid var(--color-border, #d5dbe1);
  border-radius: var(--radius-md, 0.5rem);
  background: var(--color-surface, #fff);
  color: var(--color-text, #17202a);
  font: inherit;
  font-size: 0.82rem;
  font-weight: 700;
  letter-spacing: normal;
  text-transform: none;
}

.category-overview {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: var(--space-lg, 1.5rem);
  padding: 1rem;
  border: 1px solid var(--color-border, #d5dbe1);
  border-radius: var(--radius-lg, 0.75rem);
  background: var(--color-surface, #fff);
}

.category-overview__copy > span {
  color: var(--color-accent, #2f78c4);
  font-size: 0.62rem;
  font-weight: 780;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.category-overview h2 {
  margin-block-start: 0.2rem;
  font-size: 1.2rem;
}

.category-overview__copy p {
  max-width: 38rem;
  margin-block-start: 0.25rem;
  color: var(--color-text-muted, #607080);
  font-size: 0.78rem;
}

.category-overview dl {
  display: flex;
  gap: 1px;
  overflow: hidden;
  border: 1px solid var(--color-border, #d5dbe1);
  border-radius: var(--radius-md, 0.5rem);
  background: var(--color-border, #d5dbe1);
}

.category-overview dl div {
  min-width: 7rem;
  padding: 0.72rem 0.85rem;
  background: var(--color-surface, #fff);
}

.category-overview dt {
  color: var(--color-text-muted, #607080);
  font-size: 0.6rem;
  font-weight: 750;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.category-overview dd {
  overflow: hidden;
  margin: 0.2rem 0 0;
  font-size: 0.95rem;
  font-weight: 760;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ratings-panel {
  position: relative;
  overflow: hidden;
  border: 1px solid var(--color-border, #d5dbe1);
  border-radius: var(--radius-lg, 0.75rem);
  background: var(--color-surface, #fff);
}

.refreshing {
  position: absolute;
  inset-block-start: 0.5rem;
  inset-inline-end: 0.75rem;
  display: flex;
  align-items: center;
  gap: 0.35rem;
  color: var(--color-text-muted, #607080);
  font-size: 0.68rem;
}

.refreshing span {
  width: 0.5rem;
  height: 0.5rem;
  border: 2px solid color-mix(in srgb, var(--color-accent, #2f78c4) 25%, transparent);
  border-block-start-color: var(--color-accent, #2f78c4);
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}

.inline-error {
  padding: 0.65rem 0.85rem;
  border-block-end: 1px solid color-mix(in srgb, var(--color-danger, #b42318) 22%, transparent);
  background: color-mix(in srgb, var(--color-danger, #b42318) 7%, transparent);
  color: var(--color-danger, #b42318);
  font-size: 0.75rem;
}

.inline-error button {
  padding: 0;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  font-weight: 750;
  text-decoration: underline;
  cursor: pointer;
}

.ratings-table-wrap {
  overflow-x: auto;
}

.ratings-table {
  width: 100%;
  min-width: 68rem;
  border-collapse: collapse;
  font-size: 0.86rem;
}

.ratings-table th,
.ratings-table td {
  padding: 0.85rem 1rem;
  border-block-end: 1px solid var(--color-border, #d5dbe1);
  text-align: start;
}

.ratings-table th {
  color: var(--color-text-muted, #607080);
  font-size: 0.66rem;
  letter-spacing: 0.045em;
  text-transform: uppercase;
}

.ratings-table tbody tr:last-child td { border-block-end: 0; }
.ratings-table tbody tr:hover { background: color-mix(in srgb, var(--color-accent, #2f78c4) 4.5%, transparent); }

.ratings-table a {
  color: inherit;
  font-weight: 700;
  text-decoration: none;
}

.ratings-table a:hover {
  color: var(--color-accent, #2f78c4);
  text-decoration: underline;
  text-underline-offset: 0.16em;
}

.rank-column { width: 4.2rem; text-align: center !important; }
.rank-column span {
  display: inline-grid;
  width: 1.8rem;
  height: 1.8rem;
  place-items: center;
  border-radius: 50%;
  background: color-mix(in srgb, var(--color-text, #17202a) 6%, transparent);
  font-size: 0.74rem;
  font-weight: 760;
}

.rating-column { font-size: 1rem; font-weight: 800; font-variant-numeric: tabular-nums; }
.error-column { min-width: 7rem; }
.error-estimate { color: var(--color-text-muted, #607080); font-variant-numeric: tabular-nums; }
.error-estimate strong { color: var(--color-text, #17202a); font-size: 0.76rem; }
.delta-column { font-weight: 720; font-variant-numeric: tabular-nums; }
.delta-column--positive { color: var(--color-success, #24865a); }
.delta-column--negative { color: var(--color-danger, #b42318); }
.date-column { color: var(--color-text-muted, #607080); font-size: 0.78rem; }
.action-column { width: 4rem; text-align: end !important; }
.action-column a { color: var(--color-accent, #2f78c4); font-size: 0.75rem; }

@keyframes spin { to { transform: rotate(360deg); } }

@media (max-width: 52rem) {
  .ratings-heading { align-items: stretch; flex-direction: column; }
  .ratings-controls { align-items: stretch; justify-content: flex-start; }
  .category-picker { min-width: 0; }
  .category-overview { align-items: stretch; flex-direction: column; }
  .category-overview dl { align-self: stretch; }
  .category-overview dl div { min-width: 0; flex: 1; }
}

@media (max-width: 38rem) {
  .ratings-controls { flex-direction: column; }
  .best-versions-filter { min-height: auto; }
  .category-overview dl { display: grid; grid-template-columns: repeat(2, 1fr); }
  .category-overview dl div:first-child { grid-column: 1 / -1; }
}
</style>
