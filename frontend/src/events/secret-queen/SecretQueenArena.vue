<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { api } from "@/api/client";
import ChessBoard from "@/components/chess/ChessBoard.vue";
import MoveList from "@/components/chess/MoveList.vue";
import StatusPill from "@/components/public/StatusPill.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import type { EventDetailResponse } from "@/types/events";
import SecretQueenStandings from "./SecretQueenStandings.vue";
import type { SecretQueenContest, SecretQueenMove, SecretQueenSnapshot } from "./types";

type Color = "white" | "black";
interface ClockSync {
  game_id: number;
  active_side: Color;
  clocks_ms: Record<Color, number | null>;
  running: boolean;
}

const props = defineProps<{ detail: EventDetailResponse; contests: SecretQueenContest[] }>();
const contestId = ref<number | null>(null);
const gameId = ref<number | null>(null);
const snapshot = ref<SecretQueenSnapshot | null>(null);
const ply = ref(0);
const error = ref("");
const clock = ref<(ClockSync & { observed: number }) | null>(null);
const now = ref(Date.now());
const orientation = ref<Color>("white");
let stream: EventSource | null = null;
let request: AbortController | null = null;
let poll: number | undefined;
let tick: number | undefined;
const names = computed(() => new Map(props.detail.cast.map(member => [Number(member.engine_version_id), member.display_name])));
const playable = computed(() => props.contests.filter(contest => contest.games.length));
const contest = computed(() => playable.value.find(item => item.id === contestId.value)
  ?? playable.value.find(item => item.games.some(game => game.status === "live"))
  ?? playable.value.find(item => item.status === "live") ?? playable.value.at(-1));
const game = computed(() => contest.value?.games.find(item => item.id === gameId.value)
  ?? contest.value?.games.find(item => item.status === "live")
  ?? contest.value?.games.find(item => ["assigned", "pending"].includes(item.status))
  ?? contest.value?.games.at(-1));
const moves = computed(() => snapshot.value?.moves ?? []);
const currentMove = computed(() => moves.value[ply.value - 1]);
const watchingLatest = computed(() => ply.value === moves.value.length);

function selectContest(value: number): void {
  contestId.value = value || null;
  gameId.value = null;
}

function clockLabel(side: Color): string {
  const history = moves.value.slice(0, ply.value).reverse().find(move => (move.ply % 2 === 1) === (side === "white"));
  let value = history?.clock_after_ms ?? contest.value?.time_control?.initial_ms;
  const observed = clock.value;
  if (observed && watchingLatest.value && game.value?.status === "live") {
    value = observed.clocks_ms[side] ?? value;
    if (value !== undefined && observed.running && observed.active_side === side) value -= now.value - observed.observed;
  }
  if (value === undefined) return "--:--";
  const seconds = Math.ceil(Math.max(0, value) / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function data<T>(message: Event): T | null {
  try {
    const payload = JSON.parse((message as MessageEvent<string>).data);
    return (payload.data ?? payload) as T;
  } catch {
    return null;
  }
}

async function refresh(): Promise<void> {
  const id = game.value?.id;
  if (!id || request) return;
  const pending = new AbortController();
  request = pending;
  try {
    const result = await api.get<SecretQueenSnapshot>(`/api/events/${encodeURIComponent(props.detail.event.slug)}/secret-queen/games/${id}`, { signal: pending.signal });
    if (pending.signal.aborted || game.value?.id !== id) return;
    const latest = watchingLatest.value;
    if (!snapshot.value || result.moves.length >= moves.value.length) snapshot.value = result;
    if (latest) ply.value = moves.value.length;
    error.value = "";
  } catch {
    if (!pending.signal.aborted) error.value = "The game is temporarily unavailable. Reconnecting…";
  } finally {
    if (request === pending) request = null;
  }
}

watch(() => [contest.value?.tournament_id, game.value?.id], ([tournament, id]) => {
  stream?.close();
  stream = null;
  request?.abort();
  request = null;
  snapshot.value = null;
  clock.value = null;
  ply.value = 0;
  if (!id || !tournament) return;
  void refresh();
  if (game.value?.status === "finished") return;
  const source = new EventSource(`/events/${encodeURIComponent(props.detail.event.slug)}/tournaments/${tournament}/stream?game_id=${id}&spectator=0`);
  stream = source;
  source.onopen = () => { void refresh(); };
  source.addEventListener("game.move", message => {
    const update = data<{ game_id: number; move?: SecretQueenMove }>(message);
    if (stream !== source || Number(update?.game_id) !== id) return;
    const move = update?.move;
    if (!move?.position_fen || !snapshot.value || move.ply > moves.value.length + 1) {
      void refresh();
    } else if (move.ply === moves.value.length + 1) {
      const latest = watchingLatest.value;
      snapshot.value.moves.push(move);
      if (latest) ply.value = moves.value.length;
    }
  });
  source.addEventListener("clock.sync", message => {
    const update = data<ClockSync>(message);
    if (stream === source && update && Number(update.game_id) === id) {
      clock.value = { ...update, observed: Date.now() };
    }
  });
  source.addEventListener("tournament.changed", () => { void refresh(); });
}, { immediate: true });

onMounted(() => {
  poll = window.setInterval(() => { void refresh(); }, 7500);
  tick = window.setInterval(() => { now.value = Date.now(); }, 150);
});
onBeforeUnmount(() => {
  stream?.close();
  request?.abort();
  window.clearInterval(poll);
  window.clearInterval(tick);
});
</script>

<template>
  <main class="secret-queen-arena">
    <div class="page-container arena-content">
      <header class="arena-heading">
        <RouterLink :to="{ name: 'event', params: { slug: detail.event.slug } }"><AppIcon name="arrow-left" :size="17" /> Event</RouterLink>
        <h1>Secret Queen Arena</h1>
      </header>
      <div v-if="playable.length" class="arena-filters">
        <label class="field"><span>Group or match</span><select class="input" :value="contest?.id" @change="selectContest(Number(($event.target as HTMLSelectElement).value))">
          <option v-for="item in playable" :key="item.id" :value="item.id">{{ item.title }}</option>
        </select></label>
        <label class="field"><span>Game</span><select class="input" :value="game?.id" @change="gameId = Number(($event.target as HTMLSelectElement).value)">
          <option v-for="item in contest?.games" :key="item.id" :value="item.id">{{ contest?.kind === 'group' ? `Round ${item.round}` : `Pair ${item.pair_index}` }} · {{ names.get(item.white_engine_id) }} – {{ names.get(item.black_engine_id) }}{{ item.result ? ` (${item.result})` : '' }}</option>
        </select></label>
      </div>
      <p v-if="error" role="status">{{ error }}</p>
      <div v-if="game" class="arena-viewer">
        <section class="arena-board" aria-label="Game board">
          <div class="arena-player"><strong>{{ names.get(orientation === 'white' ? game.black_engine_id : game.white_engine_id) }}</strong><time>{{ clockLabel(orientation === 'white' ? 'black' : 'white') }}</time></div>
          <ChessBoard :fen="currentMove?.position_fen ?? 'startpos'" :last-move="currentMove?.uci ?? ''" :orientation="orientation" :controls="false" />
          <div class="arena-player"><strong>{{ names.get(orientation === 'white' ? game.white_engine_id : game.black_engine_id) }}</strong><time>{{ clockLabel(orientation === 'white' ? 'white' : 'black') }}</time></div>
        </section>
        <section class="arena-game" aria-label="Game details">
          <header><StatusPill :status="snapshot?.status ?? game.status" /><strong v-if="snapshot?.result ?? game.result">{{ snapshot?.result ?? game.result }}</strong></header>
          <p v-if="snapshot?.termination">{{ snapshot.termination }}</p>
          <p v-if="snapshot?.secrets">Secret queens started on {{ snapshot.secrets.white }}2 and {{ snapshot.secrets.black }}7.</p>
          <MoveList v-model="ply" :moves="moves.map(move => move.san)" :uci-moves="moves.map(move => move.uci)" />
          <div class="arena-replay">
            <button class="button button--secondary button--small" type="button" :disabled="ply === 0" @click="ply -= 1">Previous</button>
            <button class="button button--secondary button--small" type="button" :disabled="ply >= moves.length" @click="ply += 1">Next</button>
            <button class="button button--ghost button--small" type="button" @click="ply = moves.length">Latest</button>
            <button class="button button--ghost button--small" type="button" @click="orientation = orientation === 'white' ? 'black' : 'white'">Flip board</button>
          </div>
        </section>
      </div>
      <SecretQueenStandings :contests="contests" :cast="detail.cast" :selected-contest="contest?.id ?? null" @select="selectContest" />
    </div>
  </main>
</template>

<style scoped>
.secret-queen-arena { padding-block: 2rem 4rem; background: var(--event-view-background); color: var(--event-view-text); }
.arena-content { display: grid; gap: 1.5rem; }
.arena-heading { display: grid; gap: .6rem; }
.arena-heading a { display: flex; width: fit-content; gap: .45rem; align-items: center; color: var(--event-view-accent); }
.arena-heading h1 { font-size: clamp(2rem, 4vw, 3rem); }
.arena-filters { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 2fr); gap: .8rem; }
.arena-filters select { background: #140d20; color: var(--event-view-text); }
.arena-viewer { display: grid; grid-template-columns: minmax(0, 38rem) minmax(0, 1fr); gap: 1.2rem; }
.arena-board, .arena-game { display: grid; gap: .65rem; align-content: start; }
.arena-player, .arena-game header, .arena-replay { display: flex; align-items: center; gap: .5rem; }
.arena-player, .arena-game header { justify-content: space-between; }
.arena-player time { font-variant-numeric: tabular-nums; }
.arena-game { padding: 1rem; border: 1px solid var(--event-view-border); border-radius: var(--radius-md); }
.arena-game :deep(.move-list) { max-height: 27rem; --color-text: var(--event-view-text); --color-text-muted: var(--event-view-muted); --color-surface: #140d20; --color-border: var(--event-view-border); }
.arena-replay { flex-wrap: wrap; }
@media (max-width: 50rem) { .arena-viewer, .arena-filters { grid-template-columns: minmax(0, 1fr); } }
</style>
