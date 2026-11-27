<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "@/api/client";
import { buildPositions, positionFen } from "@/components/chess/chess";
import ChessViewer from "@/components/chess/ChessViewer.vue";
import MoveList from "@/components/chess/MoveList.vue";
import ChatPanel from "@/components/public/ChatPanel.vue";
import EnginePanel from "@/components/public/EnginePanel.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import { clockLabel, moveEvaluation, moveNps, resultLabel, timeControlLabel } from "@/components/public/format";
import type { ChatMessage, EngineAnalysis, GameRecord, Identifier, TournamentDetailResponse } from "@/components/public/types";
import type { EventDetailResponse } from "@/types/events";
import EventArena from "../EventArena.vue";
import EngineCheers from "./EngineCheers.vue";
import PondScene from "./PondScene.vue";
import wakwak from "@/assets/wakwak-event.png";
import fairyStockfish from "@/assets/fairy-stockfish-event.png";
import { pondRoster, type PondPayload } from "./types";

const props = defineProps<{ detail: EventDetailResponse }>();
const route = useRoute();
const router = useRouter();
const payload = computed(() => props.detail.custom as PondPayload);
const controlLabel = computed(() => timeControlLabel(payload.value.tournament?.config?.time_control));
const initialClock = computed(() => {
  const control = payload.value.tournament?.config?.time_control;
  const milliseconds = control && (control.category === "increment" || control.category === "movestogo") ? control.initial_ms : 180_000;
  return `${String(Math.floor(milliseconds / 60_000)).padStart(2, "0")}:${String(Math.floor(milliseconds / 1000) % 60).padStart(2, "0")}`;
});
const roster = computed(() => pondRoster(props.detail.cast));
const games = ref<GameRecord[]>([]);
const viewer = ref<TournamentDetailResponse | null>(null);
const selected = ref<string | null>(typeof route.query.game_id === "string" ? route.query.game_id : null);
const selectedPly = ref(0);
const currentFen = ref("startpos");
const busy = ref(false);
const error = ref("");
const completed = computed(() => games.value.filter(game => game.status === "finished" && ["1-0", "0-1", "1/2-1/2"].includes(game.result ?? "")));
const defaultReplay = computed(() => props.detail.event.status === "completed"
  ? completed.value.find(game => game.game_number === 1) ?? completed.value[0]
  : undefined);
const requestedGameId = computed(() => selected.value ?? (defaultReplay.value ? String(defaultReplay.value.id) : undefined));
const viewerGame = computed(() => viewer.value?.viewer_game ?? games.value.find(game => String(game.id) === requestedGameId.value) ?? games.value.find(game => ["assigned", "live"].includes(game.status)) ?? games.value[0] ?? null);
const moves = computed(() => viewer.value?.viewer_moves ?? []);
const opening = computed(() => viewer.value?.opening ?? { name: "Start position", fen: "startpos" });
const positions = computed(() => buildPositions(opening.value.fen, moves.value.map(move => move.uci), "duck"));
const historical = computed(() => viewerGame.value?.status === "finished" || selectedPly.value < moves.value.length);
const panels = computed(() => ({ white: panelForSide("white"), black: panelForSide("black") }));
const oddsLabel = computed(() => payload.value.odds_steps?.[(viewerGame.value?.game_number ?? 1) - 1] ?? "");
const activeSide = computed(() => !historical.value && viewer.value?.clock_state?.running ? viewer.value.clock_state.active_side : null);
const activeTab = computed(() => ["games", "settings"].includes(String(route.query.tab)) ? String(route.query.tab) : "standings");
const timing = computed(() => props.detail.event.scheduled_start_at ? new Intl.DateTimeFormat(undefined, { dateStyle: "long", timeStyle: "short" }).format(new Date(props.detail.event.scheduled_start_at)) : "Start date TBD");
const gameLabel = computed(() => viewerGame.value?.result ? resultLabel(viewerGame.value.result) : viewerGame.value?.status === "live" ? "Live" : props.detail.event.status === "completed" ? "Completed" : timing.value);
const gameContext = computed(() => [
  "Duck Chess",
  viewerGame.value ? `Game ${viewerGame.value.game_number ?? games.value.findIndex(game => game.id === viewerGame.value?.id) + 1}` : null,
  oddsLabel.value,
].filter(Boolean).join(" / "));
const standings = computed(() => roster.value.map(member => ({
  member,
  points: completed.value.reduce((points, game) => points + (game.result === "1/2-1/2" ? .5 : (game.result === "1-0" ? String(game.white_engine_id) : String(game.black_engine_id)) === String(member.engine_version_id) ? 1 : 0), 0),
})).sort((left, right) => right.points - left.points));
let request: AbortController | null = null;
let refreshTimer: number | undefined;

watch(() => payload.value.games, value => { games.value = value; }, { immediate: true });
watch(() => [payload.value.tournament?.id, props.detail.event.revision], () => { void loadViewer(); }, { immediate: true });
watch(() => route.query.game_id, value => { void loadViewer(typeof value === "string" ? value : null); });
onMounted(() => {
  refreshTimer = window.setInterval(() => {
    if (!document.hidden && ["running", "scheduled"].includes(payload.value.tournament?.status ?? "")) void loadViewer();
  }, 3000);
});
onBeforeUnmount(() => {
  request?.abort();
  window.clearInterval(refreshTimer);
});

function memberForSide(side: "white" | "black") {
  return roster.value.find(member => String(member.engine_version_id) === String(viewerGame.value?.[`${side}_engine_id`])) ?? roster.value[side === "white" ? 0 : 1];
}

function panelForSide(side: "white" | "black"): { clock: string; analysis: EngineAnalysis | null } {
  if (!historical.value) {
    return { clock: viewer.value?.clocks?.[side] ?? initialClock.value, analysis: viewer.value?.engine_data?.[side] ?? null };
  }
  const turn = side === "white" ? "w" : "b";
  for (let index = Math.min(selectedPly.value, moves.value.length) - 1; index >= 0; index -= 1) {
    const move = moves.value[index]!;
    const position = positions.value[index]!;
    if (move.is_book || position.turn !== turn) continue;
    return {
      clock: move.clock_after_ms == null ? initialClock.value : clockLabel(move.clock_after_ms),
      analysis: {
        eval: moveEvaluation(move),
        depth: move.depth ?? null,
        seldepth: move.seldepth ?? null,
        nodes: move.nodes ?? null,
        nps: moveNps(move),
        hashfull: move.hashfull ?? null,
        info: move.info_line ?? null,
        pv: move.pv ?? null,
        pv_san: move.pv_san ?? null,
        root_fen: positionFen(position),
      },
    };
  }
  return { clock: initialClock.value, analysis: null };
}

function engineName(engineId: Identifier): string {
  return roster.value.find(member => String(member.engine_version_id) === String(engineId))?.display_name ?? "Engine";
}

function selectGame(gameId: string | null): void {
  const query = { ...route.query };
  if (gameId) query.game_id = gameId;
  else delete query.game_id;
  void router.push({ query });
}

function appendChat(message: ChatMessage): void {
  if (message.id !== undefined && props.detail.chat_messages.some(item => String(item.id) === String(message.id))) return;
  props.detail.chat_messages.push(message);
}

async function loadViewer(gameId?: string | null): Promise<void> {
  const tournament = payload.value.tournament;
  if (!tournament || tournament.status === "draft") return;
  if (busy.value && gameId === undefined) return;
  if (gameId !== undefined) selected.value = gameId;
  request?.abort();
  const currentRequest = new AbortController();
  request = currentRequest;
  busy.value = true;
  try {
    const response = await api.get<TournamentDetailResponse>(`/api/events/${encodeURIComponent(props.detail.event.slug)}/tournaments/${tournament.id}`, {
      query: { game_id: requestedGameId.value, page_size: 25 }, signal: currentRequest.signal,
    });
    if (request !== currentRequest) return;
    const followLatest = !viewer.value || String(viewer.value.viewer_game?.id) !== String(response.viewer_game?.id) || selectedPly.value >= viewer.value.viewer_moves.length;
    viewer.value = response;
    if (followLatest) selectedPly.value = response.viewer_moves.length;
    const updates = [...response.games, ...response.active_games];
    const merged = new Map(games.value.map(game => [String(game.id), game]));
    for (const game of updates) merged.set(String(game.id), game);
    games.value = [...merged.values()].sort((left, right) => (left.game_number ?? 1) - (right.game_number ?? 1));
    error.value = "";
  } catch (cause) {
    if (request === currentRequest && (cause as Error).name !== "AbortError") error.value = "The board could not be loaded.";
  } finally {
    if (request === currentRequest) busy.value = false;
  }
}
</script>

<template>
  <EventArena :title="detail.event.title" :slug="detail.event.slug" :status="gameLabel" :game-context="gameContext"
    :spectator-count="detail.spectator_count" :can-follow-live="!!selected && ['live', 'intermission'].includes(detail.event.status)"
    :error="error" @follow-live="selectGame(null)" @retry="loadViewer()">
    <template #background><PondScene /></template>
    <template #engines>
        <div v-for="side in (['black', 'white'] as const)" :key="side" class="relay-engine-slot" :style="{ '--relay-primary': memberForSide(side)?.accent_color ?? '#a9d8d4' }">
          <EnginePanel :icon="memberForSide(side)?.id === roster[0]?.id ? wakwak : fairyStockfish" :data-cheer-origin="memberForSide(side)?.id" :side="side" variant="duck" :name="memberForSide(side)?.display_name ?? ''" :engine-id="memberForSide(side)?.engine_version_id ?? null" :clock="panels[side].clock" :analysis="panels[side].analysis" :position-fen="currentFen" :active="activeSide === side" />
        </div>
        <EngineCheers class="arena-cheers" compact :event-id="detail.event.id" :slug="detail.event.slug" :members="roster" />
    </template>
    <template #board>
      <ChessViewer variant="duck" :key="viewerGame?.id ?? 'pending'" :opening="opening" :moves="moves" :model-value="selectedPly" label="wakwak versus Fairy-Stockfish" @update:model-value="selectedPly = $event" @position="currentFen = $event.fen" />
    </template>
    <template #activity>
        <MoveList class="arena-moves" :moves="moves.map(move => move.san || move.uci)" :uci-moves="moves.map(move => move.uci)" :fen="opening.fen" :book-plies="opening.book_moves?.length ?? 0" :model-value="selectedPly" @update:model-value="selectedPly = $event" />
        <ChatPanel class="arena-chat" :messages="detail.chat_messages" :settings="detail.chat_settings" :event-slug="detail.event.slug" :game-id="viewerGame?.id ?? null" @sent="appendChat" />
    </template>
    <section class="tournament-data">
      <nav class="data-tabs" aria-label="Tournament information">
        <RouterLink :to="{ query: { ...route.query, tab: 'standings' } }" :aria-current="activeTab === 'standings' ? 'page' : undefined">Standings <span>{{ roster.length }}</span></RouterLink>
        <RouterLink :to="{ query: { ...route.query, tab: 'games' } }" :aria-current="activeTab === 'games' ? 'page' : undefined">Games <span>{{ games.length }}</span></RouterLink>
        <RouterLink :to="{ query: { ...route.query, tab: 'settings' } }" :aria-current="activeTab === 'settings' ? 'page' : undefined">Settings</RouterLink>
      </nav>
      <section v-if="activeTab === 'standings'" class="data-panel" aria-labelledby="standings-title">
        <header><h2 id="standings-title">Standings</h2></header>
        <div class="table-wrap"><table><thead><tr><th>Rank</th><th>Engine</th><th>Points</th><th>Score %</th><th>Played</th></tr></thead><tbody><tr v-for="(standing, index) in standings" :key="standing.member.id"><td class="rank-cell">{{ index + 1 }}</td><td class="team-cell">{{ standing.member.display_name }}</td><td class="number-cell">{{ standing.points }}</td><td class="number-cell">{{ completed.length ? `${(standing.points / completed.length * 100).toFixed(1)}%` : '-' }}</td><td class="number-cell">{{ completed.length }}</td></tr></tbody></table></div>
      </section>
      <section v-else-if="activeTab === 'games'" class="data-panel" aria-labelledby="games-title">
        <header><h2 id="games-title">Games</h2></header>
        <div class="table-wrap"><table><thead><tr><th>Game</th><th>White</th><th>Black</th><th>Result</th><th></th></tr></thead><tbody><tr v-for="(game, index) in games" :key="game.id"><td>{{ index + 1 }}</td><td class="team-cell">{{ engineName(game.white_engine_id) }}</td><td class="team-cell">{{ engineName(game.black_engine_id) }}</td><td>{{ game.result || (['live', 'assigned'].includes(game.status) ? 'Live' : 'Pending') }}</td><td class="action-cell"><button v-if="['live', 'assigned', 'finished'].includes(game.status)" type="button" @click="selectGame(String(game.id))">{{ game.status === 'finished' ? 'Replay' : 'View' }} <AppIcon name="arrow-right" :size="13" /></button></td></tr></tbody></table></div>
      </section>
      <section v-else class="data-panel settings-panel" aria-labelledby="settings-title">
        <header><h2 id="settings-title">Settings</h2></header>
        <dl class="settings-list"><div><dt>Variant</dt><dd>Duck Chess</dd></div><div><dt>Time control</dt><dd>{{ controlLabel }}</dd></div><div><dt>Threads per engine</dt><dd>{{ payload.tournament?.config?.engine_threads ?? 64 }}</dd></div><div><dt>Hash per engine</dt><dd>{{ payload.tournament?.config?.engine_hash_mb ?? 4096 }} MiB</dd></div><div><dt>Games</dt><dd>{{ payload.odds_steps?.length ? `Up to ${payload.odds_steps.length} · ends on wakwak’s first draw or loss` : '20 · 10 colour-swapped pairs' }}</dd></div><div><dt>Starting positions</dt><dd>{{ payload.opening_book }}</dd></div><div v-if="payload.odds_steps?.length"><dt>Odds ladder</dt><dd>{{ payload.odds_steps.join(' → ') }}</dd></div><div><dt>Rated</dt><dd>No</dd></div><div><dt>Start</dt><dd>{{ timing }}</dd></div></dl>
      </section>
    </section>
  </EventArena>
</template>

<style scoped>
.arena-cheers { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .5rem; padding-top: .15rem; }

.tournament-data { display: grid; gap: var(--space-sm, .5rem); margin-block-start: var(--space-md, 1rem); }
.data-tabs { display: flex; gap: .3rem; overflow-x: auto; }
.data-tabs a { display: inline-flex; min-height: 2.35rem; align-items: center; gap: .45rem; padding-inline: .85rem; border: 1px solid transparent; border-radius: var(--radius-sm, .35rem); color: var(--color-text-muted, #607080); font-size: .78rem; font-weight: 730; text-decoration: none; white-space: nowrap; }
.data-tabs a:hover { color: var(--color-text, #17202a); background: color-mix(in srgb, var(--color-text, #17202a) 5%, transparent); }
.data-tabs a[aria-current="page"] { border-color: color-mix(in srgb, var(--color-accent, #2f78c4) 24%, transparent); background: color-mix(in srgb, var(--color-accent, #2f78c4) 9%, var(--color-surface, #fff)); color: var(--color-accent, #2f78c4); }
.data-tabs a span { color: inherit; font-size: .64rem; opacity: .75; }
.data-panel { overflow: hidden; border: 1px solid var(--color-border, #d5dbe1); border-radius: var(--radius-lg, .75rem); background: var(--color-surface, #fff); }
.data-panel > header { padding: var(--space-md, 1rem); border-block-end: 1px solid var(--color-border, #d5dbe1); }
.data-panel h2, .data-panel header p, .hardware-section h3 { margin: 0; }
.data-panel h2 { font-size: 1rem; }
.data-panel header p { margin-block-start: .18rem; color: var(--color-text-muted, #607080); font-size: .7rem; }
.table-wrap { overflow-x: auto; }
.data-panel table { width: 100%; border-collapse: collapse; font-size: .82rem; }
.data-panel th, .data-panel td { padding: .72rem .85rem; border-block-end: 1px solid var(--color-border, #d5dbe1); text-align: start; }
.data-panel th { color: var(--color-text-muted, #607080); font-size: .63rem; letter-spacing: .04em; text-transform: uppercase; white-space: nowrap; }
.data-panel tbody tr:last-child td { border-block-end: 0; }
.data-panel tbody tr:hover { background: color-mix(in srgb, var(--color-accent, #2f78c4) 4.5%, transparent); }
.data-panel td a { color: inherit; font-weight: 700; text-decoration: none; }
.data-panel td a:hover { color: var(--color-accent, #2f78c4); text-decoration: underline; text-underline-offset: .16em; }
.pagination { display: flex; align-items: center; justify-content: flex-end; gap: .75rem; padding: .75rem .85rem; border-top: 1px solid var(--color-border, #d5dbe1); }
.pagination button { padding: .4rem .65rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: .35rem; background: var(--color-surface, #fff); color: inherit; cursor: pointer; font: inherit; }
.pagination button:disabled { cursor: default; opacity: .45; }
.pagination span { color: var(--color-text-muted, #607080); font-size: .72rem; }
.rank-cell { width: 4rem; color: var(--color-text-muted, #607080); font-variant-numeric: tabular-nums; }
.team-cell { font-weight: 700; }
.number-cell { width: 6rem; font-weight: 730; font-variant-numeric: tabular-nums; }
.action-cell { width: 4rem; text-align: end !important; }
.action-cell a { color: var(--color-accent, #2f78c4) !important; font-size: .72rem; }
.settings-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr)); gap: 1px; margin: 0; background: var(--color-border, #d5dbe1); }
.settings-list div { min-width: 0; padding: .85rem 1rem; background: var(--color-surface, #fff); }
.settings-list dt { color: var(--color-text-muted, #607080); font-size: .62rem; font-weight: 750; letter-spacing: .04em; text-transform: uppercase; }
.settings-list dd { margin: .22rem 0 0; font-size: .8rem; font-weight: 650; overflow-wrap: anywhere; }
.hardware-section { border-block-start: 1px solid var(--color-border, #d5dbe1); }
.hardware-section h3 { padding: .85rem 1rem; border-block-end: 1px solid var(--color-border, #d5dbe1); font-size: .88rem; }

@media (max-width: 40rem) { .settings-list { grid-template-columns: 1fr; } }

.action-cell button { display: inline-flex; align-items: center; gap: .3rem; padding: 0; border: 0; background: transparent; color: var(--color-accent, #2f78c4); font: inherit; font-size: .72rem; font-weight: 700; cursor: pointer; }
</style>
