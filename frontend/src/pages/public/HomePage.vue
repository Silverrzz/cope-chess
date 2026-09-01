<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import ChessBoard from '@/components/chess/ChessBoard.vue'
import ContentState from '@/components/public/ContentState.vue'
import GameTable from '@/components/public/GameTable.vue'
import ProgressBar from '@/components/public/ProgressBar.vue'
import SpectatorCount from '@/components/public/SpectatorCount.vue'
import StatusPill from '@/components/public/StatusPill.vue'
import { errorMessage, formatDate } from '@/components/public/format'
import type { GameRecord, MoveRecord, OpeningRecord, TournamentSummary } from '@/components/public/types'
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
    <ContentState v-if="loading" kind="loading" title="Loading tournament activity" />
    <ContentState v-else-if="loadError" kind="error" :message="loadError" action-label="Try again" @action="load" />

    <template v-else-if="data">
      <section class="home-heading">
        <div>
          <h1>COPE Chess</h1>
          <p>{{ data.connected_workers }} workers connected, totalling {{ data.connected_cores }} cores and {{ data.live_games }} currently live games</p>
        </div>
      </section>

      <section class="section-stack" aria-labelledby="running-tournaments-title">
        <div class="section-heading">
          <div>
            <h2 id="running-tournaments-title">Running tournaments</h2>
          </div>
          <RouterLink to="/tournaments">All tournaments</RouterLink>
        </div>

        <div v-if="data.running_tournaments.length" class="live-grid">
          <article v-for="item in data.running_tournaments" :key="item.tournament.record.id" class="live-card" :class="{ 'live-card--without-preview': !item.preview }">
            <div v-if="item.preview" class="live-card__board" aria-hidden="true">
              <ChessBoard
                :fen="item.preview.opening?.fen || 'startpos'"
                :moves="item.preview.moves.map((move) => move.uci)"
                :controls="false"
                :coordinates="false"
                compact
                :label="`${item.preview.white_name} versus ${item.preview.black_name} position`"
              />
            </div>
            <div class="live-card__body">
              <header>
                <div>
                  <h3><RouterLink :to="`/tournaments/${item.tournament.record.id}`">{{ item.tournament.record.name }}</RouterLink></h3>
                  <p>{{ item.tournament.format || 'Tournament' }} / {{ item.tournament.time_control || 'Time control not set' }}</p>
                </div>
                <div class="live-card__status">
                  <SpectatorCount :count="spectatorCount(item.tournament.record.id, item.tournament.spectator_count)" />
                  <StatusPill :status="item.tournament.record.status" />
                </div>
              </header>

              <div v-if="item.preview" class="live-card__preview-heading">
                <span>Live game</span>
                <RouterLink :to="`/tournaments/${item.tournament.record.id}?game_id=${item.preview.game.id}`">
                  {{ item.preview.white_name }} <span>vs</span> {{ item.preview.black_name }}
                </RouterLink>
                <small>Round {{ item.preview.game.round ?? '-' }}</small>
              </div>
              <p v-else class="live-card__meta">Waiting for the next game to start.</p>

              <ProgressBar :value="progress(item.tournament)" :label="`${item.tournament.summary.finished || 0} of ${item.tournament.summary.total} tournament games finished`" />
              <dl class="live-card__facts">
                <div><dt>Games</dt><dd>{{ item.tournament.summary.finished || 0 }} / {{ item.tournament.summary.total }}</dd></div>
                <div><dt>Game pairs</dt><dd>{{ item.tournament.summary.pairs || 0 }}</dd></div>
                <div><dt>Live</dt><dd>{{ item.tournament.summary.live || 0 }}</dd></div>
                <div v-if="item.tournament.record.current_round"><dt>Round</dt><dd>{{ item.tournament.record.current_round }}</dd></div>
                <div v-if="item.preview"><dt>Preview plies</dt><dd>{{ item.preview.moves.length }}</dd></div>
                <div v-if="item.preview"><dt>Last move</dt><dd>{{ item.preview.last_move?.san || '-' }}</dd></div>
              </dl>
              <RouterLink v-if="item.preview" class="watch-link" :to="`/tournaments/${item.tournament.record.id}?game_id=${item.preview.game.id}`">Watch game</RouterLink>
              <RouterLink v-else class="watch-link" :to="`/tournaments/${item.tournament.record.id}`">Open tournament</RouterLink>
            </div>
          </article>
        </div>
        <ContentState v-else kind="empty" compact title="No running tournaments" />
      </section>

      <section class="engine-queues" aria-labelledby="engine-queues-title">
        <div class="section-heading queue-section-heading">
          <div>
            <h2 id="engine-queues-title">Engine queues</h2>
            <p>Engines planned for addition and testing across COPE.</p>
          </div>
        </div>

        <div class="queue-public-grid">
          <article class="panel public-queue">
            <header><div><span>Acknowledged</span><h3>Recognised engines</h3></div><strong>{{ data.engine_queues.acknowledged.length }}</strong></header>
            <p>All engines must be fully original in their data and codebase, and human-written. This queue contains engines recognised by COPE that are planned to be added. Its order may be subject to change.</p>
            <ol v-if="data.engine_queues.acknowledged.length">
              <li v-for="(item, index) in data.engine_queues.acknowledged" :key="item.id">
                <b>{{ String(index + 1).padStart(2, '0') }}</b>
                <span><strong>{{ item.name }}</strong><small>{{ item.version }}</small></span>
              </li>
            </ol>
            <div v-else class="public-queue__empty">No engines are currently queued for acknowledgement.</div>
          </article>

          <article class="panel public-queue public-queue--waiting">
            <header><div><span>Waiting for test</span><h3>Planned test runs</h3></div><strong>{{ data.engine_queues.waiting_for_test.length }}</strong></header>
            <p>Test order can vary. Every engine will eventually be tested in all lists it qualifies for, but lower-priority lists may take time. Authors who release new versions at excessive speeds may wait longer for each version to be tested. To fast-track engine testing, contact <strong>@silverrzz</strong> on Discord to learn how to donate your hardware.</p>
            <ol v-if="data.engine_queues.waiting_for_test.length">
              <li v-for="(item, index) in data.engine_queues.waiting_for_test" :key="item.id">
                <b>{{ String(index + 1).padStart(2, '0') }}</b>
                <span><strong>{{ item.engine_name }} {{ item.engine_version }}</strong><small>{{ item.rating_list_name }}</small></span>
              </li>
            </ol>
            <div v-else class="public-queue__empty">No engine tests are currently waiting.</div>
          </article>
        </div>
      </section>

      <section class="home-tables">
        <section class="panel data-panel" aria-labelledby="upcoming-title">
          <div class="data-panel__header">
            <div>
              <h2 id="upcoming-title">Upcoming</h2>
            </div>
          </div>
          <div v-if="data.upcoming_rows.length" class="upcoming-list">
            <RouterLink v-for="row in data.upcoming_rows" :key="`${row.href}-${row.tournament}-${row.round}`" :to="row.href" class="upcoming-row">
              <div>
                <strong>{{ row.tournament }}</strong>
                <span v-if="row.status === 'scheduled' && row.scheduled_start_at">Starts {{ formatDate(row.scheduled_start_at, true) }}</span>
                <span v-else>Round {{ row.round }}</span>
              </div>
              <p>{{ row.white }} <span aria-hidden="true">vs</span><span class="sr-only">versus</span> {{ row.black }}</p>
              <StatusPill :status="row.status" />
              <span class="row-arrow" aria-hidden="true">&gt;</span>
            </RouterLink>
          </div>
          <p v-else class="panel-empty">No upcoming games.</p>
        </section>

        <section class="panel data-panel" aria-labelledby="recent-title">
          <div class="data-panel__header">
            <div>
              <h2 id="recent-title">Recent results</h2>
            </div>
          </div>
          <GameTable
            v-if="data.recent_games.length"
            :games="data.recent_games"
            :engines="data.engines"
            :tournament-names="data.tournament_names"
            :show-tournament="true"
            :show-round="true"
            caption="Recent tournament results"
          />
          <p v-else class="panel-empty">No recent results.</p>
        </section>
      </section>
    </template>
  </div>
</template>

<style scoped>
.home-page {
  display: grid;
  gap: clamp(1.5rem, 3vw, 2.5rem);
  padding-block: clamp(1.2rem, 2.5vw, 2.25rem) 3rem;
}

.home-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-xl, 2rem);
  padding-block-end: var(--space-lg, 1.5rem);
  border-block-end: 1px solid var(--color-border, #d5dbe1);
}

.home-heading h1,
.home-heading p,
.section-heading h2,
.section-heading p,
.data-panel h2,
.data-panel p {
  margin: 0;
}

.home-heading h1 {
  font-size: clamp(2rem, 5vw, 4rem);
  letter-spacing: -0.045em;
  line-height: 0.98;
}

.home-heading p {
  margin-block-start: 0.65rem;
  color: var(--color-text-muted, #607080);
  font-size: clamp(0.8rem, 1.5vw, 0.95rem);
}

.section-heading,
.data-panel__header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-md, 1rem);
}

.section-heading h2,
.data-panel h2 {
  font-size: 1.05rem;
}

.section-heading p,
.data-panel__header p {
  margin-block-start: 0.2rem;
  color: var(--color-text-muted, #607080);
  font-size: 0.76rem;
}

.section-heading a,
.data-panel__header a {
  color: var(--color-accent, #2f78c4);
  font-size: 0.78rem;
  font-weight: 700;
  text-decoration: none;
  white-space: nowrap;
}

.live-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 31rem), 1fr));
  gap: var(--space-md, 1rem);
}

.live-card {
  display: grid;
  grid-template-columns: minmax(9rem, 0.72fr) minmax(0, 1.4fr);
  gap: var(--space-md, 1rem);
  padding: var(--space-md, 1rem);
  border: 1px solid var(--color-border, #d5dbe1);
  border-radius: var(--radius-lg, 0.75rem);
  background: var(--color-surface, #fff);
}

.live-card__board {
  align-self: center;
  min-width: 0;
}

.live-card__body {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.75rem;
}

.live-card--without-preview {
  grid-template-columns: 1fr;
}

.live-card header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
}

.live-card__status {
  display: flex;
  align-items: center;
  gap: var(--space-sm, 0.5rem);
}

.live-card h3,
.live-card p,
.live-card dl {
  margin: 0;
}

.live-card h3 {
  font-size: 0.92rem;
  line-height: 1.3;
}

.live-card h3 a,
.live-card header p a {
  color: inherit;
  text-decoration: none;
}

.live-card h3 a:hover,
.live-card header p a:hover {
  color: var(--color-accent, #2f78c4);
  text-decoration: underline;
  text-underline-offset: 0.16em;
}

.live-card h3 span,
.live-card header p,
.live-card__meta {
  color: var(--color-text-muted, #607080);
  font-size: 0.7rem;
  font-weight: 500;
}

.live-card__preview-heading {
  display: grid;
  gap: 0.14rem;
}

.live-card__preview-heading > span,
.live-card__preview-heading small {
  color: var(--color-text-muted, #607080);
  font-size: 0.64rem;
}

.live-card__preview-heading > span {
  font-weight: 750;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.live-card__preview-heading a {
  color: inherit;
  font-size: 0.82rem;
  font-weight: 750;
  text-decoration: none;
}

.live-card__preview-heading a:hover {
  color: var(--color-accent, #2f78c4);
  text-decoration: underline;
  text-underline-offset: 0.16em;
}

.live-card__preview-heading a span {
  color: var(--color-text-muted, #607080);
  font-weight: 500;
}

.live-card dl {
  display: flex;
  gap: 1.25rem;
}

.live-card dt {
  color: var(--color-text-muted, #607080);
  font-size: 0.61rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.live-card dd {
  margin: 0.12rem 0 0;
  font-size: 0.8rem;
  font-weight: 700;
}

.watch-link {
  align-self: flex-start;
  margin-block-start: auto;
  color: var(--color-accent, #2f78c4);
  font-size: 0.76rem;
  font-weight: 750;
  text-decoration: none;
}

.home-tables {
  display: grid;
  grid-template-columns: minmax(20rem, 0.75fr) minmax(30rem, 1.25fr);
  gap: var(--space-md, 1rem);
  align-items: start;
}

.engine-queues {
  display: grid;
  gap: var(--space-md, 1rem);
}

.queue-section-heading {
  align-items: center;
}

.queue-public-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-md, 1rem);
}

.public-queue {
  overflow: hidden;
  padding: 0;
}

.public-queue > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 1rem;
  border-block-end: 1px solid var(--color-border, #d5dbe1);
}

.public-queue > header span {
  color: var(--color-accent, #2f78c4);
  font-size: .62rem;
  font-weight: 800;
  letter-spacing: .08em;
  text-transform: uppercase;
}

.public-queue > header h3 {
  margin: .16rem 0 0;
  font-size: 1rem;
}

.public-queue > header > strong {
  display: grid;
  min-width: 1.85rem;
  height: 1.85rem;
  place-items: center;
  border-radius: 999px;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-size: .72rem;
}

.public-queue > p {
  min-height: 6.2rem;
  margin: 0;
  padding: .9rem 1rem;
  color: var(--color-text-muted, #607080);
  font-size: .72rem;
  line-height: 1.55;
}

.public-queue > p strong {
  color: var(--color-text);
}

.public-queue ol {
  display: grid;
  margin: 0;
  padding: 0;
  list-style: none;
  border-block-start: 1px solid var(--color-border, #d5dbe1);
}

.public-queue li {
  display: grid;
  grid-template-columns: 2rem minmax(0, 1fr);
  align-items: center;
  gap: .65rem;
  padding: .68rem 1rem;
  border-block-end: 1px solid var(--color-border, #d5dbe1);
}

.public-queue li:last-child {
  border-block-end: 0;
}

.public-queue li > b {
  color: var(--color-text-faint);
  font-family: var(--font-mono);
  font-size: .65rem;
}

.public-queue li > span {
  display: grid;
  min-width: 0;
  gap: .12rem;
}

.public-queue li strong {
  overflow: hidden;
  font-size: .78rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.public-queue li small {
  color: var(--color-text-muted, #607080);
  font-size: .66rem;
}

.public-queue__empty {
  padding: 1.4rem 1rem;
  border-block-start: 1px solid var(--color-border, #d5dbe1);
  color: var(--color-text-muted, #607080);
  font-size: .75rem;
  text-align: center;
}

.data-panel {
  overflow: hidden;
  padding: 0;
}

.data-panel__header {
  padding: var(--space-md, 1rem);
  border-block-end: 1px solid var(--color-border, #d5dbe1);
}

.upcoming-list {
  display: grid;
}

.upcoming-row {
  display: grid;
  grid-template-columns: minmax(8rem, 0.75fr) minmax(10rem, 1.25fr) auto auto;
  align-items: center;
  gap: 0.65rem;
  padding: 0.72rem 1rem;
  border-block-end: 1px solid var(--color-border, #d5dbe1);
  color: inherit;
  font-size: 0.76rem;
  text-decoration: none;
}

.upcoming-row:last-child {
  border-block-end: 0;
}

.upcoming-row:hover {
  background: color-mix(in srgb, var(--color-accent, #2f78c4) 5%, transparent);
}

.upcoming-row > div {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.upcoming-row strong,
.upcoming-row p {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.upcoming-row > div span,
.row-arrow {
  color: var(--color-text-muted, #607080);
  font-size: 0.66rem;
}

.row-arrow {
  font-size: 1rem;
}

.panel-empty {
  padding: 2.5rem 1rem;
  color: var(--color-text-muted, #607080);
  font-size: 0.82rem;
  text-align: center;
}

@media (max-width: 68rem) {
  .home-tables { grid-template-columns: 1fr; }
  .queue-public-grid { grid-template-columns: 1fr; }
  .public-queue > p { min-height: 0; }
}

@media (max-width: 42rem) {
  .home-heading { align-items: stretch; flex-direction: column; }
  .live-card { grid-template-columns: 7rem minmax(0, 1fr); }
  .live-card dl { flex-wrap: wrap; }
  .upcoming-row { grid-template-columns: minmax(0, 1fr) auto; }
  .upcoming-row > p { grid-column: 1; }
  .upcoming-row :deep(.status-pill) { grid-column: 2; grid-row: 1; }
  .row-arrow { grid-column: 2; grid-row: 2; }
}

@media (max-width: 30rem) {
  .live-card { grid-template-columns: 1fr; }
  .live-card__board { width: min(11rem, 100%); }
}
</style>
