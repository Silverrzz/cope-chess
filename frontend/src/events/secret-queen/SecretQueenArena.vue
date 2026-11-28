<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api } from "@/api/client";
import ChessViewer from "@/components/chess/ChessViewer.vue";
import MoveList from "@/components/chess/MoveList.vue";
import { buildPositions, chessMoveAnimationMs, positionFen, queenRevealAnimationMs, squareIndex, type Color } from "@/components/chess/chess";
import ChatPanel from "@/components/public/ChatPanel.vue";
import ContentState from "@/components/public/ContentState.vue";
import EnginePanel from "@/components/public/EnginePanel.vue";
import FollowEnginePicker from "@/components/public/FollowEnginePicker.vue";
import GameTable from "@/components/public/GameTable.vue";
import { clockLabel, moveEvaluation, moveNps, resultLabel, timeControlLabel } from "@/components/public/format";
import AppIcon from "@/components/ui/AppIcon.vue";
import type { ChatMessage, ClockState, EngineAnalysis, EngineEvaluationRange, LiveSnapshot, MoveRecord, TournamentDetailResponse } from "@/components/public/types";
import type { EventDetailResponse } from "@/types/events";
import EventArena from "../EventArena.vue";
import SecretQueenInfo from "./SecretQueenInfo.vue";
import SecretQueenStandings from "./SecretQueenStandings.vue";
import type { SecretQueenContest, SecretQueenGame } from "./types";

type SecretQueenViewer = TournamentDetailResponse & { secret_queen_squares?: Record<Color, string> | null };

const props = withDefaults(defineProps<{ detail: EventDetailResponse; contests: SecretQueenContest[]; clockOffsetMs?: number }>(), { clockOffsetMs: 0 });
const route = useRoute();
const router = useRouter();
const viewer = ref<SecretQueenViewer | null>(null);
const moves = ref<MoveRecord[]>([]);
const analysis = ref<Partial<Record<Color, EngineAnalysis>>>({});
const clock = ref<{ state: ClockState; observed: number } | null>(null);
const now = ref(Date.now());
const ply = ref(0);
const reviewing = ref(false);
const error = ref("");
const followedGameId = ref<number>();
const resultDisplayMs = 4500;
let stream: EventSource | null = null;
let request: AbortController | null = null;
let refreshTimer: number | undefined;
let clockTimer: number | undefined;
let resultTimer: number | undefined;
let moveTimer: number | undefined;
let lastMoveAt: number | undefined;
let historyLoaded = false;

const playable = computed(() => props.contests.filter(contest => contest.games.length));
const names = computed(() => new Map(props.detail.cast.map(member => [Number(member.engine_version_id), member.display_name])));
const followOptions = computed(() => [...new Set(props.contests.flatMap(contest => contest.engine_ids))]
  .map(id => ({ id: String(id), name: engineName(id) }))
  .sort((left, right) => left.name.localeCompare(right.name)));
const pinnedGame = computed(() => typeof route.query.game_id === "string" ? route.query.game_id : null);
const pinnedContest = computed(() => playable.value.find(contest => contest.games.some(game => String(game.id) === pinnedGame.value)));
const liveContest = computed(() => playable.value.find(contest => contest.key === route.query.follow_contest));
const followedEngineId = computed({
  get: () => followOptions.value.find(engine => engine.id === route.query.follow_engine)?.id ?? "",
  set: (id: string) => {
    const { query } = gameTarget();
    delete query.follow_contest;
    delete query.follow_live;
    if (id) query.follow_engine = id;
    else delete query.follow_engine;
    void router.push({ query });
  },
});
const engineTarget = computed(() => {
  if (!followedEngineId.value) return;
  const id = Number(followedEngineId.value);
  const games = playable.value.flatMap(contest => contest.games
    .filter(game => includesEngine(game, id))
    .map(game => ({ contest, game })));
  return games.find(({ game }) => game.status === "live")
    ?? games.find(({ game }) => game.status === "assigned")
    ?? games.filter(({ game }) => game.status === "finished").at(-1)
    ?? games.find(({ game }) => game.status === "pending");
});
const engineContest = computed(() => engineTarget.value?.contest
  ?? playable.value.find(item => item.engine_ids.includes(Number(followedEngineId.value))));
const firstLiveContest = computed(() => playable.value.find(item => item.games.some(game => game.status === "live")));
const watchLiveWhileWaiting = computed(() => Boolean(followedEngineId.value) && route.query.follow_live === "1");
const fallbackContest = computed(() => {
  if (!watchLiveWhileWaiting.value || engineTarget.value?.game.status === "live") return;
  return engineContest.value?.games.some(game => game.status === "live") ? engineContest.value : firstLiveContest.value;
});
const automaticContest = computed(() => followedEngineId.value
  ? fallbackContest.value ?? engineContest.value
  : liveContest.value ?? firstLiveContest.value
    ?? playable.value.find(item => item.status === "live")
    ?? playable.value.at(-1));
const automaticGame = computed(() => followedEngineId.value && !fallbackContest.value
  ? engineTarget.value?.game
  : automaticContest.value && defaultGame(automaticContest.value));
const followedContest = computed(() => playable.value.find(item => item.games.some(game => game.id === followedGameId.value)));
const contest = computed(() => pinnedContest.value ?? followedContest.value ?? automaticContest.value);
const game = computed(() => pinnedContest.value?.games.find(item => String(item.id) === pinnedGame.value)
  ?? followedContest.value?.games.find(item => item.id === followedGameId.value)
  ?? automaticGame.value);
const activeTab = computed(() => route.query.tab === "games" || route.query.tab === "settings" ? route.query.tab : "standings");
const contestGames = computed(() => {
  const selected = contest.value;
  if (!selected || selected.tournament_id == null) return [];
  const tournamentId = selected.tournament_id;
  return selected.games.map(item => ({ ...item, tournament_id: tournamentId }));
});
const gameRoutes = computed(() => Object.fromEntries(contestGames.value.map(item => [item.id, gameTarget(item.id)])));
const settingsRows = computed(() => (viewer.value?.settings ?? []).map(row => {
  const [label, value] = Array.isArray(row) ? row : [row.label, row.value];
  return { label, value: label === "Variant" ? "Secret Queen" : value };
}));
const opening = computed(() => viewer.value?.opening ?? { name: "Start position", fen: "startpos" });
const positions = computed(() => buildPositions(opening.value.fen, moves.value.map(move => move.uci), undefined, moves.value.map(move => move.position_fen)));
const currentFen = computed(() => positionFen(positions.value[ply.value] ?? positions.value[0]!));
const historical = computed(() => game.value?.status === "finished" || ply.value < moves.value.length);
const displayedMoves = computed(() => reviewing.value || ply.value === moves.value.length ? moves.value : moves.value.slice(0, ply.value));
const concealedQueens = computed(() => {
  const starts = viewer.value?.secret_queen_squares;
  if (!starts) return;
  const squares: Record<Color, string | null> = { ...starts };
  for (let index = 0; index < ply.value; index += 1) {
    const move = moves.value[index];
    const board = positions.value[index + 1];
    if (!move || !board) break;
    for (const color of ["white", "black"] as const) {
      const square = squares[color];
      if (!square) continue;
      const destination = square === move.uci.slice(0, 2) ? move.uci.slice(2, 4) : square;
      const pawn = color === "white" ? "P" : "p";
      squares[color] = board.squares[squareIndex(destination)] === pawn ? destination : null;
    }
  }
  return squares;
});
const hiddenSearch = computed(() => Boolean(concealedQueens.value?.white || concealedQueens.value?.black));
const activeSide = computed(() => historical.value || !clock.value?.state.running ? null : clock.value.state.active_side);
const engines = computed(() => ({ white: game.value?.white_engine_id, black: game.value?.black_engine_id }));
const title = computed(() => game.value ? matchup(game.value) : props.detail.event.title);
const catchingUp = computed(() => !reviewing.value && ply.value < moves.value.length);
const status = computed(() => catchingUp.value ? game.value?.status === "finished" ? "Replaying" : "Catching up"
  : game.value?.result ? resultLabel(game.value.result) : game.value?.status === "live" ? "Live" : "Waiting for game");
const followingLive = computed(() => !pinnedContest.value && !reviewing.value && !catchingUp.value && game.value?.status === "live");
const followState = computed(() => {
  if (!followedEngineId.value) return "off";
  if (pinnedContest.value) return "paused";
  if (game.value?.status === "live") return includesEngine(game.value, Number(followedEngineId.value)) ? "live" : "fallback";
  if (game.value?.status === "assigned") return "preparing";
  return "waiting";
});
const followStateLabel = computed(() => ({
  off: "",
  paused: "Paused",
  live: "Following live",
  fallback: "Watching live while waiting",
  preparing: "Following next",
  waiting: game.value?.status === "finished" ? "Last game" : "Waiting for game",
})[followState.value]);
const annotations = computed(() => displayedMoves.value.map((_, index) => {
  if (capturedKing(index)) return { label: "King captured" };
  return queenRevealed(index) ? { label: "Queen revealed" } : null;
}));
const ending = computed(() => {
  if (!game.value?.result) return "";
  if (capturedKing(moves.value.length - 1)) return "King captured";
  const termination = viewer.value?.viewer_game?.termination;
  if (termination?.startsWith("win adjudication:")) return "Win by adjudication";
  if (termination?.startsWith("draw adjudication:")) return "Draw by adjudication";
  return termination ? termination.charAt(0).toUpperCase() + termination.slice(1) : "";
});
const gameContext = computed(() => {
  const control = viewer.value?.tournament.config?.time_control;
  return [
    contest.value?.title,
    game.value ? contest.value?.kind === "group" ? "Round " + game.value.round : "Pair " + game.value.pair_index : "",
    viewer.value?.viewer_game?.game_number ? "Game " + viewer.value.viewer_game.game_number : "",
    timeControlLabel(control),
    ending.value,
  ].filter(Boolean).join(" / ");
});

function defaultGame(current: SecretQueenContest): SecretQueenGame | undefined {
  return current.games.find(item => item.status === "live")
    ?? current.games.find(item => item.status === "assigned" || item.status === "pending")
    ?? current.games.at(-1);
}

function includesEngine(game: SecretQueenGame, id: number): boolean {
  return game.white_engine_id === id || game.black_engine_id === id;
}

function engineName(id: number): string {
  return names.value.get(id) ?? viewer.value?.engines[String(id)] ?? "Engine " + id;
}

function matchup(item: SecretQueenGame): string {
  return engineName(item.white_engine_id) + " vs " + engineName(item.black_engine_id);
}

function gameTarget(gameId?: number, tab?: "games") {
  const query = { ...route.query };
  if (gameId !== undefined) query.game_id = String(gameId);
  else delete query.game_id;
  if (tab) query.tab = tab;
  return { query };
}

function selectGame(gameId?: number): void {
  void router.push(gameTarget(gameId));
}

function selectContest(contestId: number, tab?: "games"): void {
  const selected = playable.value.find(item => item.id === contestId);
  if (selected) void router.push(gameTarget(defaultGame(selected)?.id, tab));
}

function followLive(resumeEngine = false): void {
  stopMovePlayback();
  reviewing.value = false;
  ply.value = moves.value.length;
  const { query } = gameTarget();
  const current = game.value;
  if (followedEngineId.value && (resumeEngine || !pinnedContest.value || (current && includesEngine(current, Number(followedEngineId.value))))) {
    if (resumeEngine) delete query.follow_live;
    else query.follow_live = "1";
    void router.push({ query });
    if (!pinnedGame.value && current?.status !== "live") advanceFollowingGame();
    return;
  }
  if (pinnedGame.value) {
    const selected = pinnedContest.value;
    delete query.follow_engine;
    delete query.follow_live;
    if (selected?.status === "live") query.follow_contest = selected.key;
    else delete query.follow_contest;
    void router.push({ query });
  } else if (game.value?.status === "finished") advanceFollowingGame();
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

function evaluationRange(info: string | null | undefined): EngineEvaluationRange | undefined {
  const fields = info?.match(/(?:^| )sq_eval ((?:cp:-?\d+|mate:-?\d+|win|loss)),((?:cp:-?\d+|mate:-?\d+|win|loss)),([0-8]),([0-8])(?= |$)/);
  if (!fields) return;
  const [, low, high, rawSampled, rawPossible] = fields;
  const sampled = Number(rawSampled);
  const possible = Number(rawPossible);
  const estimate = info?.match(/(?:^| )sq_score (cp:-?\d+|mate:-?\d+|win|loss)(?= |$)/)?.[1];
  if (low && high && sampled <= possible && (sampled > 0 || possible === 0)) return { low, high, sampled, possible, estimate };
}

function capturedKing(index: number): boolean {
  const move = moves.value[index];
  const before = positions.value[index];
  const after = positions.value[index + 1];
  if (!move?.position_fen || !before || !after) return false;
  const victim = before.squares[squareIndex(move.uci.slice(2, 4))];
  return (victim === "k" || victim === "K") && !after.squares.includes(victim);
}

function queenRevealed(index: number): boolean {
  const move = moves.value[index];
  if (!move?.position_fen || move.uci.length !== 4) return false;
  const source = positions.value[index]?.squares[squareIndex(move.uci.slice(0, 2))];
  const target = positions.value[index + 1]?.squares[squareIndex(move.uci.slice(2, 4))];
  return (source === "P" && target === "Q") || (source === "p" && target === "q");
}

function stopMovePlayback(): void {
  window.clearTimeout(moveTimer);
  moveTimer = undefined;
  lastMoveAt = undefined;
}

function seekMove(next: number): void {
  if (next === ply.value) return;
  stopMovePlayback();
  ply.value = next;
  reviewing.value = next < moves.value.length;
}

function moveDuration(index: number): number {
  const move = moves.value[index];
  const control = viewer.value?.tournament.config?.time_control;
  if (!move) return 0;
  if (control?.category === "increment" && move.clock_after_ms != null) {
    const previous = index < 2 ? control.initial_ms : moves.value[index - 2]?.clock_after_ms;
    if (previous != null) return Math.max(0, previous + control.increment_ms - move.clock_after_ms);
  }
  return Math.max(0, move.time_ms ?? 0);
}

function playNextMove(): void {
  if (moveTimer !== undefined || reviewing.value || ply.value >= moves.value.length) return;
  const delay = lastMoveAt === undefined ? 0 : lastMoveAt + moveDuration(ply.value) - performance.now();
  if (delay > 0) {
    moveTimer = window.setTimeout(() => {
      moveTimer = undefined;
      playNextMove();
    }, Math.ceil(delay));
    return;
  }
  const reveal = queenRevealed(ply.value) && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  ply.value += 1;
  lastMoveAt = performance.now();
  const duration = chessMoveAnimationMs + (reveal ? queenRevealAnimationMs : 0) + 80;
  moveTimer = window.setTimeout(() => {
    moveTimer = undefined;
    if (ply.value < moves.value.length) playNextMove();
    else if (resultTimer !== undefined) scheduleGameSwitch(resultDisplayMs);
  }, duration);
}

function acceptMoves(next: MoveRecord[], snapshot = false): void {
  const replace = next.length < moves.value.length || (snapshot && !historyLoaded);
  historyLoaded = true;
  moves.value = next;
  if (replace) {
    stopMovePlayback();
    reviewing.value = false;
    ply.value = next.length;
    return;
  }
  playNextMove();
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
    const response = await api.get<SecretQueenViewer>(
      "/api/events/" + encodeURIComponent(props.detail.event.slug) + "/tournaments/" + tournament,
      { query: { game_id: id, page_size: 25 }, signal: pending.signal },
    );
    if (pending.signal.aborted || game.value?.id !== id) return;
    viewer.value = response;
    if (response.viewer_moves.length >= moves.value.length || response.viewer_game?.status === "pending") {
      acceptMoves(response.viewer_moves, true);
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

function advanceFollowingGame(): void {
  window.clearTimeout(resultTimer);
  resultTimer = undefined;
  followedGameId.value = automaticGame.value?.id;
}

function scheduleGameSwitch(delay: number): void {
  window.clearTimeout(resultTimer);
  resultTimer = window.setTimeout(() => {
    if (moveTimer !== undefined) scheduleGameSwitch(resultDisplayMs);
    else advanceFollowingGame();
  }, delay);
}

watch([pinnedGame, followedEngineId, () => liveContest.value?.key, watchLiveWhileWaiting, () => automaticGame.value?.id, () => game.value?.status], ([pinned, engine, contestKey, fallback, next], [previousPin, previousEngine, previousContestKey, previousFallback]) => {
  const current = game.value;
  if (pinned !== null || previousPin || engine !== previousEngine || contestKey !== previousContestKey || fallback !== previousFallback || !current || current.id !== followedGameId.value) {
    advanceFollowingGame();
    return;
  }
  if (current.status === "live") {
    if (engineTarget.value?.game.status === "live" && next !== current.id) advanceFollowingGame();
    return;
  }
  if (current.status !== "finished" || current.id === next) {
    advanceFollowingGame();
    return;
  }
  if (resultTimer !== undefined) return;
  const finishedId = current.id;
  scheduleGameSwitch(resultDisplayMs + 2000);
  request?.abort();
  request = null;
  void loadViewer().then(() => {
    if (resultTimer === undefined || game.value?.id !== finishedId) return;
    scheduleGameSwitch(resultDisplayMs);
  });
}, { immediate: true });

watch([() => contest.value?.tournament_id, () => game.value?.id], ([tournament, id]) => {
  stream?.close();
  stream = null;
  request?.abort();
  request = null;
  stopMovePlayback();
  historyLoaded = false;
  reviewing.value = false;
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
    if (snapshot.moves && (snapshot.moves.length >= moves.value.length || snapshot.game?.status === "pending")) acceptMoves(snapshot.moves, true);
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
  window.clearTimeout(resultTimer);
  stopMovePlayback();
});
</script>

<template>
  <EventArena class="secret-queen-arena" :title="detail.event.title" :slug="detail.event.slug" :status="status" :game-context="gameContext"
    :spectator-count="detail.spectator_count" :can-follow-live="!followingLive && ['live', 'intermission'].includes(detail.event.status)"
    :error="error" @follow-live="followLive()" @retry="loadViewer">
    <template #title-leading><SecretQueenInfo compact /></template>
    <template #selectors>
      <FollowEnginePicker v-if="followOptions.length" v-model="followedEngineId" class="secret-queen-follow"
        :engines="followOptions" :state="followState" :state-label="followStateLabel" @resume="followLive(true)" />
      <label v-if="playable.length > 1">Group or match
        <select :value="contest?.id" @change="selectContest(Number(($event.target as HTMLSelectElement).value))">
          <option v-for="item in playable" :key="item.id" :value="item.id">{{ item.title }}</option>
        </select>
      </label>
      <label v-if="contest">Game
        <select :value="game?.id" @change="selectGame(Number(($event.target as HTMLSelectElement).value))">
          <option v-for="item in contest.games" :key="item.id" :value="item.id">
            {{ contest.kind === 'group' ? 'Round ' + item.round : 'Pair ' + item.pair_index }} · {{ matchup(item) }}{{ item.result ? ' (' + item.result + ')' : '' }}
          </option>
        </select>
      </label>
    </template>
    <template #actions>
      <RouterLink :to="{ query: { ...route.query, tab: 'standings' }, hash: '#secret-queen-arena-info' }" class="event-page-link">
        <AppIcon name="trophy" :size="15" /> Groups &amp; bracket
      </RouterLink>
    </template>
    <template #engines>
      <div v-for="side in (['black', 'white'] as const)" :key="side" class="relay-engine-slot">
        <EnginePanel :side="side" :engine-id="engines[side] ?? null" :name="engines[side] === undefined ? '' : engineName(engines[side]!)"
          :clock="panels[side].clock" :analysis="panels[side].analysis" :position-fen="currentFen" :active="activeSide === side"
          :hidden-search="hiddenSearch" :evaluation-range="evaluationRange(panels[side].analysis?.info)" />
      </div>
    </template>
    <template #board>
      <ChessViewer :key="game?.id ?? 'pending'" :model-value="ply" :opening="opening" :moves="displayedMoves" :label="title"
        :finished="game?.status === 'finished' && ply === moves.length" :concealed-queens="concealedQueens"
        animate-queen-reveal @update:model-value="seekMove" />
    </template>
    <template #activity>
      <MoveList :model-value="ply" :moves="displayedMoves.map(move => move.san || move.uci)"
        :uci-moves="displayedMoves.map(move => move.uci)" :fen="opening.fen" :annotations="annotations" @update:model-value="seekMove" />
      <ChatPanel :messages="detail.chat_messages" :settings="detail.chat_settings" :event-slug="detail.event.slug"
        :game-id="game?.id ?? null" @sent="appendChat" />
    </template>
    <section id="secret-queen-arena-info" class="tournament-data">
      <nav class="data-tabs" aria-label="Tournament information">
        <RouterLink v-for="tab in (['standings', 'games', 'settings'] as const)" :key="tab"
          :to="{ query: { ...route.query, tab } }" :aria-current="activeTab === tab ? 'page' : undefined">
          {{ tab }}<span v-if="tab === 'games'">{{ contestGames.length }}</span>
        </RouterLink>
      </nav>
      <SecretQueenStandings v-if="activeTab === 'standings'" :contests="contests" :cast="detail.cast"
        :selected-contest="contest?.id ?? null" @select="selectContest($event, 'games')" />
      <section v-else-if="activeTab === 'games'" class="data-panel" aria-labelledby="secret-queen-games">
        <header><h2 id="secret-queen-games">{{ contest?.title ?? 'Games' }}</h2></header>
        <GameTable v-if="contestGames.length" :games="contestGames" :engines="Object.fromEntries(names)"
          :game-routes="gameRoutes" :participant-links="false" caption="Secret Queen games in the selected contest" />
        <ContentState v-else kind="empty" compact title="No games scheduled" />
      </section>
      <section v-else class="data-panel" aria-labelledby="secret-queen-settings">
        <header><h2 id="secret-queen-settings">Settings</h2></header>
        <dl v-if="settingsRows.length" class="settings-list">
          <div v-for="row in settingsRows" :key="row.label"><dt>{{ row.label }}</dt><dd>{{ row.value }}</dd></div>
        </dl>
        <ContentState v-else :kind="contest ? 'loading' : 'empty'" compact :title="contest ? 'Loading settings' : 'No contest settings yet'" />
      </section>
    </section>
  </EventArena>
</template>

<style scoped>
.secret-queen-arena :deep(.tournament-heading__controls),
.secret-queen-arena :deep(.relay-broadcast__selectors) { flex-wrap: wrap; min-width: 0; }
.secret-queen-follow { width: 14rem; max-width: 100%; }
@media (max-width: 40rem) { .secret-queen-follow { width: 100%; } }
.tournament-data { display: grid; gap: var(--space-md, 1rem); min-width: 0; }
.data-tabs { display: flex; gap: .3rem; overflow-x: auto; }
.data-tabs a { display: inline-flex; min-height: 2.35rem; align-items: center; gap: .45rem; padding-inline: .85rem; border: 1px solid transparent; border-radius: var(--radius-sm, .35rem); color: var(--color-text-muted); font-size: .78rem; font-weight: 730; text-decoration: none; text-transform: capitalize; }
.data-tabs a:hover { color: var(--color-text); background: color-mix(in srgb, var(--color-text) 5%, transparent); }
.data-tabs a[aria-current="page"] { border-color: color-mix(in srgb, var(--color-accent) 24%, transparent); background: color-mix(in srgb, var(--color-accent) 9%, var(--color-surface)); color: var(--color-accent); }
.data-tabs a span { font-size: .64rem; opacity: .75; }
.data-panel { overflow: hidden; border: 1px solid var(--color-border); border-radius: var(--radius-lg, .75rem); background: var(--color-surface); }
.data-panel > header { padding: var(--space-md, 1rem); border-block-end: 1px solid var(--color-border); }
.data-panel h2 { margin: 0; font-size: 1rem; }
.settings-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 15rem), 1fr)); gap: 1px; margin: 0; background: var(--color-border); }
.settings-list div { min-width: 0; padding: .85rem 1rem; background: var(--color-surface); }
.settings-list dt { color: var(--color-text-muted); font-size: .68rem; font-weight: 750; }
.settings-list dd { margin: .22rem 0 0; font-size: .82rem; font-weight: 650; overflow-wrap: anywhere; }
</style>
