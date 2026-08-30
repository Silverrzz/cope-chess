<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter, type RouteLocationRaw } from "vue-router";

import { api } from "@/api/client";
import ChessViewer from "@/components/chess/ChessViewer.vue";
import MoveList from "@/components/chess/MoveList.vue";
import { buildPositions, parseFen, positionFen, type BoardArrow } from "@/components/chess/chess";
import ChatPanel from "@/components/public/ChatPanel.vue";
import ContentState from "@/components/public/ContentState.vue";
import EnginePanel from "@/components/public/EnginePanel.vue";
import GameTable from "@/components/public/GameTable.vue";
import StreamIndicator from "@/components/public/StreamIndicator.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import { useViewerSettings } from "@/composables/useViewerSettings";
import { clockLabel, errorMessage, moveEvaluation, moveNps, resultLabel, scorePercentLabel, statusLabel } from "@/components/public/format";
import type {
  ClockState,
  EngineAnalysis,
  GameRecord,
  Identifier,
  MoveRecord,
  TournamentDetailResponse,
} from "@/components/public/types";
import type { EventDetailResponse } from "@/types/events";

import type { EngineRelayPayload, RelayRosterMember, RelayTeam } from "./types";

type TabKey = "standings" | "games" | "settings";

interface CheerPiece {
  id: number;
  startX: number;
  startY: number;
  endX: number;
  endY: number;
  rotation: number;
  delay: number;
  duration: number;
  size: number;
}

interface CheerBurst {
  id: string;
  color: string;
  side: "left" | "right";
  x: number;
  y: number;
  pieces: CheerPiece[];
}

interface QueuedCheer {
  id: string;
  teamId: number;
  side: "left" | "right" | null;
}

const props = withDefaults(defineProps<{ detail: EventDetailResponse; clockOffsetMs?: number; view?: "event" | "arena" }>(), { clockOffsetMs: 0, view: "event" });
const { confettiEnabled } = useViewerSettings();
const route = useRoute();
const router = useRouter();

const payload = computed(() => props.detail.custom as EngineRelayPayload);
const isFinale = computed(() => props.detail.event.handler_key === "engine-relay-finale");
const fixtureId = ref<number | null>(null);
const gameId = ref<string>("");
const gameData = ref<TournamentDetailResponse | null>(null);
const loading = ref(false);
const loadError = ref("");
const selectedPly = ref(0);
const currentPositionFen = ref("startpos");
const streamState = ref<"connecting" | "live" | "reconnecting" | "closed">("closed");
const nowMs = ref(Date.now() + props.clockOffsetMs);
const countdownFinished = ref(false);
const countdownBeat = ref(-1);
const soundState = ref<"armed" | "loading" | "playing" | "blocked" | "unavailable" | "finished">("loading");
const soundRequested = ref(false);
const cheerBursts = ref<CheerBurst[]>([]);
const selectedTeamId = ref<number | null>(null);

let controller: AbortController | null = null;
let stream: EventSource | null = null;
let streamKey = "";
let refreshTimer: number | undefined;
let countdownTimer: number | undefined;
let countdownFrame: number | undefined;
let countdownAudio: HTMLAudioElement | null = null;
let audioPlayPending = false;
let countdownAudioStarted = false;
const soundPrimed = ref(false);
let lastCheerRequestAt = 0;
let cheerBatchTimer: number | undefined;
const seenCheerIds = new Set<string>();
const cheerTimers = new Set<number>();
const queuedCheers: QueuedCheer[] = [];
const headingElement = ref<HTMLElement | null>(null);
const arenaElement = ref<HTMLElement | null>(null);
const boardColumnElement = ref<HTMLElement | null>(null);
let arenaFitFrame: number | undefined;
let headingResizeObserver: ResizeObserver | null = null;

const countdownAudioUrl = "/audio/openbench-engine-clash-countdown.wav";
const countdownAudioLengthMs = 60_000;
const cheerClientId = typeof globalThis.crypto?.randomUUID === "function"
  ? globalThis.crypto.randomUUID()
  : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;

const fixture = computed(() => payload.value.fixtures.find((item) => item.id === fixtureId.value) ?? payload.value.fixtures[0] ?? null);
const selectedRelayGame = computed(() => fixture.value?.games.find((game) => String(game.id) === gameId.value) ?? null);
const viewerGame = computed(() => gameData.value?.viewer_game ?? selectedRelayGame.value);
const moves = computed(() => gameData.value?.viewer_moves ?? []);
const opening = computed(() => gameData.value?.opening ?? { name: "Start position", fen: "startpos" });
const bookPlies = computed(() => moves.value.filter((move) => move.is_book).length);
const activeSide = computed<"white" | "black" | null>(() => gameData.value?.clock_state?.running ? gameData.value.clock_state.active_side ?? null : null);
const whiteTeam = computed(() => teamForSide("white"));
const blackTeam = computed(() => teamForSide("black"));
const whiteMember = computed(() => currentMember("white"));
const blackMember = computed(() => currentMember("black"));
const whiteAnalysis = ref<EngineAnalysis | null>(null);
const blackAnalysis = ref<EngineAnalysis | null>(null);
const isLatestPly = computed(() => selectedPly.value >= moves.value.length);
const latestPositionFen = computed(() => positionFen(buildPositions(opening.value.fen, moves.value.map((move) => move.uci)).at(-1)!));
const displayedWhiteAnalysis = computed(() => isFinale.value ? analysisForSide("white") : whiteAnalysis.value);
const displayedBlackAnalysis = computed(() => isFinale.value ? analysisForSide("black") : blackAnalysis.value);
const whiteCardMember = computed(() => memberForAnalysis("white", displayedWhiteAnalysis.value) ?? whiteMember.value);
const blackCardMember = computed(() => memberForAnalysis("black", displayedBlackAnalysis.value) ?? blackMember.value);
const whiteRoster = computed(() => rotatingRoster(whiteTeam.value, whiteMember.value));
const blackRoster = computed(() => rotatingRoster(blackTeam.value, blackMember.value));
const winningTeam = computed(() => {
  const winnerId = payload.value.fixtures.find((item) => item.winner_team_id !== null)?.winner_team_id;
  return payload.value.teams.find((team) => team.id === winnerId) ?? null;
});
const finaleComplete = computed(() => isFinale.value && props.detail.event.status === "completed" && winningTeam.value !== null);
const kibitzerAnalysis = computed(() => {
  const analysis = gameData.value?.engine_data?.kibitzer;
  return isLatestPly.value && analysis && samePosition(analysis.root_fen, latestPositionFen.value) ? analysis : null;
});
const kibitzerLabel = computed(() => {
  const analysis = kibitzerAnalysis.value;
  if (!analysis) return "—";
  const factor = parseFen(analysis.root_fen || currentPositionFen.value).turn === "b" ? -1 : 1;
  if (analysis.eval_mate !== null && analysis.eval_mate !== undefined) return `#${factor * analysis.eval_mate}`;
  const cp = Number(analysis.eval_cp);
  return Number.isFinite(cp) ? `${factor * cp >= 0 ? "+" : ""}${(factor * cp / 100).toFixed(2)}` : String(analysis.eval ?? "—");
});
const kibitzerPercent = computed(() => {
  const analysis = kibitzerAnalysis.value;
  if (!analysis) return 50;
  const blackToMove = parseFen(analysis.root_fen || currentPositionFen.value).turn === "b";
  if (analysis.eval_mate !== null && analysis.eval_mate !== undefined) {
    const whiteMate = blackToMove ? -analysis.eval_mate : analysis.eval_mate;
    return whiteMate > 0 ? 98 : 2;
  }
  const raw = Number(analysis.eval_cp);
  if (!Number.isFinite(raw)) return 50;
  const whiteCp = blackToMove ? -raw : raw;
  return Math.max(2, Math.min(98, 50 + 48 * Math.tanh(whiteCp / 500)));
});
const moveAnnotations = computed(() => moves.value.map((move, index) => {
  if (!isFinale.value) return null;
  if (move.is_book) return { label: "Opening book" };
  const side = moveSide(index);
  const team = side === "white" ? whiteTeam.value : blackTeam.value;
  const member = team?.roster.find((item) => String(item.engine_id) === String(move.engine_version_id ?? ""));
  return member ? { label: member.display_name, color: team!.primary_color } : null;
}));
const suggestedMoves = computed<BoardArrow[]>(() => {
  const positions = buildPositions(opening.value.fen, moves.value.map((move) => move.uci));
  const viewedPly = Math.max(0, Math.min(selectedPly.value, moves.value.length));
  const viewedFen = positionFen(positions[viewedPly]!);
  const sideToMove = parseFen(viewedFen).turn === "w" ? "white" : "black";
  const arrows: BoardArrow[] = [];
  const previousMove = viewedPly > 0 ? moves.value[viewedPly - 1] : undefined;

  if (previousMove) {
    const fallbackPv = movePv(previousMove);
    const playedMove = normalizedUci(previousMove.uci);
    if (fallbackPv[0] === playedMove && fallbackPv[1]) {
      arrows.push(teamArrow(fallbackPv[1], moveSide(viewedPly - 1)));
    }
  }

  const nextMove = moves.value[viewedPly];
  if (nextMove) {
    const nextPv = movePv(nextMove);
    if (nextPv[0]) arrows.push(teamArrow(nextPv[0], sideToMove));
  } else if (viewerGame.value?.status === "live") {
    const currentAnalysis = gameData.value?.engine_data?.[sideToMove];
    if (samePosition(currentAnalysis?.root_fen, viewedFen)) {
      const currentPv = pvMoves(currentAnalysis?.info);
      const fallbackPv = currentPv.length ? currentPv : pvMoves(currentAnalysis?.pv);
      if (fallbackPv[0]) arrows.push(teamArrow(fallbackPv[0], sideToMove));
    }
  }

  return arrows;
});
const gameStatus = computed(() => viewerGame.value?.status ?? fixture.value?.tournament?.status ?? "draft");
const gameLabel = computed(() => {
  if (!viewerGame.value) return "Awaiting schedule";
  if (viewerGame.value.result) return resultLabel(viewerGame.value.result);
  return statusLabel(viewerGame.value.status);
});
const isLive = computed(() => props.detail.event.status === "live" || gameStatus.value === "live" || fixture.value?.tournament?.status === "running");
const targetTime = computed(() => {
  const value = fixture.value?.tournament?.scheduled_start_at ?? props.detail.event.scheduled_start_at;
  const parsed = value ? Date.parse(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : null;
});
const remainingMs = computed(() => Math.max(0, (targetTime.value ?? nowMs.value) - nowMs.value));
const countdownVisible = computed(() => targetTime.value !== null && remainingMs.value > 0 && !countdownFinished.value);
const finalMinuteActive = computed(() => countdownVisible.value && remainingMs.value <= countdownAudioLengthMs);
const countdown = computed(() => {
  const totalSeconds = Math.ceil(remainingMs.value / 1000);
  const parts = [
    { label: "Days", value: Math.floor(totalSeconds / 86400) },
    { label: "Hours", value: Math.floor((totalSeconds % 86400) / 3600) },
    { label: "Minutes", value: Math.floor((totalSeconds % 3600) / 60) },
    { label: "Seconds", value: totalSeconds % 60 },
  ];
  const firstVisible = parts.findIndex((part) => part.value > 0);
  return parts.slice(firstVisible < 0 ? parts.length - 1 : firstVisible);
});
const scheduleLabel = computed(() => {
  if (isLive.value) return "The relay is live now";
  if (props.detail.event.status === "completed") return "The relay has concluded";
  if (!targetTime.value) return "Start time to be announced";
  return `Counting down to ${new Intl.DateTimeFormat(undefined, { weekday: "long", hour: "numeric", minute: "2-digit" }).format(new Date(targetTime.value))} (your local time)`;
});
const spectatorCount = computed(() => props.detail.spectator_count ?? gameData.value?.spectator_count ?? 0);
const systemStatus = computed(() => {
  if (props.detail.event.status === "completed") return "Relay concluded";
  if (isLive.value) return "Relay live";
  return payload.value.fixtures.length ? "All systems ready" : "Relay setup in progress";
});
const showcaseTeams = computed(() => payload.value.teams.map((team) => {
  const side = whiteTeam.value?.id === team.id ? "white" : blackTeam.value?.id === team.id ? "black" : null;
  return {
    id: team.id,
    name: team.name,
    engineCount: team.roster.length,
    color: team.primary_color,
    secondary: team.secondary_color,
    thinking: side !== null && activeSide.value === side,
    current: side !== null,
  };
}).slice(0, 4));
const selectedTeam = computed(() => payload.value.teams.find((team) => team.id === selectedTeamId.value) ?? null);
const gamePage = computed(() => Math.max(1, Number(queryValue(route.query.page)) || 1));
const gameTotal = computed(() => gameData.value?.game_pagination.total || 0);
const gamePages = computed(() => gameData.value?.game_pagination.pages || 1);
const activeTab = computed<TabKey>(() => {
  const tab = queryValue(route.query.tab);
  return ["standings", "games", "settings"].includes(tab) ? tab as TabKey : "standings";
});
const format = computed(() => {
  const value = gameData.value?.tournament.config?.format;
  return typeof value === "string" ? value : value?.value || "";
});
const settingsRows = computed(() => (gameData.value?.settings || []).map((row) => Array.isArray(row)
  ? { label: String(row[0]), value: String(row[1]) }
  : { label: String(row.label), value: String(row.value) }));
const teamsByAnchor = computed(() => {
  const entries: Array<[string, RelayTeam]> = [];
  const selectedFixture = fixture.value;
  if (!selectedFixture) return new Map(entries);
  for (const fixtureTeam of selectedFixture.teams || []) {
    const team = payload.value.teams.find((item) => item.id === fixtureTeam.id);
    if (team) entries.push([String(fixtureTeam.anchor_engine_id), team]);
  }
  if (entries.length) return new Map(entries);
  const teamA = payload.value.teams.find((team) => team.id === selectedFixture.team_a_id);
  const teamB = payload.value.teams.find((team) => team.id === selectedFixture.team_b_id);
  if (teamA) entries.push([String(selectedFixture.anchor_a_engine_id), teamA]);
  if (teamB) entries.push([String(selectedFixture.anchor_b_engine_id), teamB]);
  return new Map(entries);
});
const teamNames = computed(() => Object.fromEntries(
  [...teamsByAnchor.value].map(([engineId, team]) => [engineId, team.name]),
));
const teamStandings = computed(() => (gameData.value?.standings || []).map((standing) => {
  const team = teamsByAnchor.value.get(String(standing.engine_id));
  return { ...standing, name: team?.name ?? standing.name, team_id: team?.id ?? standing.engine_id };
}));
const teamHardwareRows = computed(() => (gameData.value?.engine_hardware || []).map((row) => ({
  ...row,
  name: teamsByAnchor.value.get(String(row.engine_id))?.name ?? row.name,
})));
const relayGameRoutes = computed<Record<string, RouteLocationRaw>>(() => Object.fromEntries(
  (gameData.value?.games || []).map((game) => [String(game.id), {
    name: "event-arena",
    params: { slug: props.detail.event.slug },
    query: { game_id: String(game.id) },
    hash: "#relay-arena",
  }]),
));

function rotatingRoster(team: RelayTeam | null, current: RelayRosterMember | null): RelayRosterMember[] {
  const roster = team?.roster ?? [];
  if (!current) return roster;
  const currentIndex = roster.findIndex((member) => member.id === current.id);
  return currentIndex < 0 ? roster : [...roster.slice(currentIndex), ...roster.slice(0, currentIndex)];
}

function teamArrow(move: string, color: "white" | "black"): BoardArrow {
  if (!isFinale.value) {
    const team = color === "white" ? whiteTeam.value : blackTeam.value;
    if (team) return { move, color, fillColor: team.primary_color };
  }
  return { move, color };
}

function moveSide(index: number): "white" | "black" {
  const first = parseFen(opening.value.fen).turn === "w" ? "white" : "black";
  return index % 2 === 0 ? first : first === "white" ? "black" : "white";
}

function pvMoves(value: string | null | undefined): string[] {
  if (!value) return [];
  const parts = value.trim().split(/\s+/);
  const pvIndex = parts.indexOf("pv");
  if (parts[0] === "info" && pvIndex < 0) return [];
  return parts
    .slice(pvIndex >= 0 ? pvIndex + 1 : 0)
    .map(normalizedUci)
    .filter((move) => /^[a-h][1-8][a-h][1-8][qrbn]?$/.test(move));
}

function movePv(move: MoveRecord): string[] {
  const infoPv = pvMoves(move.info_line);
  return infoPv.length ? infoPv : pvMoves(move.pv);
}

function analysisForSide(side: "white" | "black"): EngineAnalysis | null {
  if (isLatestPly.value) return side === "white" ? whiteAnalysis.value : blackAnalysis.value;
  const moveIndex = latestMoveIndexForSide(side, selectedPly.value);
  const move = moveIndex >= 0 ? moves.value[moveIndex] : undefined;
  if (!move) return null;
  const analysis: EngineAnalysis = {
    nps: moveNps(move),
    eval: moveEvaluation(move),
    root_fen: positionFen(buildPositions(
      opening.value.fen,
      moves.value.slice(0, moveIndex).map((item) => item.uci),
    ).at(-1)!),
  };
  if (move.engine_version_id !== undefined) analysis.engine_id = move.engine_version_id;
  if (move.eval_cp !== undefined) analysis.eval_cp = move.eval_cp;
  if (move.eval_mate !== undefined) analysis.eval_mate = move.eval_mate;
  if (move.depth !== undefined) analysis.depth = move.depth;
  if (move.seldepth !== undefined) analysis.seldepth = move.seldepth;
  if (move.nodes !== undefined) analysis.nodes = move.nodes;
  if (move.hashfull !== undefined) analysis.hashfull = move.hashfull;
  if (move.pv !== undefined) analysis.pv = move.pv;
  if (move.pv_san !== undefined) analysis.pv_san = move.pv_san;
  const info = move.info_line || move.pv;
  if (info !== undefined) analysis.info = info;
  return analysis;
}

function latestMoveIndexForSide(side: "white" | "black", ply: number): number {
  for (let index = Math.min(ply, moves.value.length) - 1; index >= 0; index -= 1) {
    if (moveSide(index) === side) return index;
  }
  return -1;
}

function normalizedUci(value: string): string {
  return value.trim().toLowerCase();
}

function samePosition(left: string | null | undefined, right: string): boolean {
  if (!left) return false;
  return left.trim().split(/\s+/).slice(0, 4).join(" ") === right.trim().split(/\s+/).slice(0, 4).join(" ");
}

watch(gameId, () => {
  whiteAnalysis.value = null;
  blackAnalysis.value = null;
});
watch(() => gameData.value?.engine_data?.white ?? null, (analysis) => {
  if (analysis) whiteAnalysis.value = analysis;
}, { immediate: true });
watch(() => gameData.value?.engine_data?.black ?? null, (analysis) => {
  if (analysis) blackAnalysis.value = analysis;
}, { immediate: true });
onMounted(() => {
  selectInitial();
  window.addEventListener("cope:event-cheer", handleCheerEvent as EventListener);
  if (props.view === "arena") {
    headingResizeObserver = new ResizeObserver(scheduleArenaFit);
    if (headingElement.value) headingResizeObserver.observe(headingElement.value);
    window.addEventListener("resize", scheduleArenaFit);
    window.visualViewport?.addEventListener("resize", scheduleArenaFit);
    scheduleArenaFit();
    return;
  }
  restoreCountdownCompletion();
  prepareCountdownAudio();
  syncCountdown();
  countdownTimer = window.setInterval(syncCountdown, 100);
  document.addEventListener("visibilitychange", handleCountdownResume);
  window.addEventListener("focus", handleCountdownResume);
  window.addEventListener("pageshow", handleCountdownResume);
  window.addEventListener("pointerdown", handleUserActivation, { passive: true });
  window.addEventListener("keydown", handleUserActivation);
  document.addEventListener("pointerdown", handleTeamPanelPointer, true);
  window.addEventListener("keydown", handleTeamPanelKeydown);
});
onBeforeUnmount(() => {
  closeStream();
  if (countdownTimer !== undefined) window.clearInterval(countdownTimer);
  if (countdownFrame !== undefined) window.cancelAnimationFrame(countdownFrame);
  document.removeEventListener("visibilitychange", handleCountdownResume);
  window.removeEventListener("focus", handleCountdownResume);
  window.removeEventListener("pageshow", handleCountdownResume);
  window.removeEventListener("pointerdown", handleUserActivation);
  window.removeEventListener("keydown", handleUserActivation);
  document.removeEventListener("pointerdown", handleTeamPanelPointer, true);
  window.removeEventListener("keydown", handleTeamPanelKeydown);
  window.removeEventListener("cope:event-cheer", handleCheerEvent as EventListener);
  if (arenaFitFrame !== undefined) window.cancelAnimationFrame(arenaFitFrame);
  headingResizeObserver?.disconnect();
  window.removeEventListener("resize", scheduleArenaFit);
  window.visualViewport?.removeEventListener("resize", scheduleArenaFit);
  if (cheerBatchTimer !== undefined) window.clearTimeout(cheerBatchTimer);
  queuedCheers.length = 0;
  for (const timer of cheerTimers) window.clearTimeout(timer);
  cheerTimers.clear();
  releaseCountdownAudio();
});

watch(() => payload.value.fixtures, () => selectInitial(), { deep: true });
watch([fixtureId, gameId], () => { void loadGame(); });
watch(gamePage, () => { void loadGame(); });
watch(() => queryValue(route.query.game_id), (requestedGameId) => {
  if (!requestedGameId || requestedGameId === gameId.value) return;
  const requestedFixture = payload.value.fixtures.find((item) => item.games.some((game) => String(game.id) === requestedGameId));
  if (!requestedFixture) return;
  fixtureId.value = requestedFixture.id;
  gameId.value = requestedGameId;
  gameData.value = null;
});
watch(targetTime, (value, previous) => {
  if (value !== previous) resetCountdownForTarget();
});
watch(headingElement, (next, previous) => {
  if (previous) headingResizeObserver?.unobserve(previous);
  if (next) headingResizeObserver?.observe(next);
  scheduleArenaFit();
}, { flush: "post" });
watch([arenaElement, boardColumnElement, gameData], () => scheduleArenaFit(), { flush: "post" });

function scheduleArenaFit(): void {
  if (props.view !== "arena") return;
  if (arenaFitFrame !== undefined) window.cancelAnimationFrame(arenaFitFrame);
  arenaFitFrame = window.requestAnimationFrame(() => {
    arenaFitFrame = undefined;
    void fitArenaToViewport();
  });
}

async function fitArenaToViewport(): Promise<void> {
  await nextTick();
  const arena = arenaElement.value;
  const boardColumn = boardColumnElement.value;
  if (!arena || !boardColumn) return;
  const viewer = boardColumn.querySelector<HTMLElement>(".viewer-shell");
  const board = viewer?.querySelector<HTMLElement>(".board-mount");
  if (!viewer || !board) return;
  const viewport = window.visualViewport;
  const viewportHeight = viewport?.height ?? window.innerHeight;
  const arenaDocumentTop = arena.getBoundingClientRect().top + window.scrollY;
  const availableHeight = Math.floor(viewportHeight - arenaDocumentTop - 8);
  if (availableHeight <= 0) return;
  const chrome = Math.max(0, viewer.getBoundingClientRect().height - board.getBoundingClientRect().height);
  const rootFontSize = Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  const boardSize = Math.max(0, Math.min(42 * rootFontSize, Math.floor(availableHeight - chrome)));
  arena.style.setProperty("--arena-content-height", `${availableHeight}px`);
  arena.style.setProperty("--arena-board-size", `${boardSize}px`);
}

function selectInitial(): void {
  const fixtures = payload.value.fixtures;
  if (!fixtures.length) {
    fixtureId.value = null;
    gameId.value = "";
    gameData.value = null;
    return;
  }
  const requestedGameId = queryValue(route.query.game_id);
  const requestedFixture = requestedGameId
    ? fixtures.find((item) => item.games.some((game) => String(game.id) === requestedGameId))
    : undefined;
  if (requestedFixture && fixtureId.value === null && !gameId.value) {
    fixtureId.value = requestedFixture.id;
    gameId.value = requestedGameId;
    return;
  }
  if (!fixtures.some((item) => item.id === fixtureId.value)) {
    const preferred = fixtures.find((item) => item.games.some((game) => game.status === "live"))
      ?? fixtures.find((item) => ["running", "scheduled"].includes(item.tournament?.status ?? ""))
      ?? fixtures[0]!;
    fixtureId.value = preferred.id;
  }
  chooseGame();
}

function chooseGame(): void {
  const games = fixture.value?.games ?? [];
  const current = games.find((game) => String(game.id) === gameId.value);
  if (current && ["live", "assigned"].includes(current.status)) return;
  const active = games.find((game) => game.status === "live")
    ?? games.find((game) => game.status === "assigned")
  if (active) {
    activateGame(active.id);
    return;
  }
  if (current) return;
  const preferred = games.find((game) => game.status === "pending") ?? games.at(-1);
  gameId.value = preferred ? String(preferred.id) : "";
}

function selectFixture(value: string): void {
  fixtureId.value = Number(value);
  gameId.value = "";
  gameData.value = null;
  chooseGame();
}

function activateGame(value: Identifier): void {
  const nextGameId = String(value);
  if (nextGameId === gameId.value) return;
  gameId.value = nextGameId;
  gameData.value = null;
  if (queryValue(route.query.game_id) !== nextGameId) {
    void router.replace({ query: { ...route.query, game_id: nextGameId } });
  }
}

async function loadGame(silent = false): Promise<void> {
  if (props.view !== "arena") return;
  controller?.abort();
  if (!fixture.value || !gameId.value || fixture.value.tournament?.status === "draft") {
    closeStream();
    gameData.value = null;
    loading.value = false;
    return;
  }
  controller = new AbortController();
  if (!silent) loading.value = true;
  loadError.value = "";
  try {
    const response = await api.get<TournamentDetailResponse>(`/api/events/${encodeURIComponent(props.detail.event.slug)}/tournaments/${fixture.value.tournament_id}`, {
      query: { game_id: gameId.value, page: gamePage.value },
      signal: controller.signal,
    });
    const wasLatest = selectedPly.value >= (gameData.value?.viewer_moves.length ?? 0);
    gameData.value = response;
    if (wasLatest || !silent) selectedPly.value = response.viewer_moves.length;
    else selectedPly.value = Math.min(selectedPly.value, response.viewer_moves.length);
    connectStream();
  } catch (cause) {
    if ((cause as { name?: string }).name !== "AbortError") loadError.value = errorMessage(cause, "The relay game could not be loaded.");
  } finally {
    loading.value = false;
  }
}

function connectStream(): void {
  if (!fixture.value || !gameId.value || typeof EventSource === "undefined") return;
  const nextKey = `${fixture.value.tournament_id}:${gameId.value}`;
  if (stream && streamKey === nextKey) return;
  closeStream();
  streamKey = nextKey;
  stream = new EventSource(`/events/${encodeURIComponent(props.detail.event.slug)}/tournaments/${fixture.value.tournament_id}/stream?game_id=${encodeURIComponent(gameId.value)}&spectator=0`);
  streamState.value = "connecting";
  stream.onopen = () => { streamState.value = "live"; };
  stream.onerror = () => { streamState.value = "reconnecting"; };
  stream.addEventListener("game.move", handleMove);
  stream.addEventListener("engine.info", handleEngineInfo);
  stream.addEventListener("clock.sync", handleClock);
  stream.addEventListener("spectators.changed", handleSpectators);
  stream.addEventListener("tournament.snapshot", handleTournamentSnapshot);
  stream.addEventListener("tournament.changed", scheduleRefresh);
}

function closeStream(): void {
  stream?.close();
  stream = null;
  streamKey = "";
  streamState.value = "closed";
  if (refreshTimer !== undefined) window.clearTimeout(refreshTimer);
  refreshTimer = undefined;
}

function scheduleRefresh(): void {
  if (refreshTimer !== undefined) return;
  refreshTimer = window.setTimeout(() => {
    refreshTimer = undefined;
    void loadGame(true);
  }, 180);
}

function eventPayload<T>(event: Event): T | null {
  try {
    const envelope = JSON.parse((event as MessageEvent).data) as { data?: T };
    return envelope.data ?? null;
  } catch {
    return null;
  }
}

function handleMove(event: Event): void {
  const data = eventPayload<{ game_id?: Identifier; move?: MoveRecord; ply?: number }>(event);
  if (!data?.move || String(data.game_id) !== gameId.value || !gameData.value) return;
  if (data.ply !== undefined && data.move.ply !== data.ply) {
    scheduleRefresh();
    return;
  }
  const records = [...gameData.value.viewer_moves];
  const index = records.findIndex((move) => move.ply === data.move!.ply);
  const wasLatest = selectedPly.value >= records.length;
  if (index >= 0) records[index] = data.move;
  else {
    const latestPly = records.at(-1)?.ply;
    if (latestPly !== undefined && data.move.ply !== latestPly + 1) {
      scheduleRefresh();
      return;
    }
    records.push(data.move);
  }
  records.sort((left, right) => left.ply - right.ply);
  gameData.value.viewer_moves = records;
  const engineData = { ...gameData.value.engine_data };
  delete engineData.kibitzer;
  gameData.value.engine_data = engineData;
  if (wasLatest) selectedPly.value = records.length;
  if (gameData.value.clock_state) gameData.value.clock_state = { ...gameData.value.clock_state, running: false };
}

function handleEngineInfo(event: Event): void {
  const data = eventPayload<{ game_id?: Identifier; side?: "white" | "black" | "kibitzer"; engine_data?: EngineAnalysis }>(event);
  if (!data?.side || !data.engine_data || String(data.game_id) !== gameId.value || !gameData.value) return;
  if (data.side === "kibitzer" && !samePosition(data.engine_data.root_fen, latestPositionFen.value)) return;
  gameData.value.engine_data = {
    ...gameData.value.engine_data,
    [data.side]: { ...gameData.value.engine_data?.[data.side], ...data.engine_data },
  };
}

function handleClock(event: Event): void {
  const data = eventPayload<ClockState>(event);
  if (!data || String(data.game_id) !== gameId.value || !gameData.value) return;
  gameData.value.clock_state = data;
  if (data.clocks_ms) {
    const clocks = { ...gameData.value.clocks };
    if (data.clocks_ms.white !== null && data.clocks_ms.white !== undefined) clocks.white = clockLabel(data.clocks_ms.white);
    if (data.clocks_ms.black !== null && data.clocks_ms.black !== undefined) clocks.black = clockLabel(data.clocks_ms.black);
    gameData.value.clocks = clocks;
  }
}

function handleSpectators(event: Event): void {
  const data = eventPayload<{ tournament_id?: Identifier; spectator_count?: number }>(event);
  if (!data || data.spectator_count === undefined || String(data.tournament_id) !== String(fixture.value?.tournament_id ?? "") || !gameData.value) return;
  gameData.value.spectator_count = data.spectator_count;
}

function handleTournamentSnapshot(event: Event): void {
  const data = eventPayload<{ active_games?: GameRecord[] }>(event);
  if (!data?.active_games || !["live", "assigned"].includes(viewerGame.value?.status ?? "")) return;
  if (data.active_games.some((game) => String(game.id) === gameId.value)) return;
  const nextGame = data.active_games.find((game) => game.status === "live")
    ?? data.active_games.find((game) => game.status === "assigned");
  if (nextGame) activateGame(nextGame.id);
}

function queryValue(value: unknown): string {
  return Array.isArray(value) ? String(value[0] || "") : typeof value === "string" ? value : "";
}

function tabTarget(tab: TabKey): RouteLocationRaw {
  return { query: { ...route.query, tab } };
}

function setGamePage(page: number): void {
  if (page < 1 || page > gamePages.value || page === gamePage.value) return;
  void router.push({ query: { ...route.query, page: String(page), tab: "games" } });
}

async function cheer(team: RelayTeam | null, side?: "left" | "right"): Promise<void> {
  if (!team) return;
  const now = Date.now();
  if (now - lastCheerRequestAt < 350) return;
  lastCheerRequestAt = now;
  try {
    await api.post(`/api/events/${encodeURIComponent(props.detail.event.slug)}/engine-relay/cheers`, {
      body: { team_id: team.id, side },
      headers: { "X-Cope-Cheer-Client": cheerClientId },
    });
  } catch {
    return;
  }
}

function cheerFromFinale(team: RelayTeam | null, side: "left" | "right", event: MouseEvent): void {
  if (!team || !(event.currentTarget instanceof HTMLElement)) return;
  void cheer(team, side);
}

function handleCheerEvent(event: Event): void {
  const detail = (event as CustomEvent<{ id?: string; team_id?: number; side?: string | null }>).detail;
  const teamId = Number(detail?.team_id);
  const cheerId = typeof detail?.id === "string" ? detail.id : "";
  if (!Number.isFinite(teamId) || !cheerId || seenCheerIds.has(cheerId)) return;
  seenCheerIds.add(cheerId);
  if (seenCheerIds.size > 128) seenCheerIds.delete(seenCheerIds.values().next().value!);
  queuedCheers.push({ id: cheerId, teamId, side: detail.side === "left" || detail.side === "right" ? detail.side : null });
  if (cheerBatchTimer === undefined) cheerBatchTimer = window.setTimeout(flushCheerEvents, 75);
}

function flushCheerEvents(): void {
  cheerBatchTimer = undefined;
  const batches = new Map<string, QueuedCheer[]>();
  for (const cheerEvent of queuedCheers.splice(0)) {
    const fallbackSide = cheerEvent.id.charCodeAt(cheerEvent.id.length - 1) % 2 === 0 ? "left" : "right";
    const side = cheerEvent.side ?? fallbackSide;
    const key = `${cheerEvent.teamId}:${finaleComplete.value ? side : "team"}`;
    const batch = batches.get(key) ?? [];
    batch.push({ ...cheerEvent, side });
    batches.set(key, batch);
  }
  for (const batch of batches.values()) {
    const first = batch[0];
    if (!first) continue;
    if (finaleComplete.value) {
      const side = first.side ?? "left";
      const button = document.querySelector<HTMLElement>(`.finale-cheer--${side}`);
      const bounds = button?.getBoundingClientRect();
      launchCheer(first.teamId, first.id, {
        side,
        x: bounds ? ((bounds.left + bounds.width / 2) / window.innerWidth) * 100 : side === "left" ? 4 : 96,
        y: bounds ? ((bounds.top + bounds.height / 2) / window.innerHeight) * 100 : 94,
        large: true,
        count: batch.length,
      });
    } else {
      launchCheer(first.teamId, first.id, { count: batch.length });
    }
  }
}

function launchCheer(teamId: number, cheerId: string, origin?: { side?: "left" | "right"; x?: number; y?: number; large?: boolean; count: number }): void {
  const team = payload.value.teams.find((item) => item.id === teamId);
  if (!team || !confettiEnabled.value || cheerBursts.value.length >= 6) return;
  const side = origin?.side ?? (team.id === blackTeam.value?.id ? "right" : "left");
  const teamCard = document.querySelector<HTMLElement>(`[data-relay-team-id="${teamId}"]`);
  const cardBounds = teamCard?.getBoundingClientRect();
  const large = origin?.large ?? false;
  const count = origin?.count ?? 1;
  const burst: CheerBurst = {
    id: cheerId,
    color: team.primary_color,
    side,
    x: origin?.x ?? (cardBounds
      ? ((cardBounds.left + cardBounds.width * (.38 + Math.random() * .24)) / window.innerWidth) * 100
      : side === "left" ? 4 + Math.random() * 13 : 83 + Math.random() * 13),
    y: origin?.y ?? (cardBounds
      ? ((cardBounds.top + cardBounds.height * (.32 + Math.random() * .28)) / window.innerHeight) * 100
      : 18 + Math.random() * 65),
    pieces: Array.from({ length: large ? Math.min(60, 30 + count * 6) : Math.min(24, 10 + count * 4) }, (_, index) => ({
      id: index,
      startX: (large ? -2.2 : -1.4) + Math.random() * (large ? 4.4 : 2.8),
      startY: (large ? -1.1 : -.7) + Math.random() * (large ? 2.2 : 1.4),
      endX: large ? (side === "left" ? 1.5 + Math.random() * 14 : -15.5 + Math.random() * 14) : -5.8 + Math.random() * 11.6,
      endY: large ? -5 - Math.random() * 12 : -3.2 - Math.random() * 5.6,
      rotation: (large ? -520 : -260) + Math.random() * (large ? 1_040 : 520),
      delay: Math.random() * (large ? 220 : 140),
      duration: (large ? 950 : 720) + Math.random() * (large ? 520 : 330),
      size: (large ? .22 : .18) + Math.random() * (large ? .24 : .2),
    })),
  };
  cheerBursts.value.push(burst);
  const timer = window.setTimeout(() => {
    cheerTimers.delete(timer);
    cheerBursts.value = cheerBursts.value.filter((item) => item.id !== burst.id);
  }, large ? 1_850 : 1_250);
  cheerTimers.add(timer);
}

function teamForSide(side: "white" | "black"): RelayTeam | null {
  const game = selectedRelayGame.value;
  if (!game) return null;
  const teamId = side === "white" ? game.white_team_id : game.black_team_id;
  return payload.value.teams.find((team) => team.id === teamId) ?? null;
}

function currentMember(side: "white" | "black"): RelayRosterMember | null {
  const team = side === "white" ? whiteTeam.value : blackTeam.value;
  if (!team?.roster.length) return null;
  if (["finished", "abandoned"].includes(viewerGame.value?.status ?? "")) {
    const latestEngineId = gameData.value?.engine_data?.[side]?.engine_id;
    const latest = team.roster.find((member) => String(member.engine_id) === String(latestEngineId ?? ""));
    if (latest) return latest;
  }
  if (!isFinale.value) {
    const white = side === "white";
    const movesPlayed = moves.value.filter((move) => !move.is_book && ((move.ply % 2 === 1) === white)).length;
    return team.roster[movesPlayed % team.roster.length] ?? null;
  }
  const movesPlayed = moves.value.filter((move, index) => !move.is_book && moveSide(index) === side).length;
  const positions = buildPositions(opening.value.fen, moves.value.map((move) => move.uci));
  const sideToMove = parseFen(positionFen(positions.at(-1)!)).turn === "w" ? "white" : "black";
  const turnIndex = side === sideToMove ? movesPlayed : Math.max(0, movesPlayed - 1);
  return team.roster[turnIndex % team.roster.length] ?? null;
}

function memberForAnalysis(side: "white" | "black", analysis: EngineAnalysis | null): RelayRosterMember | null {
  const team = side === "white" ? whiteTeam.value : blackTeam.value;
  if (!team || analysis?.engine_id === null || analysis?.engine_id === undefined) return null;
  return team.roster.find((member) => String(member.engine_id) === String(analysis.engine_id)) ?? null;
}

function teamStyle(team: RelayTeam | null): Record<string, string> | undefined {
  if (!team) return undefined;
  return { "--relay-primary": team.primary_color, "--relay-secondary": team.secondary_color };
}

function appendChat(message: EventDetailResponse["chat_messages"][number]): void {
  if (message.id !== undefined && props.detail.chat_messages.some((item) => String(item.id) === String(message.id))) return;
  props.detail.chat_messages.push(message);
}

function nodeStyle(node: { color: string; secondary: string }): Record<string, string> {
  return { "--node-color": node.color, "--node-secondary": node.secondary };
}

function openTeamPanel(teamId: number): void {
  selectedTeamId.value = teamId;
}

function closeTeamPanel(): void {
  selectedTeamId.value = null;
}

function handleTeamPanelPointer(event: PointerEvent): void {
  if (props.view !== "event" || selectedTeamId.value === null) return;
  const target = event.target instanceof Element ? event.target : null;
  if (target?.closest(".clash-team-panel")) return;
  const teamNode = target?.closest<HTMLElement>("[data-orbit-team-id]");
  if (teamNode) {
    const teamId = Number(teamNode.dataset.orbitTeamId);
    if (Number.isFinite(teamId)) openTeamPanel(teamId);
    return;
  }
  closeTeamPanel();
}

function handleTeamPanelKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape" && selectedTeamId.value !== null) closeTeamPanel();
}

function ratingLabel(member: RelayRosterMember): string {
  return member.rating ? Math.round(member.rating.elo).toLocaleString() : "Unrated";
}

function countdownCompletionKey(): string {
  return `cope.event.${props.detail.event.id}.countdown-finished.${targetTime.value ?? "unscheduled"}`;
}

function authoritativeNow(): number {
  return Date.now() + props.clockOffsetMs;
}

function restoreCountdownCompletion(): void {
  try {
    countdownFinished.value = window.localStorage.getItem(countdownCompletionKey()) === "1";
  } catch {
    countdownFinished.value = false;
  }
  if (!countdownFinished.value && targetTime.value !== null && targetTime.value <= authoritativeNow()) finishCountdown();
}

function resetCountdownForTarget(): void {
  if (countdownFrame !== undefined) window.cancelAnimationFrame(countdownFrame);
  countdownFrame = undefined;
  releaseCountdownAudio();
  countdownFinished.value = false;
  countdownBeat.value = -1;
  soundRequested.value = false;
  soundPrimed.value = false;
  soundState.value = "loading";
  nowMs.value = authoritativeNow();
  if (props.view !== "event") return;
  restoreCountdownCompletion();
  if (countdownFinished.value || targetTime.value === null) return;
  prepareCountdownAudio();
  syncCountdown(true);
}

function prepareCountdownAudio(force = false): void {
  if (countdownFinished.value || typeof Audio === "undefined") return;
  if (countdownAudio && !force) return;
  if (countdownAudio) releaseCountdownAudio();
  const audio = new Audio();
  countdownAudio = audio;
  audio.preload = "auto";
  audio.src = countdownAudioUrl;
  soundState.value = "loading";
  audio.addEventListener("canplay", () => {
    if (countdownAudio !== audio || countdownFinished.value) return;
    if (!finalMinuteActive.value) soundState.value = "armed";
    syncCountdown(true);
  });
  audio.addEventListener("playing", () => {
    if (countdownAudio !== audio || countdownFinished.value) return;
    if (!finalMinuteActive.value) return;
    soundState.value = "playing";
  });
  audio.addEventListener("error", () => {
    if (countdownAudio === audio && !countdownFinished.value) soundState.value = "unavailable";
  });
  audio.load();
}

function releaseCountdownAudio(): void {
  if (!countdownAudio) return;
  countdownAudio.pause();
  countdownAudio.removeAttribute("src");
  countdownAudio.load();
  countdownAudio = null;
  audioPlayPending = false;
  countdownAudioStarted = false;
}

function expectedCountdownAudioTime(): number | null {
  if (!finalMinuteActive.value || targetTime.value === null) return null;
  return Math.max(0, Math.min(60, (countdownAudioLengthMs - (targetTime.value - authoritativeNow())) / 1000));
}

function seekCountdownAudio(audio: HTMLAudioElement, expected: number): void {
  const maximum = Number.isFinite(audio.duration) ? Math.max(0, audio.duration - .015) : 60;
  const position = Math.min(expected, maximum);
  try {
    audio.currentTime = position;
  } catch {
    soundState.value = "loading";
  }
}

async function playCountdownAudio(expected: number, force = false): Promise<void> {
  prepareCountdownAudio(soundState.value === "unavailable");
  const audio = countdownAudio;
  if (!audio || audioPlayPending || countdownAudioStarted || countdownFinished.value) return;
  if (!audio.paused) return;
  if (!force && soundState.value === "blocked") return;
  audio.loop = false;
  audio.volume = 1;
  seekCountdownAudio(audio, expected);
  audio.playbackRate = 1;
  audioPlayPending = true;
  try {
    await audio.play();
    if (countdownAudio !== audio || countdownFinished.value) return;
    countdownAudioStarted = true;
    soundPrimed.value = true;
    soundState.value = "playing";
  } catch (cause) {
    const name = (cause as { name?: string })?.name;
    soundState.value = name === "NotAllowedError" ? "blocked" : "unavailable";
  } finally {
    audioPlayPending = false;
  }
}

function syncCountdown(forceAudio = false): void {
  if (props.view !== "event") return;
  nowMs.value = authoritativeNow();
  if (countdownFinished.value || targetTime.value === null) return;
  if (targetTime.value <= nowMs.value) {
    finishCountdown();
    return;
  }
  if (!finalMinuteActive.value) {
    if (countdownFrame !== undefined) window.cancelAnimationFrame(countdownFrame);
    countdownFrame = undefined;
    countdownBeat.value = -1;
    if (soundState.value === "playing") soundState.value = "armed";
    return;
  }
  const expected = expectedCountdownAudioTime();
  if (expected === null) return;
  scheduleCountdownFrame();
  const beat = Math.floor(expected + .025);
  if (beat !== countdownBeat.value) countdownBeat.value = beat;
  if (countdownAudioStarted || (countdownAudio && !countdownAudio.paused)) {
    soundState.value = "playing";
    return;
  }
  void playCountdownAudio(expected, forceAudio);
}

function scheduleCountdownFrame(): void {
  if (countdownFrame !== undefined || countdownFinished.value || !finalMinuteActive.value) return;
  countdownFrame = window.requestAnimationFrame(() => {
    countdownFrame = undefined;
    syncCountdown();
  });
}

function finishCountdown(): void {
  if (countdownFinished.value) return;
  countdownFinished.value = true;
  if (countdownFrame !== undefined) window.cancelAnimationFrame(countdownFrame);
  countdownFrame = undefined;
  countdownBeat.value = -1;
  soundState.value = "finished";
  try {
    window.localStorage.setItem(countdownCompletionKey(), "1");
  } catch {
    countdownFinished.value = true;
  }
  releaseCountdownAudio();
}

async function enableCountdownSound(dismiss = false): Promise<void> {
  if (dismiss) soundRequested.value = true;
  if (countdownFinished.value || !countdownVisible.value || soundState.value === "playing") return;
  prepareCountdownAudio(soundState.value === "unavailable");
  const expected = expectedCountdownAudioTime();
  if (expected !== null) {
    await playCountdownAudio(expected, true);
    return;
  }
  const audio = countdownAudio;
  if (!audio || audioPlayPending || !audio.paused || soundPrimed.value) return;
  audio.loop = true;
  audio.volume = 0;
  audioPlayPending = true;
  try {
    await audio.play();
    audio.pause();
    audio.currentTime = 0;
    soundPrimed.value = true;
    soundState.value = "armed";
  } catch {
    soundState.value = "blocked";
  } finally {
    audio.loop = false;
    audio.volume = 1;
    audioPlayPending = false;
  }
}

function handleCountdownResume(): void {
  if (document.visibilityState === "hidden") return;
  syncCountdown();
}

function handleUserActivation(): void {
  if (countdownFinished.value || !countdownVisible.value || soundState.value === "playing") return;
  if (!soundPrimed.value || finalMinuteActive.value || ["blocked", "unavailable"].includes(soundState.value)) void enableCountdownSound();
}

</script>

<template>
  <div class="engine-clash-page" :class="{ 'engine-clash-page--final-minute': finalMinuteActive, 'engine-clash-page--arena': props.view === 'arena', 'engine-clash-page--finale': isFinale }">
    <div v-if="confettiEnabled" class="cheer-layer" aria-hidden="true">
      <span v-for="burst in cheerBursts" :key="burst.id" class="cheer-burst" :class="`cheer-burst--${burst.side}`" :style="{ left: `${burst.x}%`, top: `${burst.y}%`, '--cheer-color': burst.color }">
        <i v-for="piece in burst.pieces" :key="piece.id" :style="{ '--cheer-start-x': `${piece.startX}rem`, '--cheer-start-y': `${piece.startY}rem`, '--cheer-end-x': `${piece.endX}rem`, '--cheer-end-y': `${piece.endY}rem`, '--cheer-rotation': `${piece.rotation}deg`, '--cheer-delay': `${piece.delay}ms`, '--cheer-duration': `${piece.duration}ms`, '--cheer-size': `${piece.size}rem` }"></i>
      </span>
    </div>
    <span v-if="finalMinuteActive" :key="countdownBeat" class="countdown-pulse-layer" aria-hidden="true"></span>
    <section v-if="props.view === 'event' && finaleComplete" class="finale-podium" aria-labelledby="finale-winner-title">
      <div class="finale-podium__glow" aria-hidden="true"></div>
      <div class="finale-podium__symbols" aria-hidden="true"><span>♔</span><span>♕</span><span>♖</span><span>♘</span><span>🏆</span><span>🥇</span><span>♚</span><span>🏅</span></div>
      <div class="finale-podium__content" :style="teamStyle(winningTeam)">
        <span class="finale-podium__medal">🥇</span>
        <p>Engine Relay Finale champions</p>
        <h1 id="finale-winner-title">{{ winningTeam?.name }}</h1>
        <div class="finale-winning-bench">
          <article v-for="member in winningTeam?.roster ?? []" :key="member.id"><strong>{{ member.name }}</strong></article>
        </div>
        <div class="finale-podium__stand" aria-hidden="true"></div>
      </div>
      <button type="button" class="finale-cheer finale-cheer--left" :style="teamStyle(winningTeam)" :data-relay-team-id="winningTeam?.id" aria-label="Cheer for the champions from the left" @click="cheerFromFinale(winningTeam, 'left', $event)">🎉</button>
      <button type="button" class="finale-cheer finale-cheer--right" :style="teamStyle(winningTeam)" :data-relay-team-id="winningTeam?.id" aria-label="Cheer for the champions from the right" @click="cheerFromFinale(winningTeam, 'right', $event)">🎉</button>
    </section>
    <section v-else-if="props.view === 'event'" class="clash-hero" aria-labelledby="clash-title">
      <div class="clash-hero__wash" aria-hidden="true"></div>
      <div v-if="isFinale" class="finale-symbols" aria-hidden="true"><span>♕</span><span>♖</span><span>♘</span><span>♟</span><span>🏆</span><span>🥇</span><span>♚</span><span>🏅</span><span>♜</span><span>♙</span></div>
      <span class="chess-piece chess-piece--king" aria-hidden="true">♚</span>
      <span class="chess-piece chess-piece--knight" aria-hidden="true">♞</span>
      <span class="chess-piece chess-piece--pawn" aria-hidden="true">♟</span>
      <span class="chess-piece chess-piece--bishop" aria-hidden="true">♝</span>
      <span class="chess-piece chess-piece--queen" aria-hidden="true">♛</span>
      <span class="chess-piece chess-piece--rook" aria-hidden="true">♜</span>
      <span class="chess-piece chess-piece--knight-small" aria-hidden="true">♞</span>

      <div class="clash-hero__content">
        <header class="clash-heading">
          <span class="clash-kicker"><AppIcon name="trophy" :size="15" /> {{ isFinale ? 'Engine Relay Finale' : 'Engine Relay' }}</span>
          <h1 id="clash-title">{{ detail.event.title }}</h1>
        </header>

        <div v-if="countdownVisible" class="clash-countdown" aria-live="polite">
          <div v-for="part in countdown" :key="part.label">
            <strong>{{ String(part.value).padStart(2, "0") }}</strong>
            <span>{{ part.label }}</span>
          </div>
        </div>
        <p v-if="countdownVisible" class="clash-schedule"><AppIcon name="clock" :size="18" /> {{ scheduleLabel }}</p>
        <button v-if="countdownVisible && !soundRequested && !soundPrimed && soundState !== 'playing' && soundState !== 'finished'" class="countdown-sound" type="button" @pointerdown.stop @click="enableCountdownSound(true)"><AppIcon name="radio" :size="16" />{{ soundState === 'blocked' ? 'Enable countdown sound' : 'Arm countdown sound' }}</button>

        <div class="clash-orbit" :class="`clash-orbit--${Math.min(showcaseTeams.length, 4)}`">
          <div class="clash-orbit__rings" aria-hidden="true"><span></span><span></span><span></span></div>
          <article v-for="(team, index) in showcaseTeams" :key="team.id" class="clash-node" :class="[`clash-node--${index + 1}`, { 'clash-node--thinking': team.thinking }]" :style="nodeStyle(team)" :data-relay-team-id="team.id" :data-orbit-team-id="team.id" role="button" tabindex="0" :aria-label="`View ${team.name} engines`" @click.stop="openTeamPanel(team.id)" @keydown.enter.prevent="openTeamPanel(team.id)" @keydown.space.prevent="openTeamPanel(team.id)">
            <div class="clash-node__beacon"><span><AppIcon name="trophy" :size="31" /></span></div>
            <div class="clash-node__card">
              <strong>{{ team.name }}</strong>
              <span>{{ isFinale ? `${team.engineCount}-engine finalist bench` : `${team.engineCount}-engine relay team` }}</span>
              <small><i></i>{{ team.thinking ? "Playing now" : team.current ? "On relay" : isLive ? "Ready" : "Relay team" }}</small>
            </div>
          </article>
          <p v-if="!showcaseTeams.length" class="clash-orbit__empty">No teams configured.</p>
        </div>

        <div v-if="isLive" class="clash-actions">
          <RouterLink :to="{ name: 'event-arena', params: { slug: detail.event.slug } }" class="clash-primary-action"><AppIcon name="trophy" :size="19" /> Enter arena <AppIcon name="arrow-right" :size="18" /></RouterLink>
        </div>
      </div>

      <div v-if="payload.teams.length" class="clash-cheer-rail" aria-label="Cheer for a relay team">
        <button v-for="team in payload.teams" :key="team.id" type="button" class="clash-cheer-action" :style="teamStyle(team)" :data-relay-team-id="team.id" @click="cheer(team)"><span><AppIcon name="trophy" :size="18" /></span><span><small>Cheer for</small><strong>{{ team.short_name || team.name }}</strong></span></button>
      </div>

      <aside class="clash-status-card clash-status-card--systems">
        <span class="clash-status-card__icon"><AppIcon name="activity" :size="25" /></span>
        <div><small>Live status</small><strong>{{ systemStatus }}</strong></div>
        <i :class="{ active: payload.fixtures.length > 0 && !['cancelled', 'postponed'].includes(detail.event.status) }"></i>
      </aside>
      <aside class="clash-status-card clash-status-card--audience">
        <span class="clash-status-card__icon"><AppIcon name="user" :size="25" /></span>
        <div><small>Current spectators</small><strong>{{ spectatorCount.toLocaleString() }}</strong><span>{{ spectatorCount === 1 ? "spectator" : "spectators" }}</span></div>
      </aside>
    </section>

    <Teleport to="body">
      <Transition name="clash-team-panel">
        <aside v-if="props.view === 'event' && selectedTeam" class="clash-team-panel" :style="teamStyle(selectedTeam)" role="dialog" :aria-label="`${selectedTeam.name} team engines`">
          <header>
            <div><span>Relay team</span><h2>{{ selectedTeam.name }}</h2></div>
            <button type="button" :aria-label="`Close ${selectedTeam.name} team panel`" @click="closeTeamPanel"><AppIcon name="close" :size="21" /></button>
          </header>
          <p v-if="selectedTeam.motto" class="clash-team-panel__motto">{{ selectedTeam.motto }}</p>
          <div class="clash-team-panel__roster">
            <article v-for="(member, index) in selectedTeam.roster" :key="member.id">
              <span>{{ index + 1 }}</span>
              <div><strong>{{ member.name }} {{ member.version }}</strong></div>
              <div class="clash-team-panel__rating"><strong>{{ ratingLabel(member) }}</strong><small>{{ member.rating?.list_name || 'No published rating' }}</small></div>
            </article>
          </div>
          <p v-if="!selectedTeam.roster.length" class="clash-team-panel__empty">This team has no engines yet.</p>
        </aside>
      </Transition>
    </Teleport>

    <section v-if="props.view === 'arena'" class="relay-viewer">
      <header ref="headingElement" class="tournament-heading">
        <div class="tournament-heading__title">
          <div class="title-line"><h1>{{ detail.event.title }}</h1><span class="relay-status">{{ gameLabel }}</span><span class="relay-spectators"><AppIcon name="user" :size="14" /> {{ spectatorCount.toLocaleString() }}</span></div>
          <p>{{ fixture?.title ?? "Engine relay" }}<template v-if="viewerGame"> / Game {{ viewerGame.game_number ?? viewerGame.id }}</template></p>
        </div>
        <div class="tournament-heading__controls">
          <RouterLink :to="{ name: 'event', params: { slug: detail.event.slug } }" class="event-page-link"><AppIcon name="arrow-left" :size="15" /> Event page</RouterLink>
          <div class="relay-broadcast__selectors">
        <label v-if="payload.fixtures.length > 1"><span>Fixture</span><select :value="fixture?.id ?? ''" @change="selectFixture(($event.target as HTMLSelectElement).value)"><option v-for="item in payload.fixtures" :key="item.id" :value="item.id">{{ item.title }}</option></select></label>
        <StreamIndicator v-if="gameData && !['finished', 'aborted'].includes(gameData.tournament.status)" :state="streamState" />
      </div>
      </div>
    </header>

    <ContentState v-if="!payload.fixtures.length" kind="empty" title="The first relay fixture is being assembled" message="Teams and engines will appear here as soon as the control room registers them." />
    <ContentState v-else-if="!fixture?.games.length" kind="empty" title="This relay is not on the board yet" message="The fixture exists as an unrated tournament draft. Its games appear here when it is scheduled or started." />
    <ContentState v-else-if="loading && !gameData" kind="loading" title="Synchronising the relay" />
    <ContentState v-else-if="loadError && !gameData" kind="error" :message="loadError" action-label="Try again" @action="loadGame" />

    <section v-else-if="gameData && viewerGame" id="relay-arena" ref="arenaElement" class="arena" :aria-label="`${whiteTeam?.name ?? 'White'} versus ${blackTeam?.name ?? 'Black'}`">
      <div class="engine-column">
        <div class="relay-engine-slot" :style="teamStyle(blackTeam)" :data-relay-team-id="blackTeam?.id">
          <div class="relay-team-strip"><strong>{{ blackTeam?.name ?? "Black team" }}</strong><TransitionGroup name="relay-chip" tag="div" class="relay-team-strip__track"><span v-for="member in blackRoster" :key="member.id" :class="{ current: blackMember?.id === member.id }">{{ member.label || member.name }}</span></TransitionGroup></div>
          <EnginePanel side="black" :name="blackCardMember?.display_name || blackTeam?.name || ''" :engine-id="blackCardMember?.engine_id ?? null" :clock="gameData.clocks?.black ?? '—'" :analysis="displayedBlackAnalysis" :position-fen="currentPositionFen" :active="activeSide === 'black'" />
        </div>
        <div class="relay-engine-slot" :style="teamStyle(whiteTeam)" :data-relay-team-id="whiteTeam?.id">
          <div class="relay-team-strip"><strong>{{ whiteTeam?.name ?? "White team" }}</strong><TransitionGroup name="relay-chip" tag="div" class="relay-team-strip__track"><span v-for="member in whiteRoster" :key="member.id" :class="{ current: whiteMember?.id === member.id }">{{ member.label || member.name }}</span></TransitionGroup></div>
          <EnginePanel side="white" :name="whiteCardMember?.display_name || whiteTeam?.name || ''" :engine-id="whiteCardMember?.engine_id ?? null" :clock="gameData.clocks?.white ?? '—'" :analysis="displayedWhiteAnalysis" :position-fen="currentPositionFen" :active="activeSide === 'white'" />
        </div>
        <div class="arena-cheers" aria-label="Cheer for a team">
          <button v-if="blackTeam" type="button" class="cheer-button cheer-button--arena" :style="teamStyle(blackTeam)" @click="cheer(blackTeam)"><AppIcon name="trophy" :size="14" /> Cheer {{ blackTeam.short_name || blackTeam.name }}</button>
          <button v-if="whiteTeam" type="button" class="cheer-button cheer-button--arena" :style="teamStyle(whiteTeam)" @click="cheer(whiteTeam)"><AppIcon name="trophy" :size="14" /> Cheer {{ whiteTeam.short_name || whiteTeam.name }}</button>
        </div>
      </div>
      <div ref="boardColumnElement" class="board-column">
        <div v-if="fixture?.kibitzer" class="kibitzer-bar" :aria-label="`${fixture.kibitzer.name} evaluation ${kibitzerLabel}`"><span class="kibitzer-bar__black" :style="{ height: `${100 - kibitzerPercent}%` }"></span><strong>{{ kibitzerLabel }}</strong><small>{{ fixture.kibitzer.name }}</small></div>
        <ChessViewer :opening="opening" :moves="moves" :model-value="selectedPly" :arrows="suggestedMoves" :label="`${whiteTeam?.name ?? 'White'} versus ${blackTeam?.name ?? 'Black'} relay game`" @update:model-value="selectedPly = $event" @position="currentPositionFen = $event.fen" />
      </div>
      <aside class="activity-column" aria-label="Game activity">
        <MoveList class="arena-moves" :moves="moves.map((move) => move.san || move.uci)" :uci-moves="moves.map((move) => move.uci)" :annotations="moveAnnotations" :fen="opening.fen" :book-plies="bookPlies" :model-value="selectedPly" @update:model-value="selectedPly = $event" />
        <ChatPanel class="arena-chat" :messages="detail.chat_messages" :settings="detail.chat_settings" :event-slug="detail.event.slug" @sent="appendChat" />
      </aside>
    </section>

    <section v-if="gameData" class="tournament-data">
      <nav class="data-tabs" aria-label="Tournament information">
        <RouterLink :to="tabTarget('standings')" :aria-current="activeTab === 'standings' ? 'page' : undefined">Standings <span>{{ teamStandings.length }}</span></RouterLink>
        <RouterLink :to="tabTarget('games')" :aria-current="activeTab === 'games' ? 'page' : undefined">Games <span>{{ gameTotal }}</span></RouterLink>
        <RouterLink :to="tabTarget('settings')" :aria-current="activeTab === 'settings' ? 'page' : undefined">Settings</RouterLink>
      </nav>

      <section v-if="activeTab === 'standings'" class="data-panel" aria-labelledby="standings-title">
        <header><div><h2 id="standings-title">Standings</h2></div></header>
        <div v-if="gameData.standings?.length" class="table-wrap">
          <table>
            <thead><tr><th>Rank</th><th>Team</th><th>Points</th><th>Score %</th><th>Played</th><th v-if="format === 'swiss'">Buchholz</th><th v-if="format === 'knockout'">Stage</th></tr></thead>
            <tbody>
              <tr v-for="(standing, index) in teamStandings" :key="standing.team_id">
                <td class="rank-cell">{{ index + 1 }}</td>
                <td class="team-cell">{{ standing.name }}</td>
                <td class="number-cell">{{ standing.points }}</td>
                <td class="number-cell">{{ scorePercentLabel(standing.score_percent) }}</td>
                <td class="number-cell">{{ standing.played }}</td>
                <td v-if="format === 'swiss'" class="number-cell">{{ standing.buchholz ?? 0 }}</td>
                <td v-if="format === 'knockout'" class="number-cell">{{ standing.stage ?? 0 }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <ContentState v-else kind="empty" compact title="No standings yet" />
      </section>

      <section v-else-if="activeTab === 'games'" class="data-panel" aria-labelledby="games-title">
        <header><div><h2 id="games-title">Finished games</h2></div></header>
        <GameTable v-if="gameData.games.length" :games="gameData.games" :engines="teamNames" :game-routes="relayGameRoutes" :participant-links="false" caption="Finished team games" />
        <ContentState v-else kind="empty" compact title="No finished games yet" />
        <nav v-if="gamePages > 1" class="pagination" aria-label="Game pages">
          <button type="button" :disabled="gamePage <= 1" @click="setGamePage(gamePage - 1)">Previous</button>
          <span>Page {{ gamePage.toLocaleString() }} of {{ gamePages.toLocaleString() }}</span>
          <button type="button" :disabled="gamePage >= gamePages" @click="setGamePage(gamePage + 1)">Next</button>
        </nav>
      </section>

      <section v-else class="data-panel settings-panel" aria-labelledby="settings-title">
        <header><div><h2 id="settings-title">Settings</h2></div></header>
        <dl v-if="settingsRows.length" class="settings-list">
          <div v-for="row in settingsRows" :key="row.label"><dt>{{ row.label }}</dt><dd>{{ row.value }}</dd></div>
        </dl>
        <ContentState v-else kind="empty" compact title="No settings recorded" />

        <div v-if="teamHardwareRows.length" class="hardware-section">
          <h3>Team hardware</h3>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Team</th><th>Hash</th><th>Threads</th><th>Active hardware</th></tr></thead>
              <tbody>
                <tr v-for="row in teamHardwareRows" :key="row.engine_id">
                  <td class="team-cell">{{ row.name }}</td>
                  <td>{{ row.hash || '-' }}</td>
                  <td>{{ row.threads || '-' }}</td>
                  <td>{{ row.hardware || 'Not reported' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </section>
    </section>

  </div>
</template>

<style scoped>
.engine-clash-page {
  --clash-blue: #2866cf;
  --clash-ink: #08152b;
  margin-block-end: -3rem;
  background: #f5f8fe;
  color: var(--clash-ink);
}
.engine-clash-page--arena { min-height: calc(100dvh - 2.85rem); margin-block-end: 0; background: var(--color-bg, #f3f6fa); }

.cheer-layer { position: fixed; z-index: 46; inset: 0; overflow: hidden; pointer-events: none; }
.cheer-burst { position: absolute; width: 1px; height: 1px; }
.cheer-burst i { position: absolute; width: var(--cheer-size); height: calc(var(--cheer-size) * 1.6); border-radius: .08rem; background: var(--cheer-color); opacity: 0; transform: translate(var(--cheer-start-x), var(--cheer-start-y)); animation: cheer-pop var(--cheer-duration) cubic-bezier(.16, .72, .28, 1) forwards; animation-delay: var(--cheer-delay); }
.cheer-burst i:nth-child(even) { height: var(--cheer-size); border-radius: 50%; filter: brightness(1.22); }
.cheer-button { display: inline-flex; min-height: 2.2rem; align-items: center; justify-content: center; gap: .3rem; padding: 0 .85rem; border: 1px solid color-mix(in srgb, var(--relay-primary) 55%, white); border-radius: 999px; background: color-mix(in srgb, var(--relay-primary) 13%, white); color: color-mix(in srgb, var(--relay-primary) 82%, #071426); cursor: pointer; font-size: .65rem; font-weight: 800; box-shadow: 0 .2rem .55rem color-mix(in srgb, var(--relay-primary) 14%, transparent); }
.cheer-button:hover { border-color: color-mix(in srgb, var(--relay-primary) 78%, white); background: color-mix(in srgb, var(--relay-primary) 22%, white); box-shadow: 0 .3rem .75rem color-mix(in srgb, var(--relay-primary) 22%, transparent); }
.cheer-button:active { transform: scale(.96); }
.cheer-button--arena { min-width: 0; min-height: 2.65rem; padding-inline: 1rem; font-size: .67rem; }
@keyframes cheer-pop { 0% { opacity: 0; transform: translate(var(--cheer-start-x), var(--cheer-start-y)) rotate(0) scale(.5); } 13% { opacity: .95; } 100% { opacity: 0; transform: translate(var(--cheer-end-x), var(--cheer-end-y)) rotate(var(--cheer-rotation)) scale(1); } }

@media (prefers-reduced-motion: reduce) { .cheer-burst i { animation-duration: .18s; } }

.countdown-pulse-layer {
  position: fixed;
  z-index: 48;
  inset: 0;
  display: block;
  pointer-events: none;
  background: radial-gradient(circle at 50% 45%, rgb(104 173 255 / 19%), rgb(41 102 203 / 8%) 52%, transparent 78%);
  box-shadow: inset 0 0 0 .45rem rgb(71 139 237 / 13%), inset 0 0 9rem rgb(80 151 247 / 18%);
  animation: countdown-page-pulse .58s cubic-bezier(.12, .72, .28, 1) both;
}

.engine-clash-page--final-minute .clash-hero {
  border-color: #a8c8f5;
  background:
    radial-gradient(circle at 50% 44%, rgb(255 255 255 / 98%) 0, rgb(238 247 255 / 86%) 24%, transparent 51%),
    radial-gradient(circle at 20% 76%, rgb(88 160 255 / 26%), transparent 34%),
    radial-gradient(circle at 82% 74%, rgb(105 173 255 / 24%), transparent 35%),
    linear-gradient(155deg, #f5f9ff 0%, #e5f0ff 50%, #d5e6fd 100%);
}

.clash-hero {
  position: relative;
  isolation: isolate;
  display: flex;
  min-height: calc(100vh - var(--header-height, 4rem));
  min-height: calc(100dvh - var(--header-height, 4rem));
  overflow: hidden;
  border-bottom: 1px solid #cfdbed;
  background:
    radial-gradient(circle at 50% 44%, rgb(255 255 255 / 96%) 0, rgb(244 249 255 / 79%) 23%, transparent 50%),
    radial-gradient(circle at 21% 75%, rgb(113 171 255 / 18%), transparent 33%),
    radial-gradient(circle at 82% 75%, rgb(133 180 255 / 16%), transparent 34%),
    linear-gradient(155deg, #f6f9ff 0%, #eaf2ff 50%, #dce8fa 100%);
}

.clash-hero::before,
.clash-hero::after {
  position: absolute;
  z-index: -1;
  width: 32rem;
  height: 32rem;
  border-radius: 50%;
  background: rgb(255 255 255 / 58%);
  filter: blur(4rem);
  content: "";
}

.clash-hero::before { top: -12rem; left: -8rem; }
.clash-hero::after { right: -9rem; bottom: -15rem; }

.clash-hero__wash {
  position: absolute;
  z-index: -1;
  inset: 0;
  opacity: .5;
  background-image: radial-gradient(circle, rgb(54 110 194 / 24%) 0 1px, transparent 1.4px);
  background-size: 4.5rem 4.5rem;
  mask-image: linear-gradient(to bottom, black, transparent 72%);
}

.chess-piece {
  position: absolute;
  z-index: -1;
  color: #aac2e6;
  opacity: .19;
  font-family: Georgia, serif;
  line-height: 1;
  filter: blur(.6px) drop-shadow(0 1.5rem 1.8rem rgb(72 107 157 / 16%));
  user-select: none;
}

.chess-piece--king { top: 2rem; left: 4%; font-size: clamp(13rem, 20vw, 22rem); transform: rotate(-8deg); }
.chess-piece--knight { top: 1.5rem; right: 5%; font-size: clamp(12rem, 18vw, 20rem); transform: rotate(6deg) scaleX(-1); }
.chess-piece--pawn { bottom: -1rem; left: -2rem; font-size: clamp(10rem, 15vw, 16rem); filter: blur(5px); }
.chess-piece--bishop { right: -1rem; bottom: -1rem; font-size: clamp(10rem, 15vw, 16rem); filter: blur(4px); }
.chess-piece--queen { top: 29%; left: 21%; font-size: clamp(5rem, 8vw, 9rem); opacity: .11; transform: rotate(12deg); }
.chess-piece--rook { top: 31%; right: 21%; font-size: clamp(5rem, 7.5vw, 8.5rem); opacity: .1; transform: rotate(-10deg); }
.chess-piece--knight-small { right: 43%; bottom: 2%; font-size: clamp(5rem, 8vw, 9rem); opacity: .09; transform: rotate(8deg) scaleX(-1); }

.clash-hero__content {
  z-index: 2;
  display: grid;
  width: min(100%, 100rem);
  margin: 0 auto;
  justify-items: center;
  align-content: center;
  padding: clamp(2rem, 4vh, 3.5rem) clamp(1rem, 4vw, 4rem) 7rem;
}

.clash-heading { display: grid; justify-items: center; text-align: center; }
.clash-kicker {
  display: inline-flex;
  align-items: center;
  gap: .45rem;
  padding: .42rem .9rem;
  border: 1px solid rgb(52 105 196 / 8%);
  border-radius: 999px;
  background: rgb(223 234 251 / 77%);
  color: #1f5fbf;
  font-size: .71rem;
  font-weight: 820;
  letter-spacing: .075em;
  text-transform: uppercase;
}

.clash-heading h1 {
  margin: .65rem 0 0;
  color: var(--clash-ink);
  font-size: clamp(3rem, 6.15vw, 5.75rem);
  font-weight: 760;
  letter-spacing: -.065em;
  line-height: .98;
}

.clash-countdown {
  display: flex;
  justify-content: center;
  width: min(100%, 50rem);
  margin-top: 1.1rem;
}

.clash-countdown > div {
  position: relative;
  display: grid;
  width: 25%;
  justify-items: center;
  gap: .35rem;
  padding: clamp(.8rem, 2.6vh, 1.45rem) .75rem 1.2rem;
}

.clash-countdown strong {
  color: #071938;
  font-size: clamp(3rem, 5.7vw, 5.2rem);
  font-weight: 760;
  font-variant-numeric: tabular-nums;
  letter-spacing: -.065em;
  line-height: 1;
}

.clash-countdown span {
  color: #2361bf;
  font-size: .69rem;
  font-weight: 830;
  letter-spacing: .17em;
  text-transform: uppercase;
}

.clash-schedule { display: flex; align-items: center; gap: .45rem; margin: .75rem 0 0; color: #70809a; font-size: .78rem; }
.countdown-sound { display: inline-flex; align-items: center; gap: .4rem; min-height: 2.15rem; margin-top: .65rem; padding: 0 .8rem; border: 1px solid rgb(57 111 201 / 28%); border-radius: 999px; background: rgb(255 255 255 / 68%); color: #2f63ad; cursor: pointer; font: inherit; font-size: .66rem; font-weight: 760; }
.countdown-sound:hover { border-color: #3974c8; background: white; }

.clash-orbit {
  position: relative;
  width: 100%;
  height: clamp(14rem, 27vh, 18rem);
  max-height: 19rem;
  margin-top: -1.2rem;
}

.clash-orbit__rings { position: absolute; inset: 7% 6% 0; }
.clash-orbit__rings span { position: absolute; border-radius: 50%; }
.clash-orbit__rings span:nth-child(1) { inset: 6% 2% 16%; border: 3px solid rgb(255 255 255 / 94%); box-shadow: 0 0 1.3rem rgb(255 255 255 / 90%); }
.clash-orbit__rings span:nth-child(2) { inset: 0 8% 3%; border: 1px dashed rgb(255 255 255 / 74%); }
.clash-orbit__rings span:nth-child(3) { inset: 17% 15% 1%; border: 1px dashed rgb(99 145 208 / 24%); }

.clash-node {
  position: absolute;
  z-index: 2;
  isolation: isolate;
  display: grid;
  width: 10.5rem;
  justify-items: center;
  color: var(--clash-ink);
  cursor: pointer;
}
.clash-node:focus-visible { outline: 3px solid color-mix(in srgb, var(--node-color) 70%, white); outline-offset: .35rem; border-radius: 1rem; }

.clash-node::before {
  position: absolute;
  z-index: -1;
  top: -7rem;
  left: 50%;
  width: clamp(24rem, 34vw, 38rem);
  height: 22rem;
  background: radial-gradient(ellipse, color-mix(in srgb, var(--node-color) 36%, transparent), color-mix(in srgb, var(--node-color) 16%, transparent) 35%, transparent 72%);
  content: "";
  filter: blur(1.35rem);
  pointer-events: none;
  transform: translateX(-50%);
}

.clash-node--1 { top: 14%; left: 4%; }
.clash-node--2 { bottom: 0; left: 30%; }
.clash-node--3 { right: 30%; bottom: 0; }
.clash-node--4 { top: 14%; right: 4%; }
.clash-orbit--2 .clash-node--1 { top: 14%; left: 14%; }
.clash-orbit--2 .clash-node--2 { top: 14%; right: 14%; bottom: auto; left: auto; }

.clash-node__beacon {
  position: relative;
  display: grid;
  width: 7.2rem;
  height: 3.5rem;
  place-items: start center;
  border-radius: 50%;
  background: radial-gradient(ellipse, color-mix(in srgb, var(--node-color) 38%, transparent), transparent 65%);
  box-shadow: 0 .7rem 1.9rem color-mix(in srgb, var(--node-color) 30%, transparent);
}

.clash-node__beacon::before,
.clash-node__beacon::after {
  position: absolute;
  right: .15rem;
  bottom: .05rem;
  left: .15rem;
  height: 1.5rem;
  border: 2px solid color-mix(in srgb, var(--node-color) 48%, white);
  border-radius: 50%;
  content: "";
}

.clash-node__beacon::after { right: 1.05rem; bottom: .35rem; left: 1.05rem; height: 1rem; opacity: .7; }
.clash-node__beacon > span {
  z-index: 2;
  display: grid;
  width: 4.7rem;
  height: 4.7rem;
  place-items: center;
  border: 3px solid white;
  border-radius: 50%;
  background: linear-gradient(145deg, color-mix(in srgb, var(--node-color) 78%, white), var(--node-color));
  box-shadow: 0 0 0 .2rem color-mix(in srgb, var(--node-color) 16%, transparent), 0 0 5.5rem 1.5rem color-mix(in srgb, var(--node-color) 52%, transparent);
  color: white;
}

.clash-node__card {
  display: grid;
  min-width: 9.6rem;
  justify-items: center;
  margin-top: .6rem;
  padding: 2.1rem .65rem .65rem;
  border: 1px solid rgb(255 255 255 / 72%);
  border-radius: .75rem;
  background: linear-gradient(rgb(255 255 255 / 28%), rgb(255 255 255 / 78%));
  box-shadow: 0 .8rem 2.1rem rgb(66 92 132 / 8%);
  backdrop-filter: blur(11px);
}

.clash-node__beacon + .clash-node__card { margin-top: -1.15rem; }
.clash-node__card strong { max-width: 9rem; overflow: hidden; font-size: .9rem; text-overflow: ellipsis; white-space: nowrap; }
.clash-node__card > span { margin-top: .22rem; color: var(--node-color); font-size: .63rem; font-weight: 650; }
.clash-node__card small { display: inline-flex; align-items: center; gap: .32rem; margin-top: .52rem; padding: .25rem .5rem; border-radius: 999px; background: color-mix(in srgb, var(--node-color) 9%, white); color: color-mix(in srgb, var(--node-color) 77%, #142136); font-size: .59rem; font-weight: 720; }
.clash-node__card small i { width: .35rem; height: .35rem; border-radius: 50%; background: var(--node-color); }
.clash-node--thinking .clash-node__beacon > span { animation: node-thinking 1.3s ease-in-out infinite; }
.clash-orbit__empty { position: absolute; inset: 45% 0 auto; margin: 0; color: #6c7e99; text-align: center; }

.clash-actions { z-index: 4; display: grid; justify-items: center; padding: 1rem 1.5rem; margin-top: -2rem; }
.clash-actions button, .clash-actions > a { display: inline-flex; align-items: center; justify-content: center; border: 0; cursor: pointer; font: inherit; font-weight: 730; text-decoration: none; }
.clash-primary-action { min-height: 3.65rem; gap: .75rem; padding: 0 1.8rem; border-radius: .65rem; background: linear-gradient(135deg, #215bb9, #3a7add); box-shadow: 0 .8rem 1.8rem rgb(29 86 180 / 28%); color: white; font-size: 1rem; transition: transform .18s ease, box-shadow .18s ease; }
.clash-primary-action:hover { transform: translateY(-2px); box-shadow: 0 1rem 2rem rgb(29 86 180 / 32%); }

.clash-cheer-rail {
  position: absolute;
  z-index: 7;
  bottom: 1.35rem;
  left: 50%;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: .65rem;
  width: min(calc(100% - 2rem), 52rem);
  padding: .45rem;
  border: 1px solid rgb(255 255 255 / 72%);
  border-radius: .85rem;
  background: rgb(255 255 255 / 48%);
  box-shadow: 0 .8rem 2.2rem rgb(42 75 124 / 12%);
  backdrop-filter: blur(16px);
  transform: translateX(-50%);
}
.clash-cheer-action {
  display: grid;
  grid-template-columns: auto minmax(5rem, 1fr);
  align-items: center;
  gap: .6rem;
  min-width: 9.5rem;
  min-height: 3.15rem;
  padding: .4rem .8rem .4rem .45rem;
  border: 1px solid color-mix(in srgb, var(--relay-primary) 34%, white);
  border-radius: .6rem;
  background: linear-gradient(135deg, color-mix(in srgb, var(--relay-primary) 16%, white), rgb(255 255 255 / 86%));
  box-shadow: inset 0 1px rgb(255 255 255 / 75%);
  color: color-mix(in srgb, var(--relay-primary) 78%, #08152b);
  cursor: pointer;
  text-align: left;
  transition: border-color .18s ease, box-shadow .18s ease, transform .18s ease;
}
.clash-cheer-action > span:first-child { display: grid; width: 2.25rem; height: 2.25rem; place-items: center; border-radius: .5rem; background: var(--relay-primary); color: white; box-shadow: 0 .35rem .8rem color-mix(in srgb, var(--relay-primary) 28%, transparent); }
.clash-cheer-action > span:last-child { display: grid; min-width: 0; }
.clash-cheer-action small { color: var(--color-text-muted, #607080); font-size: .55rem; font-weight: 750; letter-spacing: .07em; text-transform: uppercase; }
.clash-cheer-action strong { overflow: hidden; margin-top: .08rem; font-size: .76rem; text-overflow: ellipsis; white-space: nowrap; }
.clash-cheer-action:hover { border-color: var(--relay-primary); box-shadow: 0 .55rem 1.2rem color-mix(in srgb, var(--relay-primary) 18%, transparent); transform: translateY(-2px); }
.clash-cheer-action:active { transform: translateY(0) scale(.98); }

.clash-team-panel {
  position: fixed;
  z-index: 70;
  top: var(--header-height, 4rem);
  bottom: 0;
  left: 0;
  display: flex;
  width: min(25rem, calc(100vw - 2rem));
  flex-direction: column;
  overflow-y: auto;
  padding: 1.25rem;
  border-inline-end: 1px solid color-mix(in srgb, var(--relay-primary) 32%, #d5dbe1);
  background: color-mix(in srgb, var(--relay-primary) 4%, #fff);
  box-shadow: 1.2rem 0 3rem rgb(11 29 57 / 22%);
  color: #101827;
}
.clash-team-panel > header { display: flex; align-items: start; justify-content: space-between; gap: 1rem; padding-bottom: 1rem; border-bottom: 1px solid color-mix(in srgb, var(--relay-primary) 18%, #d5dbe1); }
.clash-team-panel > header div { display: grid; gap: .2rem; }
.clash-team-panel > header span { color: var(--relay-primary); font-size: .62rem; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }
.clash-team-panel > header h2 { margin: 0; font-size: 1.55rem; letter-spacing: -.035em; }
.clash-team-panel > header button { display: grid; width: 2.5rem; height: 2.5rem; flex: 0 0 auto; place-items: center; border: 1px solid color-mix(in srgb, var(--relay-primary) 22%, #d5dbe1); border-radius: 50%; background: #fff; color: #34445a; cursor: pointer; }
.clash-team-panel > header button:hover { border-color: var(--relay-primary); color: var(--relay-primary); }
.clash-team-panel__motto { margin: 1rem 0 0; color: #66758a; font-size: .78rem; line-height: 1.55; }
.clash-team-panel__roster { display: grid; gap: .65rem; margin-top: 1rem; }
.clash-team-panel__roster > article { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: .35rem .7rem; padding: .85rem; border: 1px solid color-mix(in srgb, var(--relay-primary) 18%, #d5dbe1); border-radius: .7rem; background: #fff; box-shadow: 0 .3rem .8rem rgb(27 48 78 / 6%); }
.clash-team-panel__roster > article > span { display: grid; grid-row: 1 / 3; width: 1.8rem; height: 1.8rem; place-items: center; border-radius: 50%; background: color-mix(in srgb, var(--relay-primary) 12%, white); color: var(--relay-primary); font-size: .65rem; font-weight: 800; }
.clash-team-panel__roster > article > div { display: grid; min-width: 0; }
.clash-team-panel__roster > article strong { font-size: .84rem; }
.clash-team-panel__roster > article small { margin-top: .12rem; overflow: hidden; color: #718096; font-size: .64rem; text-overflow: ellipsis; white-space: nowrap; }
.clash-team-panel__rating { grid-column: 2; display: flex !important; flex-direction: row; align-items: baseline; gap: .45rem; margin-top: .35rem; padding-top: .55rem; border-top: 1px solid #e7ebf0; }
.clash-team-panel__rating strong { color: var(--relay-primary); font-size: 1.12rem !important; font-variant-numeric: tabular-nums; }
.clash-team-panel__empty { margin: auto 0; color: #718096; text-align: center; }
.clash-team-panel-enter-active, .clash-team-panel-leave-active { transition: transform .24s cubic-bezier(.22, .75, .28, 1), opacity .2s ease; }
.clash-team-panel-enter-from, .clash-team-panel-leave-to { opacity: 0; transform: translateX(-100%); }

.clash-status-card {
  position: absolute;
  z-index: 5;
  bottom: 2.1rem;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: .7rem;
  min-width: 15rem;
  padding: .75rem .85rem;
  border: 1px solid rgb(255 255 255 / 75%);
  border-radius: .75rem;
  background: rgb(255 255 255 / 57%);
  box-shadow: 0 .8rem 2rem rgb(45 79 132 / 7%);
  backdrop-filter: blur(15px);
}

.clash-status-card--systems { left: 3.2%; }
.clash-status-card--audience { right: 3.2%; }
.clash-status-card__icon { display: grid; width: 2.7rem; height: 2.7rem; place-items: center; border-radius: .65rem; background: rgb(255 255 255 / 54%); color: #3a6eb8; }
.clash-status-card > div { display: grid; }
.clash-status-card small { color: #7183a1; font-size: .59rem; font-weight: 760; letter-spacing: .06em; text-transform: uppercase; }
.clash-status-card strong { margin-top: .15rem; font-size: .75rem; }
.clash-status-card div > span { margin-top: .05rem; color: #71819a; font-size: .6rem; }
.clash-status-card > i { width: .45rem; height: .45rem; border-radius: 50%; background: #a7b6c9; }
.clash-status-card > i.active { background: #35bf5b; box-shadow: 0 0 0 .25rem rgb(53 191 91 / 12%); }

.relay-viewer {
  --arena-board-size: clamp(30rem, calc(100dvh - 15rem), 42rem);
  --arena-content-height: calc(var(--arena-board-size) + 6.35rem);
  position: relative;
  display: grid;
  overflow-anchor: none;
  width: 100%;
  gap: var(--space-md, 1rem);
  padding: .55rem clamp(.75rem, 1.5vw, 1.75rem) 3rem;
  background: var(--color-bg, #f3f6fa);
  color: var(--color-text, #17202a);
}
.tournament-heading { display: flex; align-items: end; justify-content: space-between; gap: var(--space-xl, 2rem); padding-block-end: .55rem; border-block-end: 1px solid var(--color-border, #d5dbe1); }
.tournament-heading__title { display: grid; align-items: center; min-width: 0; }
.title-line { display: flex; align-items: center; gap: var(--space-sm, .5rem); min-width: 0; }
.tournament-heading h1, .tournament-heading p { margin: 0; }
.tournament-heading h1 { max-width: 54rem; font-size: clamp(1.45rem, 2.7vw, 2.15rem); letter-spacing: -.035em; line-height: 1.05; overflow-wrap: anywhere; }
.tournament-heading__title > p { margin-block-start: .18rem; color: var(--color-text-muted, #607080); font-size: .75rem; }
.relay-status, .relay-spectators { display: inline-flex; align-items: center; gap: .25rem; min-height: 1.45rem; padding-inline: .5rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: 999px; color: var(--color-text-muted, #607080); font-size: .62rem; font-weight: 730; white-space: nowrap; }
.tournament-heading__controls, .relay-broadcast__selectors { display: flex; align-items: end; gap: .65rem; }
.relay-broadcast__selectors label { display: grid; gap: .25rem; min-width: 0; color: var(--color-text-muted, #607080); font-size: .64rem; font-weight: 700; }
.relay-broadcast__selectors label:last-of-type { width: clamp(16rem, 27vw, 27rem); }
.relay-broadcast__selectors select { width: 100%; min-height: 2.35rem; padding: .4rem 2rem .4rem .65rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: var(--radius-sm, .35rem); background: var(--color-surface, #fff); color: var(--color-text, #17202a); font: inherit; font-size: .77rem; }
.event-page-link { display: inline-flex; min-height: 2.35rem; align-items: center; gap: .35rem; padding-inline: .8rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: var(--radius-sm, .35rem); color: var(--color-text-muted, #607080); font-size: .72rem; font-weight: 730; text-decoration: none; white-space: nowrap; }
.arena { display: grid; grid-template-columns: minmax(25rem, 29rem) var(--arena-board-size) minmax(28rem, 1fr); gap: clamp(.7rem, 1.5vw, 1.2rem); align-items: start; height: var(--arena-content-height); min-height: 0; }
.engine-column, .activity-column { display: grid; min-height: 0; gap: var(--space-sm, .5rem); }
.engine-column { grid-template-rows: repeat(2, minmax(0, 1fr)) auto; height: var(--arena-content-height); }
.relay-engine-slot { display: grid; grid-template-rows: auto minmax(0, 1fr); gap: .3rem; min-width: 0; min-height: 0; padding: .25rem; border-radius: calc(var(--radius-md, .5rem) + .25rem); background: color-mix(in srgb, var(--relay-primary) 5%, transparent); box-shadow: 0 0 1.35rem color-mix(in srgb, var(--relay-primary) 22%, transparent); }
.relay-engine-slot :deep(.engine-panel) { min-height: 0; border-color: var(--color-border, #d5dbe1); }
.relay-engine-slot :deep(.engine-panel--active) { border-color: var(--color-border-strong, #99a8bb); box-shadow: 0 0 0 1px var(--color-border-strong, #99a8bb); }
.relay-team-strip { display: flex; align-items: center; gap: .3rem; min-width: 0; min-height: 1.75rem; padding-inline: .35rem; }
.relay-team-strip strong { flex: 0 0 auto; margin-right: .15rem; color: var(--relay-primary); font-size: .62rem; }
.relay-team-strip__track { display: flex; flex: 1 1 auto; align-items: center; gap: .3rem; min-width: 0; overflow: hidden; }
.relay-team-strip span { flex: 0 0 auto; padding: .2rem .38rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: 999px; background: var(--color-surface, #fff); color: var(--color-text-muted, #607080); font-size: .52rem; font-weight: 680; white-space: nowrap; transition: transform .38s ease, border-color .2s ease, background-color .2s ease, color .2s ease; }
.relay-team-strip span.current { border-color: var(--relay-primary); background: color-mix(in srgb, var(--relay-primary) 12%, var(--color-surface, #fff)); color: var(--color-text, #17202a); }
.relay-chip-enter-active, .relay-chip-leave-active { transition: opacity .2s ease; }
.relay-chip-enter-from, .relay-chip-leave-to { opacity: 0; }
.board-column { width: var(--arena-board-size); min-width: 0; justify-self: center; }
.activity-column { grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: minmax(0, 1fr); height: var(--arena-content-height); }
.activity-column > * { min-width: 0; min-height: 0; height: 100%; }
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

@media (max-width: 96rem) {
  .arena { grid-template-columns: minmax(25rem, 1fr) var(--arena-board-size); height: auto; }
  .engine-column { grid-column: 1; grid-row: 1; }
  .board-column { grid-column: 2; grid-row: 1; }
  .activity-column { grid-column: 1 / -1; grid-row: 2; grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: minmax(24rem, 34rem); height: min(34rem, calc(100dvh - 8rem)); }
}
@media (max-width: 58rem) {
  .tournament-heading { align-items: stretch; flex-direction: column; }
  .tournament-heading__controls { justify-content: space-between; }
  .arena { grid-template-columns: 1fr; }
  .engine-column { grid-column: 1; grid-row: 2; grid-template-columns: 1fr 1fr; grid-template-rows: auto auto; height: auto; }
  .engine-column .arena-cheers { grid-column: 1 / -1; }
  .board-column { grid-column: 1; grid-row: 1; width: min(100%, var(--arena-board-size)); }
  .activity-column { grid-column: 1; grid-row: 3; }
}
@media (max-width: 40rem) {
  .tournament-heading__controls, .relay-broadcast__selectors { align-items: stretch; flex-direction: column-reverse; }
  .relay-broadcast__selectors label, .relay-broadcast__selectors label:last-of-type { width: 100%; }
  .engine-column { grid-template-columns: 1fr; }
  .engine-column .arena-cheers { grid-column: 1; }
  .activity-column { grid-template-columns: 1fr; grid-template-rows: minmax(12rem, 19rem) minmax(18rem, 26rem); }
  .settings-list { grid-template-columns: 1fr; }
}

.relay-showcase { scroll-margin-top: var(--header-height, 4rem); background: linear-gradient(180deg, #f7f9fd, #eef3fa); }
.relay-showcase__inner { width: min(100%, 100rem); margin: 0 auto; padding: clamp(3rem, 6vw, 5rem) clamp(1rem, 3vw, 2.5rem); }
.relay-showcase__heading { display: flex; align-items: end; justify-content: space-between; gap: 2rem; margin-bottom: 1.4rem; }
.relay-showcase__heading > div > span { display: flex; align-items: center; gap: .4rem; color: #2464c2; font-size: .64rem; font-weight: 800; letter-spacing: .11em; text-transform: uppercase; }
.relay-showcase__heading > div > span i { width: .42rem; height: .42rem; border-radius: 50%; background: #2fcb67; box-shadow: 0 0 0 .3rem rgb(47 203 103 / 12%); }
.relay-showcase__heading h2 { margin: .35rem 0 0; font-size: clamp(2.2rem, 4.5vw, 4rem); letter-spacing: -.055em; }
.relay-showcase__heading p { margin: .45rem 0 0; color: #65748b; font-size: .86rem; }
.relay-showcase__back { display: inline-flex; align-items: center; gap: .4rem; color: #49627f; font-size: .73rem; font-weight: 700; text-decoration: none; }


.relay-broadcast { --relay-ink: #101827; display: grid; gap: .85rem; margin: 1.25rem 0 2rem; color: var(--relay-ink); }.relay-broadcast__bar { display: flex; align-items: end; justify-content: space-between; gap: 1rem; padding: .8rem .9rem; border: 1px solid var(--color-border, #d9e0ea); border-radius: .7rem; background: var(--color-surface, #fff); }.relay-broadcast__bar > div:first-child { display: grid; gap: .16rem; }.relay-broadcast__bar > div:first-child span, .relay-broadcast__selectors label > span { color: var(--color-text-muted, #64748b); font-size: .58rem; font-weight: 760; letter-spacing: .08em; text-transform: uppercase; }.relay-broadcast__bar strong { font-size: .92rem; }.relay-broadcast__selectors { display: flex; align-items: end; gap: .65rem; }.relay-broadcast__selectors label { display: grid; gap: .28rem; }.relay-broadcast__selectors select { min-width: 9rem; height: 2rem; border: 1px solid var(--color-border, #d9e0ea); border-radius: .4rem; background: var(--color-surface, #fff); color: var(--color-text, #172033); font-size: .69rem; }.relay-broadcast__state { display: flex; align-items: center; gap: .5rem; min-height: 2rem; padding: 0 .65rem; border-radius: .4rem; background: var(--color-surface-subtle, #f1f5f9); font-size: .65rem; font-weight: 700; }
.handoff-strip { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; overflow: hidden; border: 1px solid var(--color-border, #d9e0ea); border-radius: .65rem; background: var(--color-border, #d9e0ea); }.handoff-strip > div { position: relative; display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: .2rem .8rem; padding: .65rem .8rem .65rem 1rem; background: linear-gradient(90deg, color-mix(in srgb, var(--relay-primary) 10%, white), white 32%); }.handoff-strip > div::before { position: absolute; inset: 0 auto 0 0; width: 4px; background: var(--relay-primary); content: ""; }.handoff-strip span { color: var(--relay-primary); font-size: .56rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }.handoff-strip strong { overflow: hidden; font-size: .78rem; text-overflow: ellipsis; white-space: nowrap; }.handoff-strip small { grid-column: 2; color: var(--color-text-muted, #64748b); font-size: .58rem; }.handoff-strip i { grid-column: 3; grid-row: 1 / 3; width: .55rem; height: .55rem; border-radius: 50%; background: var(--color-border-strong, #9ca8b8); }.handoff-strip i.live { background: var(--relay-primary); box-shadow: 0 0 0 .3rem color-mix(in srgb, var(--relay-primary) 18%, transparent); animation: relay-pulse 1.5s ease-in-out infinite; }
.relay-arena { display: grid; grid-template-columns: minmax(15rem, .58fr) minmax(24rem, 1fr) minmax(25rem, 1.12fr); align-items: stretch; gap: .85rem; min-width: 0; }.relay-activity { display: grid; grid-template-rows: auto minmax(0, 1fr); min-width: 0; min-height: 42rem; overflow: hidden; border: 1px solid var(--color-border, #d9e0ea); border-radius: .65rem; background: var(--color-surface, #fff); }.relay-activity nav { display: grid; grid-template-columns: 1fr 1fr; border-bottom: 1px solid var(--color-border, #d9e0ea); }.relay-activity nav button { display: flex; align-items: center; justify-content: center; gap: .35rem; min-height: 2.45rem; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--color-text-muted, #64748b); cursor: pointer; font-size: .69rem; font-weight: 720; }.relay-activity nav button.active { border-color: var(--color-accent, #315fcc); color: var(--color-text, #172033); }.relay-activity nav span { font-size: .55rem; }.relay-activity__panel { min-height: 0; border: 0; border-radius: 0; }.relay-board { display: grid; align-content: start; gap: .55rem; min-width: 0; }.relay-board__caption { display: flex; align-items: center; justify-content: space-between; gap: .5rem; padding: .5rem .65rem; border: 1px solid var(--color-border, #d9e0ea); border-radius: .45rem; background: var(--color-surface, #fff); }.relay-board__caption span { color: var(--color-text-muted, #64748b); font-size: .6rem; }.relay-board__caption strong { font-size: .7rem; }
.relay-engines { display: grid; grid-template-rows: repeat(2, minmax(0, 1fr)); gap: .85rem; min-width: 0; }.relay-team { display: grid; grid-template-rows: auto auto minmax(0, 1fr); gap: .6rem; min-width: 0; padding: .65rem; border: 1px solid color-mix(in srgb, var(--relay-primary) 35%, var(--color-border, #d9e0ea)); border-radius: .7rem; background: linear-gradient(145deg, color-mix(in srgb, var(--relay-primary) 6%, white), white 38%); }.relay-team > header { display: flex; align-items: end; justify-content: space-between; gap: .7rem; padding: 0 .15rem; }.relay-team > header div { display: grid; gap: .08rem; }.relay-team > header span { color: var(--relay-primary); font-size: .53rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }.relay-team > header h3 { margin: 0; font-size: .82rem; }.relay-team > header > strong { color: var(--color-text-muted, #64748b); font-size: .58rem; font-weight: 600; }.relay-roster { display: grid; grid-template-columns: repeat(auto-fit, minmax(8.5rem, 1fr)); gap: .35rem; }.relay-roster article { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: .25rem .5rem; min-width: 0; padding: .45rem; border: 1px solid var(--color-border, #d9e0ea); border-radius: .45rem; background: color-mix(in srgb, var(--color-surface, #fff) 94%, var(--relay-primary)); transition: border-color .16s, box-shadow .16s, transform .16s; }.relay-roster article > span { display: grid; grid-row: 1 / 3; width: 1.3rem; height: 1.3rem; place-items: center; border-radius: 50%; background: var(--color-surface-subtle, #f1f5f9); color: var(--color-text-muted, #64748b); font-size: .55rem; font-weight: 800; }.relay-roster article > div { display: grid; min-width: 0; }.relay-roster article strong, .relay-roster article small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.relay-roster article strong { font-size: .61rem; }.relay-roster article small { color: var(--color-text-muted, #64748b); font-size: .52rem; }.relay-roster article dl { grid-column: 2; display: flex; gap: .55rem; margin: .1rem 0 0; }.relay-roster article dl div { display: flex; gap: .2rem; }.relay-roster dt { color: var(--color-text-muted, #64748b); font-size: .49rem; text-transform: uppercase; }.relay-roster dd { margin: 0; font-size: .51rem; font-weight: 700; }.relay-roster article.current { border-color: var(--relay-primary); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--relay-primary) 35%, transparent); }.relay-roster article.thinking { transform: translateY(-1px); box-shadow: 0 0 0 2px color-mix(in srgb, var(--relay-primary) 22%, transparent), 0 .35rem .8rem color-mix(in srgb, var(--relay-primary) 13%, transparent); }.relay-roster article.thinking > span { background: var(--relay-primary); color: white; }.relay-team :deep(.engine-panel) { min-height: 13.5rem; grid-template-rows: auto minmax(3rem, .5fr) minmax(5.5rem, 1fr); border-color: color-mix(in srgb, var(--relay-primary) 24%, var(--color-border, #d9e0ea)); border-inline-start-color: var(--relay-primary); }.relay-team :deep(.engine-panel--active) { box-shadow: 0 0 0 2px color-mix(in srgb, var(--relay-primary) 20%, transparent); }
@keyframes relay-pulse { 50% { opacity: .55; transform: scale(.85); } }
@keyframes node-thinking { 50% { transform: translateY(-3px) scale(1.04); box-shadow: 0 0 0 .3rem color-mix(in srgb, var(--node-color) 14%, transparent), 0 0 7rem 2.5rem color-mix(in srgb, var(--node-color) 68%, transparent); } }
@keyframes countdown-page-pulse { 0% { opacity: 1; } 42% { opacity: .32; } 100% { opacity: 0; } }
@media (max-width: 80rem) { .clash-status-card { display: none; }.clash-node--2 { left: 27%; }.clash-node--3 { right: 27%; } }
@media (max-width: 88rem) { .relay-arena { grid-template-columns: minmax(14rem, .55fr) minmax(22rem, 1fr); }.relay-engines { grid-column: 1 / -1; grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: none; }.relay-activity { min-height: 34rem; } }
@media (max-width: 60rem) { .clash-hero { display: block; }.clash-hero__content { align-content: start; padding-bottom: 2rem; }.clash-orbit { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .8rem; height: auto; margin-top: 1.2rem; }.clash-orbit__rings { display: none; }.clash-node { position: relative; inset: auto; width: auto; }.clash-node__card { width: min(100%, 12rem); }.clash-actions { margin-top: 1.5rem; }.clash-cheer-rail { position: relative; right: auto; bottom: auto; left: auto; width: min(calc(100% - 2rem), 40rem); margin: 0 auto 1rem; transform: none; }.relay-showcase__heading { align-items: start; flex-direction: column; }.relay-broadcast__bar { align-items: stretch; flex-direction: column; }.relay-broadcast__selectors { flex-wrap: wrap; }.relay-arena { grid-template-columns: 1fr; }.relay-board { grid-row: 1; }.relay-activity { min-height: 26rem; }.relay-engines { grid-template-columns: 1fr; }.handoff-strip { grid-template-columns: 1fr; } }
@media (max-width: 60rem) { .clash-orbit--2 .clash-node { inset: auto; } }
@media (max-width: 38rem) { .clash-team-panel { top: 0; width: 100vw; padding: max(1rem, env(safe-area-inset-top)) 1rem max(1rem, env(safe-area-inset-bottom)); border: 0; }.clash-hero__content { padding-top: 1.4rem; }.clash-heading h1 { font-size: clamp(2.7rem, 13vw, 4.2rem); }.clash-countdown { margin-top: 1rem; }.clash-countdown > div { padding: .85rem .25rem; }.clash-countdown strong { font-size: clamp(2rem, 13vw, 3.2rem); }.clash-countdown span { font-size: .5rem; letter-spacing: .1em; }.clash-schedule { text-align: center; }.clash-orbit { grid-template-columns: 1fr 1fr; }.clash-node__beacon > span { width: 3.8rem; height: 3.8rem; }.clash-node__card { min-width: 0; padding-inline: .4rem; }.clash-node__card strong { max-width: 7.5rem; }.clash-cheer-rail { right: auto; left: auto; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); width: calc(100% - 1.5rem); gap: .4rem; transform: none; }.clash-cheer-action { min-width: 0; width: 100%; padding-right: .5rem; }.relay-broadcast__selectors { align-items: stretch; flex-direction: column; }.relay-broadcast__selectors select { width: 100%; }.handoff-strip > div { grid-template-columns: minmax(0, 1fr) auto; }.handoff-strip > div > span { grid-column: 1 / -1; }.handoff-strip small { grid-column: 1; }.handoff-strip i { grid-column: 2; }.relay-roster { grid-template-columns: 1fr; } }
:global(:root[data-theme="dark"] .engine-clash-page) { --clash-ink: #f4f8ff; background: #08111e; color: #eef4ff; }
:global(:root[data-theme="dark"] .engine-clash-page--final-minute .clash-hero) { background: radial-gradient(circle at 50% 42%, #20395e, #0d1d32 58%, #07101d); }
:global(:root[data-theme="dark"] .relay-showcase) { background: linear-gradient(180deg, #0b1420, #0e1927); color: #eef4ff; }
:global(:root[data-theme="dark"] .relay-showcase__heading p),
:global(:root[data-theme="dark"] .relay-showcase__back) { color: #9bacbf; }
:global(:root[data-theme="dark"] .relay-broadcast) { --relay-ink: #eef4ff; }
:global(:root[data-theme="dark"] .handoff-strip > div) { background: linear-gradient(90deg, color-mix(in srgb, var(--relay-primary) 16%, #111b29), #111821 38%); }
:global(:root[data-theme="dark"] .relay-team) { background: linear-gradient(145deg, color-mix(in srgb, var(--relay-primary) 10%, #111b29), #111821 42%); }
:global(:root[data-theme="dark"] .clash-hero) {
  border-color: #263b56;
  background:
    radial-gradient(circle at 50% 43%, rgb(37 65 103 / 78%) 0, rgb(17 35 58 / 58%) 26%, transparent 53%),
    radial-gradient(circle at 20% 76%, rgb(39 94 165 / 24%), transparent 35%),
    radial-gradient(circle at 82% 74%, rgb(49 104 176 / 22%), transparent 36%),
    linear-gradient(155deg, #0b1625 0%, #0a1422 52%, #07101c 100%);
}
:global(:root[data-theme="dark"] .clash-hero::before),
:global(:root[data-theme="dark"] .clash-hero::after) { background: rgb(50 88 139 / 20%); }
:global(:root[data-theme="dark"] .clash-hero__wash) { opacity: .32; background-image: radial-gradient(circle, rgb(99 151 222 / 28%) 0 1px, transparent 1.4px); }
:global(:root[data-theme="dark"] .chess-piece) { color: #6c8fbd; opacity: .13; }
:global(:root[data-theme="dark"] .clash-cheer-rail) { border-color: rgb(119 155 204 / 24%); background: rgb(12 27 46 / 68%); box-shadow: 0 .8rem 2.2rem rgb(0 0 0 / 28%); }
:global(:root[data-theme="dark"] .clash-cheer-action) { border-color: color-mix(in srgb, var(--relay-primary) 42%, #263b56); background: linear-gradient(135deg, color-mix(in srgb, var(--relay-primary) 18%, #142339), #101b2a); color: color-mix(in srgb, var(--relay-primary) 48%, white); box-shadow: inset 0 1px rgb(255 255 255 / 5%); }
:global(:root[data-theme="dark"] .clash-cheer-action small) { color: #94a8c2; }
:global(:root[data-theme="dark"] .clash-team-panel) { border-color: color-mix(in srgb, var(--relay-primary) 32%, #263b56); background: color-mix(in srgb, var(--relay-primary) 7%, #0d1826); color: #eef4ff; box-shadow: 1.2rem 0 3rem rgb(0 0 0 / 42%); }
:global(:root[data-theme="dark"] .clash-team-panel > header) { border-color: #263b56; }
:global(:root[data-theme="dark"] .clash-team-panel > header button),
:global(:root[data-theme="dark"] .clash-team-panel__roster > article) { border-color: color-mix(in srgb, var(--relay-primary) 28%, #263b56); background: #111e2e; color: #eef4ff; }
:global(:root[data-theme="dark"] .clash-team-panel__motto),
:global(:root[data-theme="dark"] .clash-team-panel__roster > article small),
:global(:root[data-theme="dark"] .clash-team-panel__empty) { color: #9bacc2; }
:global(:root[data-theme="dark"] .clash-team-panel__rating) { border-color: #263b56; }
:global(:root[data-theme="dark"] .clash-kicker) { border-color: rgb(105 158 231 / 16%); background: rgb(35 67 108 / 58%); color: #8fbbff; }
:global(:root[data-theme="dark"] .clash-heading h1),
:global(:root[data-theme="dark"] .clash-countdown strong) { color: #f6f9ff; }
:global(:root[data-theme="dark"] .clash-countdown span) { color: #82b1ff; }
:global(:root[data-theme="dark"] .clash-schedule),
:global(:root[data-theme="dark"] .clash-orbit__empty) { color: #9bacc2; }
:global(:root[data-theme="dark"] .clash-orbit__rings span:nth-child(1)) { border-color: rgb(135 174 224 / 42%); box-shadow: 0 0 1.8rem rgb(73 123 190 / 22%); }
:global(:root[data-theme="dark"] .clash-orbit__rings span:nth-child(2)) { border-color: rgb(125 166 222 / 30%); }
:global(:root[data-theme="dark"] .clash-orbit__rings span:nth-child(3)) { border-color: rgb(94 139 200 / 22%); }
:global(:root[data-theme="dark"] .clash-node__card) { border-color: rgb(127 165 216 / 20%); background: linear-gradient(rgb(15 31 52 / 52%), rgb(10 23 40 / 88%)); box-shadow: 0 .8rem 2.2rem rgb(0 0 0 / 24%); color: #eef4ff; }
:global(:root[data-theme="dark"] .clash-node__card small) { background: color-mix(in srgb, var(--node-color) 16%, #101b2a); color: color-mix(in srgb, var(--node-color) 62%, white); }
:global(:root[data-theme="dark"] .clash-status-card) { border-color: rgb(119 155 204 / 22%); background: rgb(12 27 46 / 74%); box-shadow: 0 .8rem 2.2rem rgb(0 0 0 / 24%); color: #eef4ff; }
:global(:root[data-theme="dark"] .clash-status-card__icon) { background: rgb(30 55 87 / 72%); color: #84b3ff; }
:global(:root[data-theme="dark"] .clash-status-card small),
:global(:root[data-theme="dark"] .clash-status-card div > span) { color: #94a8c2; }
:global(.public-main:has(.engine-clash-page--finale:not(.engine-clash-page--arena))) { min-height: 0; padding-block: 0; }
.engine-clash-page--finale:not(.engine-clash-page--arena) { height: calc(100dvh - var(--header-height, 4rem)); min-height: 0; margin-block-end: 0; overflow: hidden; background: #070604; color: #f6eedb; }
.engine-clash-page--finale .clash-hero { height: 100%; min-height: 0; border-color: #3c3018; background: radial-gradient(ellipse 48% 42% at 50% 47%, rgb(210 163 62 / 18%), transparent 72%), radial-gradient(circle at 16% 24%, rgb(184 132 37 / 9%), transparent 31%), radial-gradient(circle at 84% 78%, rgb(219 170 66 / 8%), transparent 32%), linear-gradient(135deg, #11100d 0%, #080705 52%, #12100b 100%); box-shadow: inset 0 0 8rem rgb(0 0 0 / 62%); }
.engine-clash-page--finale.engine-clash-page--final-minute .clash-hero { border-color: #6e5425; background: radial-gradient(ellipse 52% 46% at 50% 45%, rgb(230 183 82 / 26%), transparent 70%), radial-gradient(circle at 18% 72%, rgb(182 126 30 / 10%), transparent 34%), linear-gradient(135deg, #151108, #080705 56%, #171208); }
.engine-clash-page--finale .countdown-pulse-layer { background: radial-gradient(circle at 50% 45%, rgb(235 190 91 / 21%), rgb(183 128 31 / 8%) 52%, transparent 78%); box-shadow: inset 0 0 0 .45rem rgb(217 166 63 / 12%), inset 0 0 9rem rgb(219 168 66 / 17%); }
.engine-clash-page--finale .clash-hero::before, .engine-clash-page--finale .clash-hero::after { background: rgb(215 166 66 / 12%); filter: blur(6rem); }
.engine-clash-page--finale .clash-hero__wash { opacity: .22; background-image: radial-gradient(circle, rgb(226 188 103 / 34%) 0 1px, transparent 1.2px); background-size: 5rem 5rem; mask-image: radial-gradient(ellipse at center, black, transparent 78%); }
.engine-clash-page--finale .chess-piece { color: #d5ab54; opacity: .028; filter: none; }.engine-clash-page--finale .chess-piece--pawn, .engine-clash-page--finale .chess-piece--bishop, .engine-clash-page--finale .chess-piece--queen, .engine-clash-page--finale .chess-piece--rook, .engine-clash-page--finale .chess-piece--knight-small { display: none; }
.engine-clash-page--finale .clash-hero__content { width: min(100%, 84rem); align-content: center; padding: clamp(1.25rem, 3vh, 2rem) clamp(1rem, 5vw, 5rem) 5.25rem; }
.engine-clash-page--finale .clash-kicker { border-color: rgb(221 176 79 / 35%); background: rgb(12 10 6 / 72%); box-shadow: inset 0 1px rgb(255 237 189 / 7%); color: #d8ad55; }
.engine-clash-page--finale .clash-heading h1 { margin-top: .65rem; background: linear-gradient(180deg, #fff5d6 4%, #edcc7b 52%, #b78325 100%); color: #f3d17b; font-size: clamp(3.2rem, 6vw, 5.5rem); font-weight: 720; letter-spacing: -.055em; text-shadow: 0 .15rem 1.5rem rgb(220 166 54 / 13%); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
.engine-clash-page--finale .clash-schedule { color: #95886d; }
.engine-clash-page--finale .clash-countdown { margin-top: .65rem; }.engine-clash-page--finale .clash-countdown > div { padding: .6rem .75rem .7rem; }.engine-clash-page--finale .clash-countdown strong { color: #f5d787; font-size: clamp(2.5rem, 5vw, 4.25rem); }.engine-clash-page--finale .clash-countdown span { color: #a98743; }
.engine-clash-page--finale .countdown-sound { border-color: rgb(220 174 76 / 31%); background: rgb(18 15 9 / 78%); color: #d7ad58; }
.engine-clash-page--finale .clash-orbit { width: min(100%, 76rem); height: clamp(16rem, 30vh, 20rem); max-height: 20rem; margin-top: .25rem; }
.engine-clash-page--finale .clash-orbit::after { position: absolute; z-index: 3; top: 45%; left: 50%; display: grid; width: 4.2rem; height: 4.2rem; place-items: center; border: 1px solid rgb(221 177 84 / 34%); border-radius: 50%; background: #0b0905; box-shadow: 0 0 0 .55rem rgb(211 164 66 / 5%), 0 0 3rem rgb(213 164 63 / 14%); color: #dcb664; content: "VS"; font-size: .77rem; font-weight: 850; letter-spacing: .13em; transform: translate(-50%, -50%); }
.engine-clash-page--finale .clash-orbit__rings { inset: 14% 8% 8%; }
.engine-clash-page--finale .clash-orbit__rings span:nth-child(1) { inset: 8% 1% 18%; border: 1px solid rgb(210 165 70 / 26%); box-shadow: 0 0 2rem rgb(208 160 59 / 6%); }
.engine-clash-page--finale .clash-orbit__rings span:nth-child(2) { inset: 0 7% 7%; border-color: rgb(212 166 70 / 13%); }
.engine-clash-page--finale .clash-orbit__rings span:nth-child(3) { inset: 18% 16% 3%; border-color: rgb(212 166 70 / 9%); }
.engine-clash-page--finale .clash-node { width: 18rem; color: #f8f0de; }
.engine-clash-page--finale .clash-orbit--2 .clash-node--1 { top: 17%; left: 6%; }
.engine-clash-page--finale .clash-orbit--2 .clash-node--2 { top: 17%; right: 6%; bottom: auto; left: auto; }
.engine-clash-page--finale .clash-node::before { top: -3rem; width: 24rem; height: 16rem; background: radial-gradient(ellipse, color-mix(in srgb, var(--node-color) 24%, transparent), transparent 67%); filter: blur(2rem); }
.engine-clash-page--finale .clash-node__beacon { z-index: 2; width: 5.8rem; height: 3rem; background: radial-gradient(ellipse, color-mix(in srgb, var(--node-color) 30%, transparent), transparent 68%); box-shadow: none; }
.engine-clash-page--finale .clash-node__beacon::before { right: .3rem; bottom: .1rem; left: .3rem; height: 1.15rem; border: 1px solid rgb(223 181 92 / 35%); }
.engine-clash-page--finale .clash-node__beacon::after { right: 1.15rem; bottom: .35rem; left: 1.15rem; height: .7rem; border: 1px solid rgb(223 181 92 / 19%); }
.engine-clash-page--finale .clash-node__beacon > span { width: 4.15rem; height: 4.15rem; border: 1px solid color-mix(in srgb, var(--node-secondary) 68%, white); background: linear-gradient(145deg, color-mix(in srgb, var(--node-primary, var(--node-color)) 80%, white), var(--node-color)); box-shadow: 0 0 0 .3rem #0d0b07, 0 .8rem 2.2rem rgb(0 0 0 / 48%), 0 0 2.8rem color-mix(in srgb, var(--node-color) 28%, transparent); color: white; }
.engine-clash-page--finale .clash-node__card { width: 100%; min-width: 0; min-height: 9.5rem; margin-top: -1rem; padding: 3.15rem 1.4rem 1.1rem; border: 1px solid color-mix(in srgb, var(--node-color) 62%, #4d432e); border-radius: 1rem; background: radial-gradient(circle at 50% 0, color-mix(in srgb, var(--node-color) 34%, transparent), transparent 58%), linear-gradient(145deg, color-mix(in srgb, var(--node-color) 24%, #17130d), color-mix(in srgb, var(--node-secondary) 16%, #080705)); box-shadow: inset 0 1px color-mix(in srgb, var(--node-secondary) 20%, transparent), 0 1.4rem 3.2rem rgb(0 0 0 / 38%), 0 0 2.5rem color-mix(in srgb, var(--node-color) 13%, transparent); color: #f7efdc; backdrop-filter: blur(18px); }
.engine-clash-page--finale .clash-node__card strong { max-width: 15rem; color: #fff8e9; font-size: 1.35rem; letter-spacing: -.025em; }
.engine-clash-page--finale .clash-node__card > span { margin-top: .35rem; color: color-mix(in srgb, var(--node-secondary) 68%, #ddd2b9); font-size: .7rem; letter-spacing: .03em; }
.engine-clash-page--finale .clash-node__card small { margin-top: .75rem; padding: .35rem .7rem; border: 1px solid color-mix(in srgb, var(--node-color) 38%, transparent); background: color-mix(in srgb, var(--node-color) 16%, #0b0906); color: color-mix(in srgb, var(--node-secondary) 66%, white); }
.engine-clash-page--finale .clash-node__card small i { background: var(--node-color); box-shadow: 0 0 .6rem var(--node-color); }
.engine-clash-page--finale .clash-node--thinking .clash-node__beacon > span { animation: node-thinking 1.3s ease-in-out infinite; }
.engine-clash-page--finale .clash-actions { margin-top: -2.4rem; }.engine-clash-page--finale .clash-primary-action { background: linear-gradient(135deg, #edcd79, #a8731c); box-shadow: 0 .8rem 2rem rgb(175 121 30 / 22%); color: #151006; }
.engine-clash-page--finale .clash-cheer-rail { bottom: 1.25rem; width: min(calc(100% - 2rem), 42rem); padding: .5rem; border-color: rgb(222 177 81 / 20%); background: rgb(10 9 7 / 84%); box-shadow: inset 0 1px rgb(255 238 195 / 5%), 0 1rem 2.8rem rgb(0 0 0 / 38%); }
.engine-clash-page--finale .clash-cheer-action { min-width: 11rem; border-color: color-mix(in srgb, var(--relay-primary) 68%, #65573a); background: linear-gradient(140deg, color-mix(in srgb, var(--relay-primary) 58%, #17130d), color-mix(in srgb, var(--relay-secondary) 42%, #090806)); box-shadow: 0 .45rem 1rem color-mix(in srgb, var(--relay-primary) 16%, transparent); color: white; }
.engine-clash-page--finale .clash-cheer-action > span:first-child { background: linear-gradient(145deg, color-mix(in srgb, var(--relay-secondary) 72%, white), var(--relay-primary)); box-shadow: 0 .3rem .8rem color-mix(in srgb, var(--relay-primary) 24%, transparent); color: white; }
.engine-clash-page--finale .clash-cheer-action small { color: color-mix(in srgb, var(--relay-secondary) 62%, white); }.engine-clash-page--finale .clash-cheer-action:hover { border-color: color-mix(in srgb, var(--relay-primary) 74%, white); box-shadow: 0 .65rem 1.5rem color-mix(in srgb, var(--relay-primary) 22%, transparent); }
.engine-clash-page--finale .clash-status-card { border-color: rgb(219 174 79 / 18%); background: rgb(11 10 8 / 82%); box-shadow: inset 0 1px rgb(255 237 194 / 4%), 0 .8rem 2rem rgb(0 0 0 / 32%); color: #eee4cd; }
.engine-clash-page--finale .clash-status-card__icon { background: rgb(211 165 70 / 10%); color: #d4a94f; }.engine-clash-page--finale .clash-status-card small, .engine-clash-page--finale .clash-status-card div > span { color: #80745e; }.engine-clash-page--finale .clash-status-card > i.active { background: #d6a94e; box-shadow: 0 0 0 .25rem rgb(214 169 78 / 9%); }
.finale-symbols { position: absolute; z-index: 0; inset: 0; overflow: hidden; color: #d2a54a; pointer-events: none; }
.finale-symbols span { position: absolute; display: block; opacity: .065; font-family: Georgia, serif; font-size: clamp(2.5rem, 5vw, 5.5rem); line-height: 1; filter: grayscale(1) sepia(1); }
.finale-symbols span:nth-child(1) { top: 7%; left: 2%; transform: rotate(-12deg); }.finale-symbols span:nth-child(2) { top: 3%; left: 23%; transform: rotate(8deg); }.finale-symbols span:nth-child(3) { top: 17%; left: 42%; transform: rotate(-7deg); }.finale-symbols span:nth-child(4) { top: 4%; right: 25%; transform: rotate(13deg); }.finale-symbols span:nth-child(5) { top: 11%; right: 4%; transform: rotate(-10deg); }
.finale-symbols span:nth-child(6) { top: 39%; left: 7%; transform: rotate(9deg); }.finale-symbols span:nth-child(7) { top: 45%; left: 25%; transform: rotate(-14deg); }.finale-symbols span:nth-child(8) { top: 36%; right: 27%; transform: rotate(11deg); }.finale-symbols span:nth-child(9) { top: 42%; right: 7%; transform: rotate(-7deg); }.finale-symbols span:nth-child(10) { bottom: 7%; left: 3%; transform: rotate(12deg); }
.finale-podium { position: relative; display: grid; min-height: calc(100dvh - 2.85rem); overflow: hidden; place-items: center; padding: 4rem 1rem 5rem; background: radial-gradient(ellipse 45% 42% at 50% 42%, rgb(216 168 67 / 19%), transparent 72%), linear-gradient(145deg, #12100c, #070604 58%, #131009); color: #f3e7ca; }
.finale-podium__glow { position: absolute; inset: 12% 23%; border-radius: 50%; background: #b98123; filter: blur(8rem); opacity: .13; }
.finale-podium__symbols { position: absolute; inset: 0; display: grid; grid-template-columns: repeat(4, 1fr); align-items: center; color: #d4a748; font-size: clamp(3rem, 8vw, 8rem); opacity: .035; filter: grayscale(1) sepia(1); }.finale-podium__symbols span:nth-child(even) { align-self: end; transform: rotate(14deg); }.finale-podium__symbols span:nth-child(3n) { align-self: start; transform: rotate(-14deg); }
.finale-podium__content { position: relative; z-index: 1; display: grid; width: min(64rem, 100%); justify-items: center; text-align: center; transform: translateY(-2.6rem); }.finale-podium__medal { font-size: clamp(3.5rem, 7vw, 5.5rem); filter: grayscale(.25) drop-shadow(0 1rem 1.4rem rgb(0 0 0 / 44%)); }.finale-podium__content > p { margin: .65rem 0 0; color: #c7a75f; font-size: .7rem; font-weight: 850; letter-spacing: .2em; text-transform: uppercase; }.finale-podium__content h1 { margin: .55rem 0 1.8rem; background: linear-gradient(180deg, color-mix(in srgb, var(--relay-secondary) 72%, white), var(--relay-primary)); color: var(--relay-primary); font-size: clamp(3rem, 8vw, 6.5rem); line-height: .95; text-shadow: 0 .3rem 2rem color-mix(in srgb, var(--relay-primary) 22%, transparent); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
.finale-winning-bench { display: flex; flex-wrap: wrap; justify-content: center; gap: .45rem; width: 100%; }.finale-winning-bench article { min-width: 8.5rem; padding: .55rem .85rem; border: 1px solid rgb(215 170 78 / 10%); border-radius: .55rem; background: rgb(13 11 8 / 52%); box-shadow: none; text-align: center; }.finale-winning-bench strong { color: #c9c1b1; font-size: .68rem; font-weight: 650; }
.finale-podium__stand { position: absolute; z-index: -1; top: calc(100% + .85rem); left: 50%; width: min(36rem, 72vw); height: 100dvh; overflow: hidden; border: 1px solid #95886d; border-bottom: 0; border-radius: .8rem .8rem 0 0; background-color: #665f52; background-image: linear-gradient(335deg, #625b4e 1.45rem, transparent 1.45rem), linear-gradient(155deg, #827763 1.45rem, transparent 1.45rem), linear-gradient(335deg, #575248 1.45rem, transparent 1.45rem), linear-gradient(155deg, #746a58 1.45rem, transparent 1.45rem); background-position: 0 .1rem, .25rem 2.2rem, 1.8rem 1.95rem, 2.1rem .4rem; background-size: 3.65rem 3.65rem; box-shadow: inset 0 1px rgb(255 242 205 / 26%), inset 0 0 4rem rgb(31 27 21 / 30%), 0 1.2rem 2.4rem rgb(0 0 0 / 37%); transform: translateX(-50%); }.finale-podium__stand::after { position: absolute; inset: 0; border-radius: inherit; background: radial-gradient(circle at 18% 22%, rgb(255 244 213 / 18%) 0 1px, transparent 1.5px), radial-gradient(circle at 72% 64%, rgb(27 23 18 / 28%) 0 1px, transparent 1.7px), radial-gradient(circle at 42% 78%, rgb(223 201 153 / 12%) 0 1.5px, transparent 2px); background-size: 2.7rem 2.3rem, 3.4rem 3rem, 4.1rem 3.7rem; box-shadow: inset 0 0 3rem rgb(25 21 16 / 22%); content: ""; opacity: .72; pointer-events: none; }.finale-cheer { position: absolute; z-index: 3; bottom: 1.5rem; display: grid; width: 4rem; height: 4rem; place-items: center; padding: 0; border: 1px solid color-mix(in srgb, var(--relay-primary) 58%, #b9a678); border-radius: 50%; background: color-mix(in srgb, var(--relay-primary) 20%, #14100a); box-shadow: inset 0 1px rgb(255 255 255 / 16%), 0 .8rem 1.8rem color-mix(in srgb, var(--relay-primary) 18%, transparent); color: white; cursor: pointer; font: inherit; font-size: 1.55rem; transition: transform 150ms ease, background 150ms ease; }.finale-cheer:hover { background: color-mix(in srgb, var(--relay-primary) 32%, #171108); transform: translateY(-.2rem) scale(1.04); }.finale-cheer:active { transform: scale(.94); }.finale-cheer--left { left: 1.5rem; }.finale-cheer--right { right: 1.5rem; transform: scaleX(-1); }.finale-cheer--right:hover { transform: translateY(-.2rem) scaleX(-1) scale(1.04); }.finale-cheer--right:active { transform: scaleX(-1) scale(.94); }
@media (max-width: 60rem) { .engine-clash-page--finale .clash-hero__content { padding-bottom: 2rem; }.engine-clash-page--finale .clash-orbit { height: auto; margin-top: 2rem; }.engine-clash-page--finale .clash-orbit::after { display: none; }.engine-clash-page--finale .clash-node { width: auto; }.engine-clash-page--finale .clash-node__card { width: min(100%, 18rem); }.engine-clash-page--finale .clash-actions { margin-top: 1.5rem; }.engine-clash-page--finale .clash-cheer-rail { margin-bottom: 1rem; } }
@media (max-width: 54rem) { .finale-podium__stand { width: min(34rem, 86vw); } }
.board-column { position: relative; }.kibitzer-bar { position: absolute; z-index: 5; top: 0; bottom: 0; left: -2.35rem; width: 1.8rem; overflow: hidden; border: 1px solid #31343a; border-radius: .35rem; background: #f4f4f2; box-shadow: 0 .25rem .7rem rgb(0 0 0 / 18%); }.kibitzer-bar__black { position: absolute; top: 0; right: 0; left: 0; min-height: 2%; background: #202124; transition: height .3s ease; }.kibitzer-bar strong { position: absolute; z-index: 1; top: 50%; left: 50%; padding: .18rem .12rem; border-radius: .2rem; background: rgb(255 255 255 / 82%); color: #111; font-size: .52rem; transform: translate(-50%, -50%) rotate(-90deg); white-space: nowrap; }.kibitzer-bar small { position: absolute; z-index: 1; bottom: .35rem; left: 50%; color: #777; font-size: .43rem; font-weight: 800; letter-spacing: .04em; transform: translateX(-50%) rotate(-90deg); transform-origin: center; white-space: nowrap; }
@media (max-width: 38rem) { .finale-winning-bench article { min-width: min(100%, 9rem); }.kibitzer-bar { left: .25rem; opacity: .88; } }
@media (prefers-reduced-motion: reduce) { .countdown-pulse-layer { display: none; } }
</style>
