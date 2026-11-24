<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api } from "@/api/client";
import ChessViewer from "@/components/chess/ChessViewer.vue";
import MoveList from "@/components/chess/MoveList.vue";
import { buildPositions, positionFen, squareIndex, type Color } from "@/components/chess/chess";
import ChatPanel from "@/components/public/ChatPanel.vue";
import EnginePanel from "@/components/public/EnginePanel.vue";
import { clockLabel, moveEvaluation, moveNps, resultLabel, timeControlLabel } from "@/components/public/format";
import type { ChatMessage, ClockState, EngineAnalysis, LiveSnapshot, MoveRecord, TournamentDetailResponse } from "@/components/public/types";
import type { EventDetailResponse } from "@/types/events";
import EventArena from "../EventArena.vue";
import SecretQueenStandings from "./SecretQueenStandings.vue";
import type { SecretQueenContest, SecretQueenGame, SecretQueenSnapshot } from "./types";

const props = withDefaults(defineProps<{ detail: EventDetailResponse; contests: SecretQueenContest[]; clockOffsetMs?: number }>(), { clockOffsetMs: 0 });
const route = useRoute();
const router = useRouter();
const viewer = ref<TournamentDetailResponse | null>(null);
const moves = ref<MoveRecord[]>([]);
const analysis = ref<Partial<Record<Color, EngineAnalysis>>>({});
const clock = ref<{ state: ClockState; observed: number } | null>(null);
const now = ref(Date.now());
const ply = ref(0);
const error = ref("");
const concealedTimeline = ref<SecretQueenSnapshot["concealed_queens"]>(null);
let stream: EventSource | null = null;
let request: AbortController | null = null;
let refreshTimer: number | undefined;
let clockTimer: number | undefined;

const playable = computed(() => props.contests.filter(contest => contest.games.length));
const pinnedGame = computed(() => typeof route.query.game_id === "string" ? route.query.game_id : null);
const pinnedContest = computed(() => playable.value.find(contest => contest.games.some(game => String(game.id) === pinnedGame.value)));
const contest = computed(() => pinnedContest.value
  ?? playable.value.find(item => item.games.some(game => game.status === "live"))
  ?? playable.value.find(item => item.status === "live")
  ?? playable.value.at(-1));
const game = computed(() => contest.value?.games.find(item => String(item.id) === pinnedGame.value)
  ?? (contest.value ? defaultGame(contest.value) : undefined));
const pairFinished = computed(() => {
  const selected = game.value;
  const pair = contest.value?.games.filter(item => item.round === selected?.round && item.pair_index === selected?.pair_index);
  return pair?.length === 2 && pair.every(item => item.status === "finished");
});
const names = computed(() => new Map(props.detail.cast.map(member => [Number(member.engine_version_id), member.display_name])));
const opening = computed(() => viewer.value?.opening ?? { name: "Start position", fen: "startpos" });
const positions = computed(() => buildPositions(opening.value.fen, moves.value.map(move => move.uci), undefined, moves.value.map(move => move.position_fen)));
const currentFen = computed(() => positionFen(positions.value[ply.value] ?? positions.value[0]!));
const historical = computed(() => game.value?.status === "finished" || ply.value < moves.value.length);
const concealedQueens = computed(() => concealedTimeline.value?.[ply.value]);
const activeSide = computed(() => historical.value || !clock.value?.state.running ? null : clock.value.state.active_side);
const engines = computed(() => ({ white: game.value?.white_engine_id, black: game.value?.black_engine_id }));
const title = computed(() => game.value ? engineName(game.value.white_engine_id) + " versus " + engineName(game.value.black_engine_id) : props.detail.event.title);
const status = computed(() => game.value?.result ? resultLabel(game.value.result) : game.value?.status === "live" ? "Live" : "Waiting for game");
const followingLive = computed(() => !pinnedContest.value && !historical.value);
const annotations = computed(() => moves.value.map((move, index) => {
  if (move.uci.length !== 4 || !move.position_fen) return null;
  const before = positions.value[index];
  const after = positions.value[index + 1];
  const source = before?.squares[squareIndex(move.uci.slice(0, 2))];
  const target = after?.squares[squareIndex(move.uci.slice(2, 4))];
  return (source === "P" && target === "Q") || (source === "p" && target === "q")
    ? { label: "Queen revealed" } : null;
}));
const gameContext = computed(() => {
  const control = viewer.value?.tournament.config?.time_control;
  return [
    contest.value?.title,
    game.value ? contest.value?.kind === "group" ? "Round " + game.value.round : "Pair " + game.value.pair_index : "",
    viewer.value?.viewer_game?.game_number ? "Game " + viewer.value.viewer_game.game_number : "",
    timeControlLabel(control),
  ].filter(Boolean).join(" / ");
});

function defaultGame(current: SecretQueenContest): SecretQueenGame | undefined {
  return current.games.find(item => item.status === "live")
    ?? current.games.find(item => item.status === "assigned" || item.status === "pending")
    ?? current.games.at(-1);
}

function engineName(id: number): string {
  return names.value.get(id) ?? viewer.value?.engines[String(id)] ?? "Engine " + id;
}

function selectGame(gameId?: number): void {
  const query = { ...route.query };
  if (gameId !== undefined) query.game_id = String(gameId);
  else delete query.game_id;
  void router.push({ query });
}

function selectContest(contestId: number): void {
  const selected = playable.value.find(item => item.id === contestId);
  if (selected) selectGame(defaultGame(selected)?.id);
}

function followLive(): void {
  ply.value = moves.value.length;
  if (pinnedGame.value) selectGame();
}

function appendChat(message: ChatMessage): void {
  if (message.id !== undefined && props.detail.chat_messages.some(item => String(item.id) === String(message.id))) return;
  props.detail.chat_messages.push(message);
}

function panel(side: Color): { clock: string; analysis: EngineAnalysis | null } {
  const start = contest.value?.time_control?.initial_ms;
  if (!historical.value) {
    const live = clock.value;
    let value = live?.state.clocks_ms?.[side] ?? start;
    if (value !== undefined && live?.state.running && live.state.active_side === side) {
      value -= Math.max(0, now.value + props.clockOffsetMs - live.observed);
    }
    return { clock: value === undefined ? viewer.value?.clocks?.[side] ?? "--:--" : clockLabel(value), analysis: analysis.value[side] ?? null };
  }
  for (let index = Math.min(ply.value, moves.value.length) - 1; index >= 0; index -= 1) {
    const before = positions.value[index];
    const move = moves.value[index]!;
    if (!before || (before.turn === "w") !== (side === "white") || move.is_book) continue;
    return {
      clock: clockLabel(move.clock_after_ms ?? start),
      analysis: {
        eval: moveEvaluation(move), depth: move.depth ?? null, seldepth: move.seldepth ?? null,
        nodes: move.nodes ?? null, nps: moveNps(move), hashfull: move.hashfull ?? null,
        info: move.info_line ?? null, pv: move.pv ?? null, pv_san: move.pv_san ?? null,
        root_fen: positionFen(before),
      },
    };
  }
  return { clock: clockLabel(start), analysis: null };
}
const panels = computed(() => ({ white: panel("white"), black: panel("black") }));

function acceptMoves(next: MoveRecord[]): void {
  const atEnd = ply.value >= moves.value.length;
  moves.value = next;
  ply.value = atEnd ? next.length : Math.min(ply.value, next.length);
}

function acceptClock(state: ClockState): void {
  const observed = Date.parse(state.observed_at ?? state.sent_at ?? "");
  if (Number.isFinite(observed) && clock.value && observed < clock.value.observed) return;
  now.value = Date.now();
  clock.value = { state, observed: Number.isFinite(observed) ? observed : now.value + props.clockOffsetMs };
}

function parseStream<T>(event: Event): T | undefined {
  try {
    const message = JSON.parse((event as MessageEvent<string>).data) as { data?: T };
    return message.data;
  } catch {
    return undefined;
  }
}

async function loadViewer(): Promise<void> {
  const id = game.value?.id;
  const tournament = contest.value?.tournament_id;
  if (id === undefined || tournament == null || request) return;
  const pending = new AbortController();
  request = pending;
  try {
    const response = await api.get<TournamentDetailResponse>(
      "/api/events/" + encodeURIComponent(props.detail.event.slug) + "/tournaments/" + tournament,
      { query: { game_id: id, page_size: 25 }, signal: pending.signal },
    );
    if (pending.signal.aborted || game.value?.id !== id) return;
    viewer.value = response;
    if (response.viewer_moves.length >= moves.value.length || response.viewer_game?.status === "pending") {
      acceptMoves(response.viewer_moves);
    }
    analysis.value = response.engine_data ?? {};
    if (response.clock_state) acceptClock(response.clock_state);
    error.value = "";
  } catch {
    if (!pending.signal.aborted) error.value = "The board could not be loaded.";
  } finally {
    if (request === pending) request = null;
  }
}

watch([() => contest.value?.tournament_id, () => game.value?.id], ([tournament, id]) => {
  stream?.close();
  stream = null;
  request?.abort();
  request = null;
  viewer.value = null;
  moves.value = [];
  analysis.value = {};
  clock.value = null;
  ply.value = 0;
  if (tournament == null || id === undefined) return;
  void loadViewer();
  if (game.value?.status === "finished") return;

  const source = new EventSource("/events/" + encodeURIComponent(props.detail.event.slug) + "/tournaments/" + tournament + "/stream?game_id=" + id + "&spectator=0");
  stream = source;
  source.addEventListener("tournament.snapshot", event => {
    const snapshot = parseStream<LiveSnapshot>(event);
    if (stream !== source || Number(snapshot?.game?.id) !== id || !snapshot) return;
    if (snapshot.moves && (snapshot.moves.length >= moves.value.length || snapshot.game?.status === "pending")) acceptMoves(snapshot.moves);
    if (snapshot.engine_data) analysis.value = snapshot.engine_data;
    if (snapshot.clock_state) acceptClock(snapshot.clock_state);
  });
  source.addEventListener("game.move", event => {
    const update = parseStream<{ game_id: number; move: MoveRecord; clocks_ms?: NonNullable<ClockState["clocks_ms"]> }>(event);
    if (stream !== source || Number(update?.game_id) !== id || !update?.move) return;
    if (update.move.ply === moves.value.length + 1 && update.move.position_fen) {
      acceptMoves([...moves.value, update.move]);
      if (update.clocks_ms) acceptClock({ clocks_ms: update.clocks_ms, running: false });
    } else if (update.move.ply > moves.value.length) {
      void loadViewer();
    }
  });
  source.addEventListener("engine.info", event => {
    const update = parseStream<EngineAnalysis & { game_id: number; side: Color; engine_data?: EngineAnalysis }>(event);
    if (stream !== source || !update || Number(update.game_id) !== id || !["white", "black"].includes(update.side)) return;
    analysis.value[update.side] = { ...analysis.value[update.side], ...(update.engine_data ?? update) };
  });
  source.addEventListener("clock.sync", event => {
    const state = parseStream<ClockState>(event);
    if (stream === source && state && Number(state.game_id) === id) acceptClock(state);
  });
  source.addEventListener("tournament.changed", () => { if (stream === source) void loadViewer(); });
}, { immediate: true });

watch([() => game.value?.id, pairFinished], ([id, finished], _, onCleanup) => {
  concealedTimeline.value = null;
  if (id === undefined || !finished) return;
  const pending = new AbortController();
  onCleanup(() => pending.abort());
  void api.get<SecretQueenSnapshot>("/api/events/" + encodeURIComponent(props.detail.event.slug) + "/secret-queen/games/" + id, { signal: pending.signal })
    .then(snapshot => { if (!pending.signal.aborted) concealedTimeline.value = snapshot.concealed_queens; })
    .catch(() => {});
}, { immediate: true });

onMounted(() => {
  refreshTimer = window.setInterval(() => {
    if (!document.hidden && game.value?.status !== "finished") void loadViewer();
  }, 5000);
  clockTimer = window.setInterval(() => { now.value = Date.now(); }, 100);
});
onBeforeUnmount(() => {
  stream?.close();
  request?.abort();
  window.clearInterval(refreshTimer);
  window.clearInterval(clockTimer);
});
</script>

<template>
  <EventArena :title="detail.event.title" :slug="detail.event.slug" :status="status" :game-context="gameContext"
    :spectator-count="detail.spectator_count" :can-follow-live="!followingLive && ['live', 'intermission'].includes(detail.event.status)"
    :error="error" @follow-live="followLive" @retry="loadViewer">
    <template #selectors>
      <label v-if="playable.length > 1">Group or match
        <select :value="contest?.id" @change="selectContest(Number(($event.target as HTMLSelectElement).value))">
          <option v-for="item in playable" :key="item.id" :value="item.id">{{ item.title }}</option>
        </select>
      </label>
      <label v-if="contest">Game
        <select :value="game?.id" @change="selectGame(Number(($event.target as HTMLSelectElement).value))">
          <option v-for="item in contest.games" :key="item.id" :value="item.id">
            {{ contest.kind === 'group' ? 'Round ' + item.round : 'Pair ' + item.pair_index }} · {{ engineName(item.white_engine_id) }} – {{ engineName(item.black_engine_id) }}{{ item.result ? ' (' + item.result + ')' : '' }}
          </option>
        </select>
      </label>
    </template>
    <template #engines>
      <div v-for="side in (['black', 'white'] as const)" :key="side" class="relay-engine-slot">
        <EnginePanel :side="side" :engine-id="engines[side] ?? null" :name="engines[side] === undefined ? '' : engineName(engines[side]!)"
          :clock="panels[side].clock" :analysis="panels[side].analysis" :position-fen="currentFen" :active="activeSide === side" />
      </div>
    </template>
    <template #board>
      <ChessViewer :key="game?.id ?? 'pending'" v-model="ply" :opening="opening" :moves="moves" :label="title"
        :finished="game?.status === 'finished'" :concealed-queens="concealedQueens" />
    </template>
    <template #activity>
      <MoveList v-model="ply" :moves="moves.map(move => move.san || move.uci)" :uci-moves="moves.map(move => move.uci)" :fen="opening.fen" :annotations="annotations" />
      <ChatPanel :messages="detail.chat_messages" :settings="detail.chat_settings" :event-slug="detail.event.slug"
        :game-id="game?.id ?? null" @sent="appendChat" />
    </template>
    <SecretQueenStandings :contests="contests" :cast="detail.cast" :selected-contest="contest?.id ?? null" @select="selectContest" />
  </EventArena>
</template>
