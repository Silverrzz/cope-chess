<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { api } from "@/api/client";
import ChessViewer from "@/components/chess/ChessViewer.vue";
import MoveList from "@/components/chess/MoveList.vue";
import ChatPanel from "@/components/public/ChatPanel.vue";
import ContentState from "@/components/public/ContentState.vue";
import EnginePanel from "@/components/public/EnginePanel.vue";
import StreamIndicator from "@/components/public/StreamIndicator.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import { clockLabel, errorMessage, resultLabel, statusLabel } from "@/components/public/format";
import type {
  ClockState,
  EngineAnalysis,
  Identifier,
  MoveRecord,
  TournamentDetailResponse,
} from "@/components/public/types";
import type { EventDetailResponse } from "@/types/events";

import type { EngineRelayPayload, RelayRosterMember, RelayTeam } from "./types";

const props = withDefaults(defineProps<{ detail: EventDetailResponse; clockOffsetMs?: number; view?: "event" | "arena" }>(), { clockOffsetMs: 0, view: "event" });

const payload = computed(() => props.detail.custom as EngineRelayPayload);
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
const cheerBursts = ref<Array<{ id: string; color: string; side: "left" | "right"; x: number; y: number; pieces: number[] }>>([]);

let controller: AbortController | null = null;
let stream: EventSource | null = null;
let refreshTimer: number | undefined;
let countdownTimer: number | undefined;
let countdownAudio: HTMLAudioElement | null = null;
let audioPlayPending = false;
let soundPrimed = false;
let lastCheerRequestAt = 0;
const seenCheerIds = new Set<string>();
const cheerTimers = new Set<number>();
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
const whiteAnalysis = computed(() => analysisFor("white", whiteMember.value));
const blackAnalysis = computed(() => analysisFor("black", blackMember.value));
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
  return [
    { label: "Days", value: Math.floor(totalSeconds / 86400) },
    { label: "Hours", value: Math.floor((totalSeconds % 86400) / 3600) },
    { label: "Minutes", value: Math.floor((totalSeconds % 3600) / 60) },
    { label: "Seconds", value: totalSeconds % 60 },
  ];
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
});
onBeforeUnmount(() => {
  closeStream();
  if (countdownTimer !== undefined) window.clearInterval(countdownTimer);
  document.removeEventListener("visibilitychange", handleCountdownResume);
  window.removeEventListener("focus", handleCountdownResume);
  window.removeEventListener("pageshow", handleCountdownResume);
  window.removeEventListener("pointerdown", handleUserActivation);
  window.removeEventListener("keydown", handleUserActivation);
  window.removeEventListener("cope:event-cheer", handleCheerEvent as EventListener);
  if (arenaFitFrame !== undefined) window.cancelAnimationFrame(arenaFitFrame);
  headingResizeObserver?.disconnect();
  window.removeEventListener("resize", scheduleArenaFit);
  window.visualViewport?.removeEventListener("resize", scheduleArenaFit);
  for (const timer of cheerTimers) window.clearTimeout(timer);
  cheerTimers.clear();
  releaseCountdownAudio();
});

watch(() => payload.value.fixtures, () => selectInitial(), { deep: true });
watch([fixtureId, gameId], () => { void loadGame(); });
watch(targetTime, () => syncCountdown());
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
  const viewer = boardColumn.firstElementChild as HTMLElement | null;
  const board = viewer?.querySelector<HTMLElement>(".board-mount");
  if (!viewer || !board) return;
  const viewport = window.visualViewport;
  const viewportBottom = viewport ? viewport.offsetTop + viewport.height : window.innerHeight;
  const availableHeight = Math.floor(viewportBottom - arena.getBoundingClientRect().top - 8);
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
  if (games.some((game) => String(game.id) === gameId.value)) return;
  const preferred = games.find((game) => game.status === "live")
    ?? games.find((game) => game.status === "assigned")
    ?? games.find((game) => game.status === "pending")
    ?? games.at(-1);
  gameId.value = preferred ? String(preferred.id) : "";
}

function selectFixture(value: string): void {
  fixtureId.value = Number(value);
  gameId.value = "";
  gameData.value = null;
  chooseGame();
}

function selectGame(value: string): void {
  gameId.value = value;
  gameData.value = null;
}

async function loadGame(silent = false): Promise<void> {
  if (props.view !== "arena") return;
  closeStream();
  controller?.abort();
  if (!fixture.value || !gameId.value || fixture.value.tournament?.status === "draft") {
    gameData.value = null;
    loading.value = false;
    return;
  }
  controller = new AbortController();
  if (!silent) loading.value = true;
  loadError.value = "";
  try {
    const response = await api.get<TournamentDetailResponse>(`/api/tournaments/${fixture.value.tournament_id}`, {
      query: { game_id: gameId.value },
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
  stream = new EventSource(`/tournaments/${fixture.value.tournament_id}/events?game_id=${encodeURIComponent(gameId.value)}&spectator=0`);
  streamState.value = "connecting";
  stream.onopen = () => { streamState.value = "live"; };
  stream.onerror = () => { streamState.value = "reconnecting"; };
  stream.addEventListener("game.move", handleMove);
  stream.addEventListener("engine.info", handleEngineInfo);
  stream.addEventListener("clock.sync", handleClock);
  stream.addEventListener("spectators.changed", handleSpectators);
  stream.addEventListener("tournament.snapshot", scheduleRefresh);
  stream.addEventListener("tournament.changed", scheduleRefresh);
}

function closeStream(): void {
  stream?.close();
  stream = null;
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
  const records = [...gameData.value.viewer_moves];
  const index = records.findIndex((move) => move.ply === data.move!.ply);
  const wasLatest = selectedPly.value >= records.length;
  if (index >= 0) records[index] = data.move;
  else records.push(data.move);
  records.sort((left, right) => left.ply - right.ply);
  gameData.value.viewer_moves = records;
  if (wasLatest) selectedPly.value = records.length;
  if (gameData.value.clock_state) gameData.value.clock_state = { ...gameData.value.clock_state, running: false };
}

function handleEngineInfo(event: Event): void {
  const data = eventPayload<{ game_id?: Identifier; side?: "white" | "black"; engine_data?: EngineAnalysis }>(event);
  if (!data?.side || !data.engine_data || String(data.game_id) !== gameId.value || !gameData.value) return;
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

async function cheer(team: RelayTeam | null): Promise<void> {
  if (!team) return;
  const now = Date.now();
  if (now - lastCheerRequestAt < 350) return;
  lastCheerRequestAt = now;
  try {
    await api.post(`/api/events/${encodeURIComponent(props.detail.event.slug)}/engine-relay/cheers`, {
      body: { team_id: team.id },
      headers: { "X-Cope-Cheer-Client": cheerClientId },
    });
  } catch {
    return;
  }
}

function handleCheerEvent(event: Event): void {
  const detail = (event as CustomEvent<{ id?: string; team_id?: number }>).detail;
  const teamId = Number(detail?.team_id);
  const cheerId = typeof detail?.id === "string" ? detail.id : "";
  if (!Number.isFinite(teamId) || !cheerId || seenCheerIds.has(cheerId)) return;
  seenCheerIds.add(cheerId);
  if (seenCheerIds.size > 128) seenCheerIds.delete(seenCheerIds.values().next().value!);
  launchCheer(teamId, cheerId);
}

function launchCheer(teamId: number, cheerId: string): void {
  const team = payload.value.teams.find((item) => item.id === teamId);
  if (!team || cheerBursts.value.length >= 6) return;
  const side = team.id === blackTeam.value?.id ? "right" : "left";
  const burst: { id: string; color: string; side: "left" | "right"; x: number; y: number; pieces: number[] } = {
    id: cheerId,
    color: team.primary_color,
    side,
    x: side === "left" ? 4 + Math.random() * 13 : 83 + Math.random() * 13,
    y: 18 + Math.random() * 65,
    pieces: Array.from({ length: 7 }, (_, index) => index),
  };
  cheerBursts.value.push(burst);
  const timer = window.setTimeout(() => {
    cheerTimers.delete(timer);
    cheerBursts.value = cheerBursts.value.filter((item) => item.id !== burst.id);
  }, 900);
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
  const white = side === "white";
  const movesPlayed = moves.value.filter((move) => !move.is_book && ((move.ply % 2 === 1) === white)).length;
  const cycle = team.roster.reduce((total, member) => total + member.relay_moves, 0);
  let offset = movesPlayed % cycle;
  for (const member of team.roster) {
    if (offset < member.relay_moves) return member;
    offset -= member.relay_moves;
  }
  return team.roster[0] ?? null;
}

function analysisFor(side: "white" | "black", member: RelayRosterMember | null): EngineAnalysis | null {
  const analysis = gameData.value?.engine_data?.[side] ?? null;
  if (!analysis || !member) return analysis;
  if (analysis.engine_id !== null && analysis.engine_id !== undefined && String(analysis.engine_id) !== String(member.engine_id)) return null;
  return analysis;
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

function countdownCompletionKey(): string {
  return `cope.event.${props.detail.event.id}.countdown-finished`;
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
    const expected = expectedCountdownAudioTime();
    if (expected !== null) seekCountdownAudio(audio, expected, true);
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
}

function expectedCountdownAudioTime(): number | null {
  if (!finalMinuteActive.value || targetTime.value === null) return null;
  return Math.max(0, Math.min(60, (countdownAudioLengthMs - (targetTime.value - authoritativeNow())) / 1000));
}

function seekCountdownAudio(audio: HTMLAudioElement, expected: number, force = false): void {
  const maximum = Number.isFinite(audio.duration) ? Math.max(0, audio.duration - .015) : 60;
  const position = Math.min(expected, maximum);
  if (!force && Math.abs(audio.currentTime - position) <= .12) return;
  try {
    audio.currentTime = position;
  } catch {
    soundState.value = "loading";
  }
}

async function playCountdownAudio(expected: number, force = false): Promise<void> {
  prepareCountdownAudio(soundState.value === "unavailable");
  const audio = countdownAudio;
  if (!audio || audioPlayPending || countdownFinished.value) return;
  if (!force && soundState.value === "blocked") return;
  seekCountdownAudio(audio, expected, true);
  audioPlayPending = true;
  try {
    await audio.play();
    const corrected = expectedCountdownAudioTime();
    if (corrected !== null) seekCountdownAudio(audio, corrected, true);
    soundState.value = "playing";
  } catch (cause) {
    const name = (cause as { name?: string })?.name;
    soundState.value = name === "NotAllowedError" ? "blocked" : "unavailable";
  } finally {
    audioPlayPending = false;
  }
}

function syncCountdown(forceAudio = false): void {
  nowMs.value = authoritativeNow();
  if (countdownFinished.value || targetTime.value === null) return;
  if (targetTime.value <= nowMs.value) {
    finishCountdown();
    return;
  }
  if (!finalMinuteActive.value) {
    countdownBeat.value = -1;
    if (countdownAudio && !countdownAudio.paused) countdownAudio.pause();
    if (soundState.value === "playing") soundState.value = "armed";
    return;
  }
  const expected = expectedCountdownAudioTime();
  if (expected === null) return;
  const beat = Math.floor(expected + .025);
  if (beat !== countdownBeat.value) countdownBeat.value = beat;
  if (countdownAudio && !countdownAudio.paused) {
    seekCountdownAudio(countdownAudio, expected);
    soundState.value = "playing";
    return;
  }
  void playCountdownAudio(expected, forceAudio);
}

function finishCountdown(): void {
  if (countdownFinished.value) return;
  countdownFinished.value = true;
  countdownBeat.value = -1;
  soundState.value = "finished";
  try {
    window.localStorage.setItem(countdownCompletionKey(), "1");
  } catch {
    countdownFinished.value = true;
  }
  releaseCountdownAudio();
}

async function enableCountdownSound(): Promise<void> {
  if (countdownFinished.value || !countdownVisible.value || soundState.value === "playing") return;
  prepareCountdownAudio(soundState.value === "unavailable");
  const expected = expectedCountdownAudioTime();
  if (expected !== null) {
    await playCountdownAudio(expected, true);
    return;
  }
  const audio = countdownAudio;
  if (!audio || soundPrimed) return;
  audio.muted = true;
  try {
    await audio.play();
    audio.pause();
    audio.currentTime = 0;
    soundPrimed = true;
    soundState.value = "armed";
  } catch {
    soundState.value = "blocked";
  } finally {
    audio.muted = false;
  }
}

function handleCountdownResume(): void {
  if (document.visibilityState === "hidden") return;
  syncCountdown();
}

function handleUserActivation(): void {
  if (countdownFinished.value || !countdownVisible.value || soundState.value === "playing") return;
  if (!soundPrimed || finalMinuteActive.value || ["blocked", "unavailable"].includes(soundState.value)) void enableCountdownSound();
}

</script>

<template>
  <div class="engine-clash-page" :class="{ 'engine-clash-page--final-minute': finalMinuteActive }">
    <div class="cheer-layer" aria-hidden="true">
      <span v-for="burst in cheerBursts" :key="burst.id" class="cheer-burst" :class="`cheer-burst--${burst.side}`" :style="{ left: `${burst.x}%`, top: `${burst.y}%`, '--cheer-color': burst.color }">
        <i v-for="piece in burst.pieces" :key="piece" :style="{ '--piece': piece }"></i>
      </span>
    </div>
    <span v-if="finalMinuteActive" :key="countdownBeat" class="countdown-pulse-layer" aria-hidden="true"></span>
    <section v-if="props.view === 'event'" class="clash-hero" aria-labelledby="clash-title">
      <div class="clash-hero__wash" aria-hidden="true"></div>
      <span class="chess-piece chess-piece--king" aria-hidden="true">♚</span>
      <span class="chess-piece chess-piece--knight" aria-hidden="true">♞</span>
      <span class="chess-piece chess-piece--pawn" aria-hidden="true">♟</span>
      <span class="chess-piece chess-piece--bishop" aria-hidden="true">♝</span>
      <span class="chess-piece chess-piece--queen" aria-hidden="true">♛</span>
      <span class="chess-piece chess-piece--rook" aria-hidden="true">♜</span>
      <span class="chess-piece chess-piece--knight-small" aria-hidden="true">♞</span>

      <div class="clash-hero__content">
        <header class="clash-heading">
          <span class="clash-kicker"><AppIcon name="trophy" :size="15" /> Featured showcase event</span>
          <h1 id="clash-title">{{ detail.event.title }}</h1>
        </header>

        <div v-if="countdownVisible" class="clash-countdown" aria-live="polite">
          <div v-for="part in countdown" :key="part.label">
            <strong>{{ String(part.value).padStart(2, "0") }}</strong>
            <span>{{ part.label }}</span>
          </div>
        </div>
        <p v-if="countdownVisible" class="clash-schedule"><AppIcon name="clock" :size="18" /> {{ scheduleLabel }}</p>

        <div class="clash-orbit" :class="`clash-orbit--${Math.min(showcaseTeams.length, 4)}`">
          <div class="clash-orbit__rings" aria-hidden="true"><span></span><span></span><span></span></div>
          <article v-for="(team, index) in showcaseTeams" :key="team.id" class="clash-node" :class="[`clash-node--${index + 1}`, { 'clash-node--thinking': team.thinking }]" :style="nodeStyle(team)">
            <div class="clash-node__beacon"><span><AppIcon name="trophy" :size="31" /></span></div>
            <div class="clash-node__card">
              <strong>{{ team.name }}</strong>
              <span>{{ team.engineCount }}-engine relay team</span>
              <small><i></i>{{ team.thinking ? "Playing now" : team.current ? "On relay" : isLive ? "Ready" : "Relay team" }}</small>
            </div>
          </article>
          <p v-if="!showcaseTeams.length" class="clash-orbit__empty">Teams will join the circuit when the lineup is locked.</p>
        </div>

        <div class="clash-actions">
          <RouterLink :to="{ name: 'event-arena', params: { slug: detail.event.slug } }" class="clash-primary-action"><AppIcon name="trophy" :size="19" /> Enter arena <AppIcon name="arrow-right" :size="18" /></RouterLink>
        </div>
      </div>

      <div v-if="whiteTeam || blackTeam" class="clash-cheer-rail" aria-label="Cheer for a relay team">
        <button v-if="whiteTeam" type="button" class="clash-cheer-action" :style="teamStyle(whiteTeam)" @click="cheer(whiteTeam)"><span><AppIcon name="trophy" :size="18" /></span><span><small>Cheer for</small><strong>{{ whiteTeam.short_name || whiteTeam.name }}</strong></span></button>
        <button v-if="blackTeam" type="button" class="clash-cheer-action" :style="teamStyle(blackTeam)" @click="cheer(blackTeam)"><span><AppIcon name="trophy" :size="18" /></span><span><small>Cheer for</small><strong>{{ blackTeam.short_name || blackTeam.name }}</strong></span></button>
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
        <label v-if="fixture?.games.length"><span>Board</span><select :value="gameId" @change="selectGame(($event.target as HTMLSelectElement).value)"><option v-for="game in fixture.games" :key="game.id" :value="String(game.id)">Game {{ game.game_number ?? game.id }} · {{ statusLabel(game.status) }}<template v-if="game.result"> · {{ resultLabel(game.result) }}</template></option></select></label>
        <StreamIndicator v-if="gameData && !['finished', 'aborted'].includes(gameData.tournament.status)" :state="streamState" />
      </div>
      </div>
    </header>

    <ContentState v-if="!payload.fixtures.length" kind="empty" title="The first relay fixture is being assembled" message="Teams and engines will appear here as soon as the control room registers them." />
    <ContentState v-else-if="!fixture?.games.length" kind="empty" title="This relay is not on the board yet" message="The fixture exists as an unrated tournament draft. Its games appear here when it is scheduled or started." />
    <ContentState v-else-if="loading && !gameData" kind="loading" title="Synchronising the relay" />
    <ContentState v-else-if="loadError && !gameData" kind="error" :message="loadError" action-label="Try again" @action="loadGame" />

    <section v-else-if="gameData && viewerGame" ref="arenaElement" class="arena" :aria-label="`${whiteTeam?.name ?? 'White'} versus ${blackTeam?.name ?? 'Black'}`">
      <div class="engine-column">
        <div class="relay-engine-slot" :style="teamStyle(blackTeam)"><EnginePanel side="black" :name="blackMember?.display_name || blackTeam?.name || ''" :engine-id="blackMember?.engine_id ?? null" :clock="`${(blackMember?.nodes ?? 0).toLocaleString()} nodes`" :analysis="blackAnalysis" :position-fen="currentPositionFen" :active="activeSide === 'black'" /></div>
        <div class="relay-engine-slot" :style="teamStyle(whiteTeam)"><EnginePanel side="white" :name="whiteMember?.display_name || whiteTeam?.name || ''" :engine-id="whiteMember?.engine_id ?? null" :clock="`${(whiteMember?.nodes ?? 0).toLocaleString()} nodes`" :analysis="whiteAnalysis" :position-fen="currentPositionFen" :active="activeSide === 'white'" /></div>
        <dl class="game-facts">
          <div :style="teamStyle(blackTeam)"><dt>Black team</dt><dd>{{ blackTeam?.name ?? '-' }} <button v-if="blackTeam" type="button" @click="cheer(blackTeam)">Cheer</button></dd></div>
          <div :style="teamStyle(whiteTeam)"><dt>White team</dt><dd>{{ whiteTeam?.name ?? '-' }} <button v-if="whiteTeam" type="button" @click="cheer(whiteTeam)">Cheer</button></dd></div>
          <div><dt>Status</dt><dd>{{ statusLabel(gameStatus) }}</dd></div>
          <div><dt>Result</dt><dd>{{ resultLabel(viewerGame.result) }}</dd></div>
        </dl>
      </div>
      <div ref="boardColumnElement" class="board-column">
        <ChessViewer :opening="opening" :moves="moves" :model-value="selectedPly" :label="`${whiteTeam?.name ?? 'White'} versus ${blackTeam?.name ?? 'Black'} relay game`" @update:model-value="selectedPly = $event" @position="currentPositionFen = $event.fen" />
      </div>
      <aside class="activity-column" aria-label="Game activity">
        <MoveList class="arena-moves" :moves="moves.map((move) => move.san || move.uci)" :uci-moves="moves.map((move) => move.uci)" :fen="opening.fen" :book-plies="bookPlies" :model-value="selectedPly" @update:model-value="selectedPly = $event" />
        <ChatPanel class="arena-chat" :messages="detail.chat_messages" :settings="detail.chat_settings" :event-slug="detail.event.slug" @sent="appendChat" />
      </aside>
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

.cheer-layer { position: fixed; z-index: 46; inset: 0; overflow: hidden; pointer-events: none; }
.cheer-burst { position: absolute; width: 1px; height: 1px; }
.cheer-burst i { --angle: calc(var(--piece) * 51deg - 150deg); position: absolute; width: .28rem; height: .5rem; border-radius: .08rem; background: var(--cheer-color); opacity: 0; transform: rotate(var(--angle)) translateY(0); animation: cheer-pop .82s ease-out forwards; animation-delay: calc(var(--piece) * 12ms); }
.cheer-burst i:nth-child(even) { width: .22rem; height: .22rem; border-radius: 50%; filter: brightness(1.22); }
.cheer-button { min-height: 2.2rem; padding: 0 .85rem; border: 1px solid color-mix(in srgb, var(--relay-primary) 55%, white); border-radius: 999px; background: color-mix(in srgb, var(--relay-primary) 13%, white); color: color-mix(in srgb, var(--relay-primary) 82%, #071426); cursor: pointer; font-size: .65rem; font-weight: 800; }
.cheer-button:hover { background: color-mix(in srgb, var(--relay-primary) 22%, white); }
.cheer-button:active { transform: scale(.96); }
.cheer-button--arena { min-height: 1.75rem; padding-inline: .65rem; font-size: .57rem; }
@keyframes cheer-pop { 0% { opacity: 0; transform: rotate(var(--angle)) translateY(0) scale(.5); } 14% { opacity: .95; } 100% { opacity: 0; transform: rotate(var(--angle)) translateY(-3.4rem) scale(1); } }

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
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  width: min(100%, 50rem);
  margin-top: 1.1rem;
}

.clash-countdown > div {
  position: relative;
  display: grid;
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
}

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
  align-items: center;
  gap: .65rem;
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
  background: var(--color-background, #f7f9fd);
  color: var(--color-text, #17202a);
}
.tournament-heading { display: flex; align-items: end; justify-content: space-between; gap: var(--space-xl, 2rem); padding-block-end: .55rem; border-block-end: 1px solid var(--color-border, #d5dbe1); }
.tournament-heading__title { display: grid; align-items: center; min-width: 0; }
.title-line { display: flex; align-items: center; gap: var(--space-sm, .5rem); min-width: 0; }
.tournament-heading h1, .tournament-heading p, .game-facts { margin: 0; }
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
.relay-engine-slot { min-width: 0; min-height: 0; }
.relay-engine-slot :deep(.engine-panel) { --side-color: var(--relay-primary); min-height: 0; border-color: color-mix(in srgb, var(--relay-primary) 30%, var(--color-border, #d5dbe1)); }
.relay-engine-slot :deep(.engine-panel--active) { box-shadow: 0 0 0 1px var(--relay-primary), 0 0 1.4rem color-mix(in srgb, var(--relay-primary) 20%, transparent); }
.board-column { width: var(--arena-board-size); min-width: 0; justify-self: center; }
.activity-column { grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: minmax(0, 1fr); height: var(--arena-content-height); }
.activity-column > * { min-width: 0; min-height: 0; height: 100%; }
.game-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: repeat(2, minmax(0, 1fr)); gap: 1px; height: 6.35rem; overflow: hidden; border: 1px solid var(--color-border, #d5dbe1); border-radius: var(--radius-md, .5rem); background: var(--color-border, #d5dbe1); }
.game-facts div { min-width: 0; padding: .65rem .75rem; background: var(--color-surface, #fff); }
.game-facts dt { color: var(--color-text-muted, #607080); font-size: .59rem; font-weight: 750; letter-spacing: .04em; text-transform: uppercase; }
.game-facts dd { display: flex; align-items: center; justify-content: space-between; gap: .35rem; overflow: hidden; margin: .18rem 0 0; font-size: .75rem; font-weight: 700; text-overflow: ellipsis; white-space: nowrap; }
.game-facts button { padding: .15rem .45rem; border: 1px solid color-mix(in srgb, var(--relay-primary) 50%, var(--color-border, #d5dbe1)); border-radius: 999px; background: color-mix(in srgb, var(--relay-primary) 10%, var(--color-surface, #fff)); color: var(--relay-primary); cursor: pointer; font: inherit; font-size: .58rem; }

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
  .engine-column .game-facts { grid-column: 1 / -1; }
  .board-column { grid-column: 1; grid-row: 1; width: min(100%, var(--arena-board-size)); }
  .activity-column { grid-column: 1; grid-row: 3; }
}
@media (max-width: 40rem) {
  .tournament-heading__controls, .relay-broadcast__selectors { align-items: stretch; flex-direction: column-reverse; }
  .relay-broadcast__selectors label, .relay-broadcast__selectors label:last-of-type { width: 100%; }
  .engine-column { grid-template-columns: 1fr; }
  .engine-column .game-facts { grid-column: 1; }
  .activity-column { grid-template-columns: 1fr; grid-template-rows: minmax(12rem, 19rem) minmax(18rem, 26rem); }
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
@media (max-width: 60rem) { .clash-hero__content { align-content: start; }.clash-orbit { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .8rem; height: auto; margin-top: 1.2rem; }.clash-orbit__rings { display: none; }.clash-node { position: relative; inset: auto; width: auto; }.clash-node__card { width: min(100%, 12rem); }.clash-actions { margin-top: 1.5rem; }.relay-showcase__heading { align-items: start; flex-direction: column; }.relay-broadcast__bar { align-items: stretch; flex-direction: column; }.relay-broadcast__selectors { flex-wrap: wrap; }.relay-arena { grid-template-columns: 1fr; }.relay-board { grid-row: 1; }.relay-activity { min-height: 26rem; }.relay-engines { grid-template-columns: 1fr; }.handoff-strip { grid-template-columns: 1fr; } }
@media (max-width: 60rem) { .clash-orbit--2 .clash-node { inset: auto; } }
@media (max-width: 38rem) { .clash-hero__content { padding-top: 1.4rem; }.clash-heading h1 { font-size: clamp(2.7rem, 13vw, 4.2rem); }.clash-countdown { margin-top: 1rem; }.clash-countdown > div { padding: .85rem .25rem; }.clash-countdown strong { font-size: clamp(2rem, 13vw, 3.2rem); }.clash-countdown span { font-size: .5rem; letter-spacing: .1em; }.clash-schedule { text-align: center; }.clash-orbit { grid-template-columns: 1fr 1fr; }.clash-node__beacon > span { width: 3.8rem; height: 3.8rem; }.clash-node__card { min-width: 0; padding-inline: .4rem; }.clash-node__card strong { max-width: 7.5rem; }.clash-cheer-rail { right: .75rem; left: .75rem; gap: .4rem; transform: none; }.clash-cheer-action { min-width: 0; flex: 1; padding-right: .5rem; }.relay-broadcast__selectors { align-items: stretch; flex-direction: column; }.relay-broadcast__selectors select { width: 100%; }.handoff-strip > div { grid-template-columns: minmax(0, 1fr) auto; }.handoff-strip > div > span { grid-column: 1 / -1; }.handoff-strip small { grid-column: 1; }.handoff-strip i { grid-column: 2; }.relay-roster { grid-template-columns: 1fr; } }
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
@media (prefers-reduced-motion: reduce) { .countdown-pulse-layer { display: none; } }
</style>
