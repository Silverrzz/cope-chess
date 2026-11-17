<script setup lang="ts">
import { tournamentPath } from '@/router/publicUrls'
import { engineVersionLabel } from '@/components/public/format'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import ChessBoard from '@/components/chess/ChessBoard.vue'
import ContentState from '@/components/public/ContentState.vue'
import DonorRankings from '@/components/public/DonorRankings.vue'
import GameTable from '@/components/public/GameTable.vue'
import ProgressBar from '@/components/public/ProgressBar.vue'
import SpectatorCount from '@/components/public/SpectatorCount.vue'
import StatusPill from '@/components/public/StatusPill.vue'
import { errorMessage, formatDate } from '@/components/public/format'
import type { GameRecord, MoveRecord, OpeningRecord, TournamentSummary } from '@/components/public/types'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { useTournamentSpectators } from '@/composables/useTournamentSpectators'

interface GamePreview {
  game: GameRecord
  moves: MoveRecord[]
  opening?: OpeningRecord | null
  last_move?: MoveRecord | null
  white_name: string
  black_name: string
}

interface RunningTournamentCard {
  tournament: TournamentSummary
  preview?: GamePreview | null
}

interface UpcomingRow {
  game_id: number
  href: string
  tournament: string
  round: string
  white: string
  black: string
  status: string
  scheduled_start_at?: string | null
}

interface AcknowledgedQueueItem {
  id: number
  name: string
  version: string
}

interface WaitingQueueItem {
  id: number
  engine_name: string
  engine_version: string
  rating_list_name: string
}

interface EngineQueues {
  acknowledged: AcknowledgedQueueItem[]
  waiting_for_test: WaitingQueueItem[]
}

interface HomeResponse {
  connected_workers: number
  connected_cores: number
  live_games: number
  running_tournaments: RunningTournamentCard[]
  upcoming_rows: UpcomingRow[]
  recent_games: GameRecord[]
  engines: Record<string, string>
  tournament_names: Record<string, string>
  engine_queues: EngineQueues
}

const data = ref<HomeResponse | null>(null)
const { spectatorCount } = useTournamentSpectators()
const loading = ref(true)
const loadError = ref('')
let controller: AbortController | null = null

const UPCOMING_PREVIEW_COUNT = 4
const RESULTS_PREVIEW_COUNT = 6
const count = new Intl.NumberFormat()
const showAllUpcoming = ref(false)
const showAllResults = ref(false)

const upcomingGames = computed(() => {
  const rows = data.value?.upcoming_rows ?? []
  return showAllUpcoming.value ? rows : rows.slice(0, UPCOMING_PREVIEW_COUNT)
})
const recentGames = computed(() => {
  const games = data.value?.recent_games ?? []
  return showAllResults.value ? games : games.slice(0, RESULTS_PREVIEW_COUNT)
})
const queueGroups = computed(() => {
  const queues = data.value?.engine_queues
  if (!queues) return []
  return [
    {
      title: 'Waiting for test',
      open: true,
      empty: 'No engine tests are currently waiting.',
      items: queues.waiting_for_test.map(item => ({
        id: item.id,
        name: item.engine_name,
        version: engineVersionLabel(item.engine_version),
        ratingList: item.rating_list_name,
      })),
    },
    {
      title: 'Recognised engines',
      open: !queues.waiting_for_test.length,
      empty: 'No engines are currently queued for acknowledgement.',
      items: queues.acknowledged.map(item => ({
        id: item.id,
        name: item.name,
        version: engineVersionLabel(item.version),
        ratingList: '',
      })),
    },
  ]
})

onMounted(load)
onBeforeUnmount(() => controller?.abort())

async function load(): Promise<void> {
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  loadError.value = ''
  try {
    data.value = await api.get<HomeResponse>('/api/home', { signal: controller.signal })
  } catch (error) {
    if ((error as { name?: string })?.name !== 'AbortError') {
      loadError.value = errorMessage(error, 'The live overview could not be loaded.')
    }
  } finally {
    loading.value = false
  }
}

function progress(item: TournamentSummary): number {
  if (item.progress_percent != null) return item.progress_percent
  return item.summary.total ? Math.round((item.summary.finished || 0) / item.summary.total * 100) : 0
}
</script>

<template>
  <div class="page-container home-page">
    <header class="home-heading">
      <h1>COPE Chess</h1>
      <dl v-if="data" class="home-stats panel" aria-label="Platform activity">
        <div><dt>Live games</dt><dd>{{ count.format(data.live_games) }}</dd></div>
        <div><dt>Workers</dt><dd>{{ count.format(data.connected_workers) }}</dd></div>
        <div><dt>Cores</dt><dd>{{ count.format(data.connected_cores) }}</dd></div>
      </dl>
    </header>

    <ContentState v-if="loading" kind="loading" compact title="Loading tournament activity" />
    <ContentState v-else-if="loadError" kind="error" compact :message="loadError" action-label="Try again" @action="load" />

    <template v-else-if="data">
      <div class="home-dashboard">
        <div class="home-column">
          <section class="section-stack" aria-labelledby="running-tournaments-title">
            <header class="home-section-heading">
              <h2 id="running-tournaments-title">Running tournaments</h2>
              <RouterLink class="home-section-link" to="/tournaments">All tournaments</RouterLink>
            </header>

            <div v-if="data.running_tournaments.length" class="home-tournaments">
              <article
                v-for="item in data.running_tournaments" :key="item.tournament.record.id"
                class="panel panel__body live-card" :class="{ 'live-card--preview': item.preview }"
              >
                <div v-if="item.preview" class="live-card__board">
                  <ChessBoard
                    :variant="item.tournament.record.config?.variant"
                    :fen="item.preview.opening?.fen || 'startpos'"
                    :moves="item.preview.moves.map((move) => move.uci)"
                    :controls="false"
                    :coordinates="false"
                    compact
                    :label="`${item.preview.white_name} versus ${item.preview.black_name} position`"
                  />
                </div>

                <div class="live-card__body">
                  <header class="live-card__heading">
                    <div>
                      <h3><RouterLink :to="tournamentPath(item.tournament.record.id)">{{ item.tournament.record.name }}</RouterLink></h3>
                      <p class="muted">{{ item.tournament.format || 'Tournament' }} · {{ item.tournament.time_control || 'Time control not set' }}</p>
                    </div>
                    <div class="live-card__status">
                      <StatusPill :status="item.tournament.record.status" />
                      <SpectatorCount :count="spectatorCount(item.tournament.record.id, item.tournament.spectator_count)" />
                    </div>
                  </header>

                  <div v-if="item.preview" class="live-card__match">
                    <p>{{ item.preview.white_name }} <span class="muted">vs</span> {{ item.preview.black_name }}</p>
                    <small v-if="item.preview.game.round != null" class="muted">Round {{ item.preview.game.round }}</small>
                  </div>
                  <p v-else class="muted">Waiting for the next game to start.</p>

                  <ProgressBar :value="progress(item.tournament)" :label="`${count.format(item.tournament.summary.finished || 0)} of ${count.format(item.tournament.summary.total)} games finished`" />

                  <footer class="live-card__footer">
                    <p class="muted">
                      {{ count.format(item.tournament.summary.live || 0) }} live · {{ count.format(item.tournament.summary.pairs || 0) }} pairs
                      <template v-if="item.preview?.last_move?.san"> · Last move {{ item.preview.last_move.san }}</template>
                      <template v-else-if="!item.preview && item.tournament.record.current_round"> · Round {{ item.tournament.record.current_round }}</template>
                    </p>
                    <BaseButton
                      size="small"
                      :to="item.preview
                        ? tournamentPath(item.tournament.record.id, { gameId: item.preview.game.id })
                        : tournamentPath(item.tournament.record.id)"
                    >
                      {{ item.preview ? 'Watch game' : 'Open tournament' }}
                      <template #trailing><AppIcon name="arrow-right" :size="14" /></template>
                    </BaseButton>
                  </footer>
                </div>
              </article>
            </div>
            <p v-else class="panel panel__body home-empty muted">No tournaments are running right now.</p>
          </section>

          <section class="panel home-panel" aria-labelledby="home-donors-title">
            <header class="home-section-heading home-panel__header">
              <div>
                <h2 id="home-donors-title">Donors</h2>
                <p class="muted home-caption">Ranked by estimated CPU hours</p>
              </div>
            </header>
            <DonorRankings compact />
          </section>
        </div>

        <div class="home-column">
          <section class="panel home-panel" aria-labelledby="upcoming-title">
            <header class="home-section-heading home-panel__header">
              <h2 id="upcoming-title">Upcoming games</h2>
            </header>
            <div v-if="data.upcoming_rows.length" id="home-upcoming-list">
              <RouterLink v-for="row in upcomingGames" :key="row.game_id" :to="row.href" class="upcoming-row">
                <div>
                  <strong>{{ row.white }} <span class="muted">vs</span> {{ row.black }}</strong>
                  <small class="muted">
                    {{ row.tournament }} ·
                    {{ row.status === 'scheduled' && row.scheduled_start_at ? formatDate(row.scheduled_start_at, true) : `Round ${row.round}` }}
                  </small>
                </div>
                <StatusPill :status="row.status" />
                <AppIcon class="muted" name="chevron-right" :size="15" />
              </RouterLink>
            </div>
            <p v-else class="home-empty muted">No upcoming games.</p>
            <footer v-if="data.upcoming_rows.length > UPCOMING_PREVIEW_COUNT" class="home-panel__footer">
              <BaseButton
                size="small" variant="ghost" :aria-expanded="showAllUpcoming" aria-controls="home-upcoming-list"
                @click="showAllUpcoming = !showAllUpcoming"
              >
                {{ showAllUpcoming ? 'Show fewer games' : `Show all ${data.upcoming_rows.length} upcoming games` }}
              </BaseButton>
            </footer>
          </section>

          <section class="panel home-panel" aria-labelledby="engine-queues-title">
            <header class="home-section-heading home-panel__header">
              <h2 id="engine-queues-title">Engine queues</h2>
            </header>
            <details v-for="queue in queueGroups" :key="queue.title" class="queue-group" :open="queue.open">
              <summary>
                <span>{{ queue.title }}</span>
                <span class="queue-group__count">{{ count.format(queue.items.length) }}</span>
                <AppIcon name="chevron-down" :size="15" />
              </summary>
              <ol v-if="queue.items.length" class="queue-group__list">
                <li v-for="item in queue.items" :key="item.id">
                  <strong>{{ item.name }}<span v-if="item.version" class="muted">{{ item.version }}</span></strong>
                  <small v-if="item.ratingList" class="muted">{{ item.ratingList }}</small>
                </li>
              </ol>
              <p v-else class="queue-group__empty muted">{{ queue.empty }}</p>
            </details>
            <details class="queue-policy">
              <summary>Queue policy</summary>
              <p>Engine code and data must be original and human-written. The recognised engine queue shows planned additions, and its order may change.</p>
              <p>Engines are tested in every rating list they qualify for. Lower-priority lists and engines with frequent releases may take longer. For hardware donations and faster testing, contact <strong>@silverrzz</strong> on Discord.</p>
            </details>
          </section>
        </div>
      </div>

      <section class="panel home-panel" aria-labelledby="recent-title">
        <header class="home-section-heading home-panel__header">
          <h2 id="recent-title">Recent results</h2>
        </header>
        <GameTable
          v-if="data.recent_games.length" id="home-recent-results"
          :games="recentGames"
          :engines="data.engines"
          :tournament-names="data.tournament_names"
          show-tournament
          show-round
          caption="Recent tournament results"
        />
        <p v-else class="home-empty muted">No recent results.</p>
        <footer v-if="data.recent_games.length > RESULTS_PREVIEW_COUNT" class="home-panel__footer">
          <BaseButton
            size="small" variant="ghost" :aria-expanded="showAllResults" aria-controls="home-recent-results"
            @click="showAllResults = !showAllResults"
          >
            {{ showAllResults ? 'Show fewer results' : `Show all ${data.recent_games.length} results` }}
          </BaseButton>
        </footer>
      </section>
    </template>
  </div>
</template>

<style scoped>
.home-page {
  display: grid;
  gap: var(--space-8);
}

.home-heading {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: var(--space-4);
}

.home-heading h1 {
  font-size: clamp(2rem, 4.5vw, 3.4rem);
}

.home-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  min-width: min(100%, 24rem);
}

.home-stats > div {
  display: flex;
  flex-direction: column-reverse;
  gap: var(--space-1);
  padding: var(--space-3) var(--space-4);
}

.home-stats > div + div {
  border-inline-start: 1px solid var(--color-border);
}

.home-stats dt {
  color: var(--color-text-muted);
  font-size: 0.75rem;
}

.home-stats dd {
  font-size: clamp(1.25rem, 3vw, 1.75rem);
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}

.home-dashboard {
  display: grid;
  grid-template-columns: minmax(0, 1.65fr) minmax(0, 1fr);
  align-items: start;
  gap: var(--space-6);
}

.home-column {
  display: grid;
  min-width: 0;
  gap: var(--space-6);
}

.home-panel {
  min-width: 0;
}

.home-section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.home-section-heading h2 {
  font-size: 1.16rem;
  text-wrap: wrap;
}

.home-section-link {
  color: var(--color-accent);
  font-size: 0.85rem;
  font-weight: 650;
  text-decoration: none;
  text-align: end;
}

.home-section-link:hover {
  text-decoration: underline;
}

.home-panel__header,
.home-panel__footer {
  padding: var(--space-4);
}

.home-panel__header {
  border-block-end: 1px solid var(--color-border);
}

.home-panel__footer {
  display: flex;
  justify-content: center;
  padding-block: var(--space-2);
  border-block-start: 1px solid var(--color-border);
}

.home-caption {
  margin-block-start: var(--space-1);
  font-size: 0.8rem;
}

.home-tournaments {
  display: grid;
  gap: var(--space-3);
}

.live-card {
  display: grid;
  align-items: center;
  gap: var(--space-5);
}

.live-card--preview {
  grid-template-columns: minmax(7rem, 0.8fr) minmax(0, 1.45fr);
}

.live-card__board {
  width: min(100%, 14rem);
  justify-self: center;
}

.live-card__body {
  display: grid;
  min-width: 0;
  gap: var(--space-3);
  overflow-wrap: anywhere;
}

.live-card__heading,
.live-card__footer {
  display: flex;
  align-items: start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
}

.live-card__heading h3 {
  font-size: clamp(1.06rem, 1.6vw, 1.32rem);
  text-wrap: wrap;
}

.live-card__heading h3 a {
  text-decoration: none;
}

.live-card__heading h3 a:hover {
  color: var(--color-accent);
}

.live-card__heading p {
  margin-block-start: var(--space-1);
}

.live-card__status {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.live-card__body > p,
.live-card__heading p,
.live-card__footer p {
  font-size: 0.8rem;
}

.live-card__match p {
  font-size: 0.94rem;
  font-weight: 650;
}

.live-card__footer {
  align-items: center;
}

.upcoming-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  text-decoration: none;
  overflow-wrap: anywhere;
}

.upcoming-row + .upcoming-row {
  border-block-start: 1px solid var(--color-border);
}

.upcoming-row:hover,
.upcoming-row:focus-visible {
  background: var(--color-surface-hover);
}

.upcoming-row:focus-visible {
  outline-offset: -3px;
}

.upcoming-row strong,
.upcoming-row small {
  display: block;
  font-size: 0.84rem;
}

.upcoming-row small {
  font-size: 0.75rem;
}

.queue-group,
.queue-policy {
  border-block-end: 1px solid var(--color-border);
  font-size: 0.85rem;
}

.queue-group summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  font-weight: 650;
  cursor: pointer;
}

.queue-group summary:hover {
  background: var(--color-surface-hover);
}

.queue-group summary::-webkit-details-marker {
  display: none;
}

.queue-group summary svg {
  color: var(--color-text-muted);
}

.queue-group[open] summary svg {
  transform: rotate(180deg);
}

.queue-group__count {
  min-width: 2ch;
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
  text-align: end;
}

.queue-group__list {
  padding: 0 var(--space-4) var(--space-3) var(--space-10);
  overflow-wrap: anywhere;
}

.queue-group__list li {
  padding-block: var(--space-1);
}

.queue-group__list li::marker {
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}

.queue-group__list strong > span {
  margin-inline-start: var(--space-1);
  font-weight: 500;
}

.queue-group__list small {
  display: block;
}

.queue-group__empty {
  padding: 0 var(--space-4) var(--space-3);
  font-size: 0.8rem;
}

.queue-policy {
  border-block-end: 0;
  padding: var(--space-3) var(--space-4);
  color: var(--color-text-muted);
  font-size: 0.8rem;
}

.queue-policy summary {
  cursor: pointer;
}

.queue-policy p {
  margin-block-start: var(--space-2);
}

.home-empty {
  padding: var(--space-6) var(--space-4);
  text-align: center;
  font-size: 0.88rem;
}

@media (max-width: 56rem) {
  .home-dashboard {
    grid-template-columns: minmax(0, 1fr);
  }

  .home-page {
    gap: var(--space-6);
  }
}

@media (max-width: 36rem) {
  .home-heading {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 28rem) {
  .live-card--preview {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
