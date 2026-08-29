<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { api } from "@/api/client";
import ChessBoard from "@/components/chess/ChessBoard.vue";
import type { BoardArrow, Color } from "@/components/chess/chess";
import ChatPanel from "@/components/public/ChatPanel.vue";
import FollowEnginePicker from "@/components/public/FollowEnginePicker.vue";
import SpectatorCount from "@/components/public/SpectatorCount.vue";
import StatusPill from "@/components/public/StatusPill.vue";
import type { ChatMessage } from "@/components/public/types";
import AppIcon from "@/components/ui/AppIcon.vue";
import { useViewerSettings } from "@/composables/useViewerSettings";
import type { EventDetailResponse } from "@/types/events";

import type { GauntletEngineInfo, GauntletEntry, GauntletPayload } from "./types";

interface ConfettiPiece {
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

interface ConfettiBurst {
  id: string;
  side: "left" | "right";
  x: number;
  y: number;
  pieces: ConfettiPiece[];
}

interface GauntletMoveEvent {
  game_id?: string | number;
  move?: {
    uci?: string;
    time_ms?: number;
  };
}

interface GauntletStreamSnapshot {
  engine_infos?: GauntletEngineInfo[];
}

const COPE_BLUE = "#2d63bf";
const KNOCKOUT_GRAY = "#7b8495";

const props = defineProps<{
  detail: EventDetailResponse;
  clockOffsetMs?: number;
  view?: "event" | "arena";
}>();

const { confettiEnabled } = useViewerSettings();
const router = useRouter();
const payload = computed(() => props.detail.custom as GauntletPayload);
const focusedId = ref<number | null>(null);
const now = ref(Date.now());
const streamState = ref<"connecting" | "live" | "offline">("connecting");
const infoByEngine = ref<Record<number, GauntletEngineInfo>>({});
const countdownFinished = ref(false);
const countdownBeat = ref(-1);
const soundState = ref<"armed" | "loading" | "playing" | "blocked" | "unavailable" | "finished">("loading");
const soundRequested = ref(false);
const soundPrimed = ref(false);
const confettiBursts = ref<ConfettiBurst[]>([]);
const rulesOpen = ref(false);
const fenCopied = ref(false);
const arenaElement = ref<HTMLElement | null>(null);
const boardColumnElement = ref<HTMLElement | null>(null);
let stream: EventSource | null = null;
const ticker = window.setInterval(() => { now.value = Date.now(); }, 100);
let countdownTimer: number | undefined;
let countdownFrame: number | undefined;
let countdownAudio: HTMLAudioElement | null = null;
let audioPlayPending = false;
let countdownAudioStarted = false;
let lastConfettiRequestAt = 0;
const seenConfettiIds = new Set<string>();
const confettiTimers = new Set<number>();
let fenCopyTimer: number | undefined;
let arenaFitFrame: number | undefined;
const countdownAudioUrl = "/audio/openbench-engine-clash-countdown.wav";
const countdownAudioLengthMs = 60_000;
const confettiClientId = typeof globalThis.crypto?.randomUUID === "function"
  ? globalThis.crypto.randomUUID()
  : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;

const isComplete = computed(() => props.detail.event.status === "completed" || props.detail.event.status === "cancelled" || payload.value.phase === "completed");
const isLive = computed(() => {
  const runtimeStatus = payload.value.tournament?.status ?? "";
  const scheduledRuntimeDue = runtimeStatus === "scheduled" && targetTime.value !== null && remainingMs.value === 0;
  return !isComplete.value && (
    ["live", "intermission"].includes(props.detail.event.status)
    || ["running", "paused"].includes(runtimeStatus)
    || payload.value.phase === "live"
    || scheduledRuntimeDue
  );
});
const activeEntries = computed(() => payload.value.entries.filter((entry) => entry.status === "active"));
const winners = computed(() => payload.value.entries.filter((entry) => entry.winner));
const showConfettiButtons = computed(() => (!isLive.value && !isComplete.value) || (isComplete.value && winners.value.length > 0));
const focused = computed(() => payload.value.entries.find((entry) => entry.id === focusedId.value) ?? null);
const focusedInfo = computed(() => focused.value ? infoByEngine.value[Number(focused.value.engine_id)] : undefined);
const boardOrientation = computed<Color>(() => payload.value.current_puzzle?.fen.split(/\s+/)[1] === "b" ? "black" : "white");
const puzzleNumber = computed(() => (payload.value.current_puzzle?.position ?? 0) + 1);
const totalPuzzleCount = computed(() => payload.value.puzzle_count ?? payload.value.puzzles.length);
const solvedPercent = computed(() => totalPuzzleCount.value ? Math.round(payload.value.rounds.length / totalPuzzleCount.value * 100) : 0);
const puzzlesLeft = computed(() => Math.max(0, totalPuzzleCount.value - payload.value.rounds.length));

const proposals = computed(() => payload.value.entries
  .filter((entry) => entry.status === "active" || entry.attempt?.game_status === "finished")
  .map((entry) => ({ entry, move: proposedMove(entry) }))
  .filter((item): item is { entry: GauntletEntry; move: string } => !!item.move));

const moveGroups = computed(() => {
  const groups = new Map<string, GauntletEntry[]>();
  for (const proposal of proposals.value) {
    const entries = groups.get(proposal.move) ?? [];
    entries.push(proposal.entry);
    groups.set(proposal.move, entries);
  }
  const sorted = [...groups.entries()].sort(([left], [right]) => left.localeCompare(right));
  const solutions = new Set((payload.value.current_puzzle?.solutions ?? []).map((move) => move.toLowerCase()));
  return sorted.map(([move, entries]) => {
    return {
      move,
      entries,
      count: entries.length,
      solution: solutions.has(move.toLowerCase()),
    };
  });
});
const rankedMoveGroups = computed(() => [...moveGroups.value].sort((left, right) => right.count - left.count || left.move.localeCompare(right.move)));
const mostPopularMove = computed(() => rankedMoveGroups.value[0] ?? null);
const focusOptions = computed(() => activeEntries.value.map((entry) => ({ id: String(entry.id), name: `${entry.name} ${entry.version}` })));
const followedEngineId = computed({
  get: () => focusedId.value === null ? "" : String(focusedId.value),
  set: (value: string) => { focusedId.value = value ? Number(value) : null; },
});
const followStateLabel = computed(() => focused.value ? "Focused" : "Overview");

const orderedMessages = computed(() => [...props.detail.chat_messages].sort((left, right) => {
  const leftTime = Date.parse(left.at ?? "");
  const rightTime = Date.parse(right.at ?? "");
  if (Number.isFinite(leftTime) && Number.isFinite(rightTime)) return leftTime - rightTime;
  return Number(left.id ?? 0) - Number(right.id ?? 0);
}));

const standings = computed(() => payload.value.entries
  .map((entry) => ({
    entry,
    completed: completedPuzzles(entry),
    eliminatedAt: eliminatedOn(entry),
  }))
  .sort((left, right) => {
    const leftActive = left.entry.status === "active" ? 1 : 0;
    const rightActive = right.entry.status === "active" ? 1 : 0;
    return rightActive - leftActive || right.completed - left.completed || left.entry.position - right.entry.position;
  }));

const boardArrows = computed<BoardArrow[]>(() => {
  const focusedMove = focused.value ? proposedMove(focused.value) : "";
  return moveGroups.value.map((group) => {
    const focusedGroup = focused.value !== null && group.entries.some((entry) => entry.id === focused.value?.id);
    const muted = focused.value !== null && !focusedGroup;
    return {
      move: group.move,
      color: group.move === focusedMove ? "white" : "black",
      fillColor: muted || !group.entries.some((entry) => entry.status === "active") ? KNOCKOUT_GRAY : COPE_BLUE,
      ...(group.solution && !muted ? { outlineColor: "#22c55e" } : {}),
      label: String(group.count),
    };
  });
});

const targetTime = computed(() => {
  const target = payload.value.settings.scheduled_start_at ?? props.detail.event.scheduled_start_at;
  const parsed = target ? Date.parse(target) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : null;
});
const remainingMs = computed(() => {
  const current = now.value + (props.clockOffsetMs ?? 0);
  return Math.max(0, (targetTime.value ?? current) - current);
});
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
  if (!targetTime.value) return "Start time to be announced";
  return `Counting down to ${new Intl.DateTimeFormat(undefined, { weekday: "long", hour: "numeric", minute: "2-digit" }).format(new Date(targetTime.value))} (your local time)`;
});

const searchRemainingMs = computed(() => {
  const puzzle = payload.value.current_puzzle;
  const startedAt = payload.value.entries
    .map((entry) => entry.attempt?.started_at ? Date.parse(entry.attempt.started_at) : Number.NaN)
    .filter(Number.isFinite)
    .sort((left, right) => left - right)[0];
  if (!puzzle || startedAt === undefined) return null;
  return Math.max(0, puzzle.time_limit_ms - ((now.value + (props.clockOffsetMs ?? 0)) - startedAt));
});

const searchPercent = computed(() => {
  const limit = payload.value.current_puzzle?.time_limit_ms ?? 0;
  if (!limit || searchRemainingMs.value === null) return 100;
  return Math.max(0, Math.min(100, searchRemainingMs.value / limit * 100));
});
const searchUrgent = computed(() => searchRemainingMs.value !== null && searchRemainingMs.value < 5000);
const isIntermission = computed(() => payload.value.phase === "intermission" && payload.value.transition !== null);

watch(
  () => payload.value.entries.map((entry) => `${entry.id}:${entry.status}`).join("|"),
  () => {
    if (focusedId.value !== null && !activeEntries.value.some((entry) => entry.id === focusedId.value)) focusedId.value = null;
  },
);

watch(
  () => payload.value.current_puzzle?.id,
  () => {
    infoByEngine.value = {};
    fenCopied.value = false;
    connectStream();
  },
);

watch(
  () => payload.value.tournament?.id,
  connectStream,
  { immediate: true },
);

watch([isLive, isComplete], () => {
  rulesOpen.value = false;
});

watch(targetTime, (value, previous) => {
  if (value !== previous) resetCountdownForTarget();
});

watch(isLive, scheduleArenaFit, { flush: "post" });

onMounted(() => {
  window.addEventListener("cope:event-cheer", handleConfettiEvent as EventListener);
  window.addEventListener("resize", scheduleArenaFit);
  window.visualViewport?.addEventListener("resize", scheduleArenaFit);
  scheduleArenaFit();
  if (props.view !== "event") return;
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
  window.removeEventListener("cope:event-cheer", handleConfettiEvent as EventListener);
  window.clearInterval(ticker);
  if (countdownTimer !== undefined) window.clearInterval(countdownTimer);
  if (countdownFrame !== undefined) window.cancelAnimationFrame(countdownFrame);
  document.removeEventListener("visibilitychange", handleCountdownResume);
  window.removeEventListener("focus", handleCountdownResume);
  window.removeEventListener("pageshow", handleCountdownResume);
  window.removeEventListener("pointerdown", handleUserActivation);
  window.removeEventListener("keydown", handleUserActivation);
  for (const timer of confettiTimers) window.clearTimeout(timer);
  confettiTimers.clear();
  if (fenCopyTimer !== undefined) window.clearTimeout(fenCopyTimer);
  if (arenaFitFrame !== undefined) window.cancelAnimationFrame(arenaFitFrame);
  window.removeEventListener("resize", scheduleArenaFit);
  window.visualViewport?.removeEventListener("resize", scheduleArenaFit);
  releaseCountdownAudio();
  stream?.close();
});

function scheduleArenaFit(): void {
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
  if (!arena || !boardColumn || window.matchMedia("(max-width: 96rem)").matches) return;
  const board = boardColumn.querySelector<HTMLElement>(".gauntlet-board");
  const progression = boardColumn.querySelector<HTMLElement>(".puzzle-progression");
  if (!board || !progression) return;

  const viewport = window.visualViewport;
  const viewportBottom = viewport ? viewport.offsetTop + viewport.height : window.innerHeight;
  const availableHeight = Math.floor(viewportBottom - arena.getBoundingClientRect().top - 8);
  if (availableHeight <= 0) return;

  const progressionStyle = getComputedStyle(progression);
  const progressionHeight = progression.getBoundingClientRect().height
    + Number.parseFloat(progressionStyle.marginTop || "0")
    + Number.parseFloat(progressionStyle.marginBottom || "0");
  const rootFontSize = Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  const boardSize = Math.max(0, Math.min(42 * rootFontSize, Math.floor(availableHeight - progressionHeight)));
  arena.style.setProperty("--arena-content-height", `${availableHeight}px`);
  arena.style.setProperty("--arena-board-size", `${boardSize}px`);
}

function connectStream(): void {
  stream?.close();
  const tournamentId = payload.value.tournament?.id;
  if (!tournamentId || typeof EventSource === "undefined") {
    streamState.value = "offline";
    return;
  }
  streamState.value = "connecting";
  stream = new EventSource(`/events/${encodeURIComponent(props.detail.event.slug)}/tournaments/${tournamentId}/stream?spectator=0`);
  stream.onopen = () => { streamState.value = "live"; };
  stream.onerror = () => { streamState.value = "offline"; };
  stream.addEventListener("tournament.snapshot", (raw) => {
    const data = streamData<GauntletStreamSnapshot>(raw);
    const snapshot: Record<number, GauntletEngineInfo> = {};
    for (const info of data.engine_infos ?? []) {
      const entry = payload.value.entries.find((item) => String(item.attempt?.game_id) === String(info.game_id));
      if (!entry || Number(entry.engine_id) !== Number(info.engine_id)) continue;
      snapshot[info.engine_id] = info;
    }
    infoByEngine.value = snapshot;
  });
  stream.addEventListener("engine.info", (raw) => {
    const data = streamData<GauntletEngineInfo>(raw);
    const entry = payload.value.entries.find((item) => String(item.attempt?.game_id) === String(data.game_id));
    if (!data.engine_id || !entry || Number(entry.engine_id) !== Number(data.engine_id)) return;
    infoByEngine.value = { ...infoByEngine.value, [data.engine_id]: data };
  });
  stream.addEventListener("game.move", (raw) => {
    const data = streamData<GauntletMoveEvent>(raw);
    const move = data.move?.uci?.toLowerCase() ?? "";
    if (data.game_id === undefined || !/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(move)) return;
    const entry = payload.value.entries.find((item) => String(item.attempt?.game_id) === String(data.game_id));
    if (!entry?.attempt) return;
    entry.attempt.move_uci = move;
    if (data.move?.time_ms !== undefined) entry.attempt.elapsed_ms = data.move.time_ms;
  });
  stream.addEventListener("spectators.changed", (raw) => {
    const count = streamData<{ spectator_count?: number }>(raw).spectator_count;
    if (count !== undefined) props.detail.spectator_count = count;
  });
}

function streamData<T>(raw: Event): T {
  try {
    const envelope = JSON.parse((raw as MessageEvent<string>).data) as { data?: T };
    return envelope.data ?? ({} as T);
  } catch {
    return {} as T;
  }
}

function proposedMove(entry: GauntletEntry | null): string {
  if (!entry) return "";
  const lockedMove = entry.attempt?.move_uci?.toLowerCase() ?? "";
  if (/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(lockedMove)) return lockedMove;
  const info = infoByEngine.value[Number(entry.engine_id)];
  if (!info || String(info.game_id) !== String(entry.attempt?.game_id)) return "";
  const liveMove = info?.engine_data.pv?.trim().split(/\s+/)[0]?.toLowerCase() ?? "";
  return /^[a-h][1-8][a-h][1-8][qrbn]?$/.test(liveMove) ? liveMove : "";
}

function timeLabel(value: number | null): string {
  if (value === null) return formatSeconds(payload.value.current_puzzle?.time_limit_ms ?? payload.value.settings.start_time_ms);
  return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)}s`;
}

function formatSeconds(value: number): string {
  return value >= 60000 ? `${Math.round(value / 6000) / 10}m` : `${Math.round(value / 100) / 10}s`;
}

function engineInitials(entry: GauntletEntry): string {
  return entry.name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
}

function eliminatedOn(entry: GauntletEntry): number | null {
  const round = payload.value.rounds.find((item) => item.eliminated_ids.includes(entry.id));
  return round ? round.position + 1 : null;
}

function completedPuzzles(entry: GauntletEntry): number {
  const eliminatedIndex = payload.value.rounds.findIndex((round) => round.eliminated_ids.includes(entry.id));
  return eliminatedIndex < 0 ? payload.value.rounds.length : eliminatedIndex;
}

function appendChatMessage(message: ChatMessage): void {
  if (message.id !== undefined && props.detail.chat_messages.some((item) => String(item.id) === String(message.id))) return;
  props.detail.chat_messages.push(message);
}

async function copyFen(): Promise<void> {
  const fen = payload.value.current_puzzle?.fen;
  if (!fen) return;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(fen);
    } else {
      const input = document.createElement("textarea");
      input.value = fen;
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.append(input);
      input.select();
      document.execCommand("copy");
      input.remove();
    }
    fenCopied.value = true;
    if (fenCopyTimer !== undefined) window.clearTimeout(fenCopyTimer);
    fenCopyTimer = window.setTimeout(() => { fenCopied.value = false; }, 1400);
  } catch {
    fenCopied.value = false;
  }
}

function countdownCompletionKey(): string {
  return `cope.event.${props.detail.event.id}.countdown-finished.${targetTime.value ?? "unscheduled"}`;
}

function authoritativeNow(): number {
  return Date.now() + (props.clockOffsetMs ?? 0);
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
    if (countdownAudio !== audio || countdownFinished.value || !finalMinuteActive.value) return;
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
  if (!audio || audioPlayPending || countdownAudioStarted || countdownFinished.value || !audio.paused) return;
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
  if (countdownFinished.value || targetTime.value === null) return;
  if (targetTime.value <= authoritativeNow()) {
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
  if (props.view === "event" && payload.value.tournament) {
    void router.replace({ name: "event-arena", params: { slug: props.detail.event.slug } });
  }
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

async function celebrate(side: "left" | "right"): Promise<void> {
  const requestedAt = Date.now();
  if (requestedAt - lastConfettiRequestAt < 350) return;
  lastConfettiRequestAt = requestedAt;
  try {
    await api.post(`/api/events/${encodeURIComponent(props.detail.event.slug)}/puzzle-gauntlet/cheers`, {
      body: { side },
      headers: { "X-Cope-Cheer-Client": confettiClientId },
    });
  } catch {
    return;
  }
}

function handleConfettiEvent(event: Event): void {
  const detail = (event as CustomEvent<{ id?: string; side?: string }>).detail;
  const confettiId = typeof detail?.id === "string" ? detail.id : "";
  if (!confettiId || (detail.side !== "left" && detail.side !== "right") || seenConfettiIds.has(confettiId)) return;
  seenConfettiIds.add(confettiId);
  if (seenConfettiIds.size > 128) seenConfettiIds.delete(seenConfettiIds.values().next().value!);
  launchConfetti(detail.side, confettiId);
}

function launchConfetti(side: "left" | "right", confettiId: string): void {
  if (!confettiEnabled.value || confettiBursts.value.length >= 6) return;
  const bounds = document.querySelector<HTMLElement>(`.tada-button--${side}`)?.getBoundingClientRect();
  const burst: ConfettiBurst = {
    id: confettiId,
    side,
    x: bounds ? ((bounds.left + bounds.width / 2) / window.innerWidth) * 100 : side === "left" ? 4 : 96,
    y: bounds ? ((bounds.top + bounds.height / 2) / window.innerHeight) * 100 : 94,
    pieces: Array.from({ length: 42 }, (_, index) => ({
      id: index,
      startX: -2.2 + Math.random() * 4.4,
      startY: -1.1 + Math.random() * 2.2,
      endX: side === "left" ? 1.5 + Math.random() * 14 : -15.5 + Math.random() * 14,
      endY: -5 - Math.random() * 12,
      rotation: -520 + Math.random() * 1_040,
      delay: Math.random() * 220,
      duration: 950 + Math.random() * 520,
      size: .22 + Math.random() * .24,
    })),
  };
  confettiBursts.value.push(burst);
  const timer = window.setTimeout(() => {
    confettiTimers.delete(timer);
    confettiBursts.value = confettiBursts.value.filter((item) => item.id !== burst.id);
  }, 1_850);
  confettiTimers.add(timer);
}
</script>

<template>
  <main class="gauntlet" :class="[`gauntlet--${isComplete ? 'complete' : isLive ? 'live' : 'countdown'}`, `gauntlet--${view || 'event'}`]">
    <div class="puzzle-pattern" aria-hidden="true"></div>
    <div v-if="confettiEnabled" class="confetti-layer" aria-hidden="true">
      <span v-for="burst in confettiBursts" :key="burst.id" class="confetti-burst" :class="`confetti-burst--${burst.side}`" :style="{ left: `${burst.x}%`, top: `${burst.y}%` }">
        <i v-for="piece in burst.pieces" :key="piece.id" :style="{ '--confetti-start-x': `${piece.startX}rem`, '--confetti-start-y': `${piece.startY}rem`, '--confetti-end-x': `${piece.endX}rem`, '--confetti-end-y': `${piece.endY}rem`, '--confetti-rotation': `${piece.rotation}deg`, '--confetti-delay': `${piece.delay}ms`, '--confetti-duration': `${piece.duration}ms`, '--confetti-size': `${piece.size}rem` }"></i>
      </span>
    </div>
    <span v-if="finalMinuteActive" :key="countdownBeat" class="countdown-pulse-layer" aria-hidden="true"></span>
    <button v-if="showConfettiButtons" type="button" class="tada-button tada-button--left" aria-label="Celebrate from the left" @click="celebrate('left')">🎉</button>
    <button v-if="showConfettiButtons" type="button" class="tada-button tada-button--right" aria-label="Celebrate from the right" @click="celebrate('right')">🎉</button>
    <button v-if="!isLive && !isComplete" type="button" class="rules-trigger" aria-controls="gauntlet-rules-panel" :aria-expanded="rulesOpen" aria-label="How Puzzle Gauntlet works" @click="rulesOpen = true" @keydown.esc="rulesOpen = false"><AppIcon name="info" :size="18" /></button>

    <Teleport to="body">
      <Transition name="rules-panel">
        <div v-if="rulesOpen" class="rules-overlay" @keydown.esc="rulesOpen = false">
          <button type="button" class="rules-backdrop" aria-label="Close event information" @click="rulesOpen = false"></button>
          <aside id="gauntlet-rules-panel" class="rules-panel" role="dialog" aria-modal="true" aria-labelledby="gauntlet-rules-title">
            <header>
              <div><span>Puzzle Gauntlet</span><h2 id="gauntlet-rules-title">How it works</h2></div>
              <button type="button" aria-label="Close event information" autofocus @click="rulesOpen = false"><AppIcon name="close" :size="19" /></button>
            </header>
            <div class="rules-panel__list">
              <article><span>01</span><div><strong>Same position</strong><p>Every engine faces every puzzle.</p></div></article>
              <article><span>02</span><div><strong>One life</strong><p>A wrong move means elimination.</p></div></article>
              <article><span>03</span><div><strong>Shrinking clock</strong><p>{{ formatSeconds(payload.settings.start_time_ms) }} down to {{ formatSeconds(payload.settings.minimum_time_ms) }}.</p></div></article>
            </div>
          </aside>
        </div>
      </Transition>
    </Teleport>

    <section v-if="!isLive && !isComplete" class="countdown-stage">
      <div class="stage-spectators"><span>Current spectators</span><SpectatorCount :count="detail.spectator_count ?? 0" /></div>
      <div class="countdown-copy">
        <h1><span>Puzzle</span> Gauntlet</h1>
      </div>
      <div v-if="countdownVisible" class="countdown-clock" aria-live="polite">
        <div v-for="part in countdown" :key="part.label">
          <strong>{{ String(part.value).padStart(2, "0") }}</strong>
          <span>{{ part.label }}</span>
        </div>
      </div>
      <p v-if="countdownVisible" class="countdown-schedule"><AppIcon name="clock" :size="18" /> {{ scheduleLabel }}</p>
      <button v-if="countdownVisible && !soundRequested && !soundPrimed && soundState !== 'playing' && soundState !== 'finished'" class="countdown-sound" type="button" @pointerdown.stop @click="enableCountdownSound(true)"><AppIcon name="radio" :size="16" />{{ soundState === "blocked" ? "Enable countdown sound" : "Arm countdown sound" }}</button>
      <section class="starting-field" aria-labelledby="starting-field-title">
        <header><div><span>Competitors</span><h2 id="starting-field-title">Participating engines</h2></div><strong>{{ payload.entries.length }}</strong></header>
        <ul><li v-for="entry in payload.entries" :key="entry.id" :class="{ 'starting-engine--knocked-out': entry.status !== 'active' }"><i>{{ engineInitials(entry) }}</i><span><strong :title="entry.name">{{ entry.name }}</strong><small :title="entry.version">{{ entry.version }}</small></span></li></ul>
      </section>
    </section>

    <section v-else-if="isComplete" class="finish-stage">
      <div class="stage-spectators"><span>Current spectators</span><SpectatorCount :count="detail.spectator_count ?? 0" /></div>
      <div class="finish-emblem"><AppIcon :name="winners.length ? 'trophy' : 'stop'" :size="42" /></div>
      <span class="kicker">{{ detail.event.status === "cancelled" ? "Run terminated" : "Gauntlet complete" }}</span>
      <h1 v-if="winners.length === 1"><span>{{ winners[0]?.name }}</span> survives</h1>
      <h1 v-else-if="winners.length">Shared <span>victory</span></h1>
      <h1 v-else>No winner <span>declared</span></h1>
      <p v-if="winners.length === 1">The last engine standing after {{ payload.rounds.length }} {{ payload.rounds.length === 1 ? "puzzle" : "puzzles" }}.</p>
      <p v-else-if="!winners.length">The event ended before an engine could claim the gauntlet.</p>
      <div v-if="winners.length" class="winner-lineup">
        <article v-for="winner in winners" :key="winner.id"><i>{{ engineInitials(winner) }}</i><strong>{{ winner.name }}</strong><span>{{ winner.version }}</span></article>
      </div>
      <div class="finish-stats"><div><strong>{{ payload.rounds.length }}</strong><span>Puzzles faced</span></div><div><strong>{{ payload.entries.length }}</strong><span>Engines entered</span></div><div><strong>{{ payload.rounds.filter((round) => round.void).length }}</strong><span>Everyone missed</span></div></div>
      <RouterLink class="back-home" to="/"><AppIcon name="arrow-left" :size="16" />Back to COPE</RouterLink>
    </section>

    <section v-else class="viewer-stage page-container tournament-page">
      <header class="tournament-heading">
        <div class="tournament-heading__title">
          <div class="title-line">
            <h1>{{ detail.event.title }}</h1>
            <StatusPill :status="payload.tournament?.status || detail.event.status" />
            <SpectatorCount :count="detail.spectator_count ?? 0" />
          </div>
          <p v-if="isIntermission">Puzzle {{ puzzleNumber }} complete / {{ activeEntries.length }} {{ activeEntries.length === 1 ? "engine remains" : "engines remain" }}</p>
          <p v-else>Puzzle {{ puzzleNumber }} of {{ totalPuzzleCount }} / {{ activeEntries.length }} {{ activeEntries.length === 1 ? "engine remains" : "engines remain" }}</p>
        </div>
        <div class="tournament-heading__controls">
          <FollowEnginePicker
            v-if="focusOptions.length && !isIntermission"
            v-model="followedEngineId"
            :engines="focusOptions"
            :state="focused ? 'live' : 'off'"
            :state-label="followStateLabel"
          />
        </div>
      </header>

      <div class="live-round-content">
      <section ref="arenaElement" class="arena" aria-label="Puzzle Gauntlet arena">
        <div class="engine-column">
          <article class="viewer-card puzzle-card" :class="{ 'puzzle-card--urgent': searchUrgent }">
            <header>
              <div><span>Current puzzle</span><strong>{{ payload.current_puzzle?.title || `Puzzle ${puzzleNumber}` }}</strong></div>
              <span class="side-to-move">{{ boardOrientation === "white" ? "White" : "Black" }} to move</span>
            </header>
            <div class="puzzle-clock">
              <span>{{ searchRemainingMs === null ? "Waiting for engines" : "Time remaining" }}</span>
              <time>{{ timeLabel(searchRemainingMs) }}</time>
              <i><span :style="{ width: `${searchPercent}%` }"></span></i>
            </div>
            <dl class="puzzle-details">
              <div>
                <dt>FEN</dt>
                <dd class="fen-value">
                  <code :title="payload.current_puzzle?.fen || undefined">{{ payload.current_puzzle?.fen || "Waiting for puzzle" }}</code>
                  <button type="button" :disabled="!payload.current_puzzle?.fen" :title="fenCopied ? 'Copied' : 'Copy FEN'" :aria-label="fenCopied ? 'FEN copied' : 'Copy FEN'" @click="copyFen">
                    <AppIcon :name="fenCopied ? 'check' : 'copy'" :size="14" />
                  </button>
                </dd>
              </div>
              <div><dt>Correct solution</dt><dd class="solution-value">{{ payload.current_puzzle?.solutions?.join(" / ") || "Pending" }}</dd></div>
            </dl>
          </article>

          <article class="viewer-card engine-card">
            <template v-if="focused">
              <header class="engine-card__heading">
                <span class="engine-avatar">{{ engineInitials(focused) }}</span>
                <div><span>Focused engine</span><strong>{{ focused.name }}</strong><small>{{ focused.version }}</small></div>
                <i></i>
              </header>
              <div class="engine-call"><span>Wants to play</span><strong>{{ proposedMove(focused) || "…" }}</strong></div>
              <dl class="focus-metrics">
                <div><dt>Eval</dt><dd>{{ focusedInfo?.engine_data.eval || "-" }}</dd></div>
                <div><dt>Depth</dt><dd>{{ focusedInfo?.engine_data.depth || "-" }}<small v-if="focusedInfo?.engine_data.seldepth">/{{ focusedInfo.engine_data.seldepth }}</small></dd></div>
                <div><dt>Nodes</dt><dd>{{ focusedInfo?.engine_data.nodes || "-" }}</dd></div>
                <div><dt>NPS</dt><dd>{{ focusedInfo?.engine_data.nps || "-" }}</dd></div>
              </dl>
              <div class="focus-pv"><span>Principal variation</span><p>{{ focusedInfo?.engine_data.pv || focused.attempt?.move_uci || "The calculation stream will appear when this engine begins searching." }}</p></div>
            </template>
            <template v-else>
              <header class="overview-heading"><div><span>Field overview</span><strong>Engine consensus</strong></div></header>
              <div class="overview-primary"><span>Most popular move</span><strong>{{ mostPopularMove?.move || "-" }}</strong><small>{{ mostPopularMove ? `${mostPopularMove.count} engine${mostPopularMove.count === 1 ? "" : "s"}` : "Waiting for candidate moves" }}</small></div>
              <div class="consensus-list">
                <span>Move split</span>
                <ol v-if="rankedMoveGroups.length">
                  <li v-for="group in rankedMoveGroups.slice(0, 6)" :key="group.move"><strong>{{ group.move }}</strong><i><span :style="{ width: `${group.count / Math.max(activeEntries.length, 1) * 100}%` }"></span></i><small>{{ group.count }}</small></li>
                </ol>
                <p v-else>No engine has committed to a line yet.</p>
              </div>
            </template>
          </article>
        </div>

        <div ref="boardColumnElement" class="board-column">
          <div class="gauntlet-board">
            <ChessBoard :fen="payload.current_puzzle?.fen ?? null" :orientation="boardOrientation" :controls="false" :arrows="boardArrows" label="Current Puzzle Gauntlet position" />
          </div>
          <div class="puzzle-progression">
            <div><span>Puzzle progression</span><strong>{{ payload.rounds.length }} completed</strong></div>
            <div class="progress-track"><span :style="{ width: `${solvedPercent}%` }"></span></div>
            <div class="progress-counts"><span>{{ puzzleNumber }} / {{ totalPuzzleCount }}</span><strong>{{ puzzlesLeft }} left</strong></div>
          </div>
        </div>

        <aside class="activity-column" aria-label="Gauntlet activity">
          <section class="puzzle-log" aria-labelledby="puzzle-log-title">
            <header><h2 id="puzzle-log-title">Puzzle log</h2><span>{{ payload.rounds.length }}</span></header>
            <ol v-if="payload.rounds.length">
              <li v-for="round in payload.rounds" :key="round.puzzle_id">
                <div><span>{{ String(round.position + 1).padStart(2, "0") }}</span><strong>{{ round.title || `Puzzle ${round.position + 1}` }}</strong><small>{{ round.void ? "Field saved" : `${round.correct_ids.length} correct` }}</small></div>
                <code>{{ round.fen }}</code>
                <p><span>Solution</span><strong>{{ round.solutions.join(" / ") }}</strong></p>
                <small v-if="round.eliminated_ids.length">{{ round.eliminated_ids.length }} {{ round.eliminated_ids.length === 1 ? "engine" : "engines" }} knocked out</small>
              </li>
            </ol>
            <p v-else class="puzzle-log__empty">Completed puzzles will appear here.</p>
          </section>
          <ChatPanel
            class="arena-chat"
            :messages="orderedMessages"
            :settings="detail.chat_settings"
            :event-slug="detail.event.slug"
            @sent="appendChatMessage"
          />
        </aside>
      </section>

      <section class="tournament-data">
        <nav class="data-tabs" aria-label="Gauntlet information"><span aria-current="page">Standings <small>{{ payload.entries.length }}</small></span></nav>
        <section class="data-panel" aria-labelledby="gauntlet-standings-title">
          <header><div><h2 id="gauntlet-standings-title">Standings</h2><p>Remaining engines lead; eliminated engines are ordered by puzzles completed.</p></div></header>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Rank</th><th>Engine</th><th>Status</th><th>Puzzles completed</th><th>Exit puzzle</th><th>Current move</th></tr></thead>
              <tbody>
                <tr v-for="(standing, index) in standings" :key="standing.entry.id" :class="{ 'standing--eliminated': standing.entry.status !== 'active' }">
                  <td class="rank-cell">{{ index + 1 }}</td>
                  <td><span class="standing-engine"><i>{{ engineInitials(standing.entry) }}</i><span><strong>{{ standing.entry.name }}</strong><small>{{ standing.entry.version }}</small></span></span></td>
                  <td><span class="survival-status" :data-status="standing.entry.status">{{ standing.entry.status === "active" ? "Remaining" : "Knocked out" }}</span></td>
                  <td class="number-cell">{{ standing.completed }}</td>
                  <td>{{ standing.eliminatedAt ? `Puzzle ${standing.eliminatedAt}` : "-" }}</td>
                  <td><code>{{ standing.entry.status === "active" ? proposedMove(standing.entry) || "thinking" : "-" }}</code></td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </section>
      </div>
    </section>

  </main>
</template>

<style scoped>
.gauntlet { --ink: #f8f7ff; --muted: #a9a5bd; --gauntlet-purple: #a78bfa; --violet: var(--gauntlet-purple); --cyan: #22d3ee; --engine-alive: #2d63bf; --engine-knocked-out: #7b8495; position: relative; min-height: calc(100dvh - var(--header-height, 0px)); overflow: hidden; background: #090713; color: var(--ink); font-family: Inter, ui-sans-serif, system-ui, sans-serif; isolation: isolate; }
.countdown-stage, .finish-stage { position: relative; display: grid; width: min(76rem, calc(100% - 2rem)); min-height: calc(100dvh - 4.4rem); margin: auto; place-content: center; justify-items: center; padding: clamp(3rem, 8vw, 7rem) 0; text-align: center; }.stage-spectators { display: inline-flex; align-items: center; gap: .55rem; margin-bottom: 1.1rem; padding: .42rem .65rem; border: 1px solid rgb(167 139 250 / 24%); border-radius: 999px; background: rgb(20 16 36 / 78%); box-shadow: inset 0 1px rgb(255 255 255 / 5%); }.stage-spectators > span { color: #837d93; font-size: .54rem; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }.stage-spectators :deep(.spectator-count) { color: #d9ccff; font-size: .68rem; }.finish-stage .kicker { color: var(--cyan); font-size: .66rem; font-weight: 850; letter-spacing: .24em; text-transform: uppercase; }.countdown-copy h1, .finish-stage h1 { margin: .55rem 0 0; font-size: clamp(3.7rem, 10vw, 8.6rem); font-weight: 900; letter-spacing: -.075em; line-height: .85; text-transform: uppercase; }.countdown-copy h1 span, .finish-stage h1 span { color: transparent; -webkit-text-stroke: 1px #c5b5ff; text-shadow: 0 0 2.5rem rgb(139 92 246 / 26%); }.countdown-clock { display: flex; align-items: center; gap: clamp(.6rem, 2vw, 1.5rem); margin-top: clamp(2.2rem, 5vw, 4rem); padding: 1rem 1.4rem; border: 1px solid rgb(167 139 250 / 24%); border-radius: 1.2rem; background: linear-gradient(135deg, rgb(29 22 50 / 88%), rgb(12 10 24 / 90%)); box-shadow: 0 1.3rem 4rem rgb(0 0 0 / 32%), inset 0 1px rgb(255 255 255 / 7%); }.countdown-clock div { display: grid; min-width: clamp(4.4rem, 9vw, 7rem); }.countdown-clock strong { font-variant-numeric: tabular-nums; font-size: clamp(2.3rem, 6vw, 4.8rem); font-weight: 770; letter-spacing: -.06em; line-height: 1; }.countdown-clock div span { margin-top: .4rem; color: #837d93; font-size: .54rem; font-weight: 800; letter-spacing: .15em; text-transform: uppercase; }.countdown-clock b { color: #686178; font-size: 2rem; font-weight: 400; transform: translateY(-.45rem); }.countdown-clock .ready-word { padding: .4rem 2rem; color: #d9ccff; font-size: clamp(1.6rem, 5vw, 3.6rem); text-transform: uppercase; }.countdown-rules { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; width: min(54rem, 100%); margin-top: clamp(2.2rem, 5vw, 4rem); overflow: hidden; border: 1px solid rgb(255 255 255 / 9%); border-radius: .85rem; background: rgb(255 255 255 / 9%); text-align: left; }.countdown-rules article { display: flex; gap: .75rem; padding: .9rem 1rem; background: rgb(9 7 19 / 82%); }.countdown-rules article > span { color: var(--violet); font-size: .6rem; font-weight: 900; }.countdown-rules strong { font-size: .7rem; }.countdown-rules p { margin: .22rem 0 0; color: #8f899f; font-size: .6rem; line-height: 1.4; }.starting-field { width: min(68rem, 100%); margin-top: 2.2rem; text-align: left; }.starting-field > header { display: flex; align-items: end; justify-content: space-between; gap: 1rem; margin-bottom: .65rem; padding-inline: .15rem; }.starting-field > header div { display: grid; gap: .12rem; }.starting-field > header span { color: var(--violet); font-size: .53rem; font-weight: 850; letter-spacing: .12em; text-transform: uppercase; }.starting-field > header h2 { margin: 0; font-size: .9rem; }.starting-field > header > strong { color: #d9ccff; font-size: 1.15rem; font-variant-numeric: tabular-nums; }.starting-field ul { display: grid; grid-template-columns: repeat(auto-fill, minmax(8rem, 1fr)); gap: .35rem; margin: 0; padding: 0; list-style: none; }.starting-field li { --engine: var(--engine-alive); display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: .45rem; min-width: 0; padding: .38rem .45rem; border: 1px solid rgb(255 255 255 / 8%); border-radius: .45rem; background: rgb(20 16 36 / 72%); }.starting-field li > i { display: grid; width: 1.45rem; aspect-ratio: 1; place-items: center; border-radius: .32rem; background: color-mix(in srgb, var(--engine) 20%, #171225); color: var(--engine); font-size: .46rem; font-style: normal; font-weight: 900; }.starting-field li > span { display: grid; min-width: 0; }.starting-field li strong, .starting-field li small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.starting-field li strong { font-size: .58rem; }.starting-field li small { color: #7f798e; font-size: .47rem; }.starting-field li.starting-engine--knocked-out { --engine: var(--engine-knocked-out); opacity: .62; }
.finish-stage { max-width: 68rem; }.finish-emblem { display: grid; width: 5.5rem; aspect-ratio: 1; margin-bottom: 1.6rem; place-items: center; border: 1px solid rgb(167 139 250 / 42%); border-radius: 50%; background: radial-gradient(circle at 50% 38%, rgb(167 139 250 / 28%), rgb(34 211 238 / 9%) 68%, rgb(9 7 19 / 82%)); color: #e9dfff; box-shadow: inset 0 0 0 .45rem rgb(9 7 19 / 42%), 0 0 3.5rem rgb(139 92 246 / 30%); }.finish-stage h1 { max-width: 65rem; font-size: clamp(3rem, 9vw, 7.4rem); }.finish-stage > p { max-width: 38rem; margin: 1.25rem 0 0; color: #aaa4b8; font-size: clamp(.82rem, 1.6vw, 1.05rem); line-height: 1.6; }.winner-lineup { display: flex; flex-wrap: wrap; justify-content: center; gap: .7rem; margin-top: 2rem; }.winner-lineup article { display: grid; grid-template-columns: auto auto; align-items: center; gap: .05rem .6rem; min-width: 12rem; padding: .65rem .8rem; border: 1px solid color-mix(in srgb, var(--engine) 32%, transparent); border-radius: .7rem; background: color-mix(in srgb, var(--engine) 8%, #100c1e); text-align: left; }.winner-lineup i { display: grid; grid-row: 1 / 3; width: 2.2rem; aspect-ratio: 1; place-items: center; border-radius: .5rem; background: var(--engine); color: #090713; font-size: .62rem; font-style: normal; font-weight: 950; }.winner-lineup strong { font-size: .72rem; }.winner-lineup span { color: #898297; font-size: .56rem; }.finish-stats { display: flex; gap: 1px; margin-top: 2rem; overflow: hidden; border: 1px solid rgb(255 255 255 / 9%); border-radius: .75rem; background: rgb(255 255 255 / 9%); }.finish-stats div { display: grid; min-width: 8rem; padding: .7rem 1rem; background: #0f0b1c; }.finish-stats strong { font-size: 1.2rem; }.finish-stats span { color: #7f798e; font-size: .52rem; font-weight: 750; letter-spacing: .09em; text-transform: uppercase; }.back-home { display: inline-flex; align-items: center; gap: .45rem; margin-top: 2rem; color: #aaa4b8; font-size: .66rem; font-weight: 700; text-decoration: none; }
.live-stage { min-height: calc(100dvh - 4.4rem); }.round-strip { display: grid; grid-template-columns: auto minmax(8rem, 1fr) auto; align-items: center; gap: 1.5rem; padding: .7rem clamp(1rem, 2.5vw, 2.5rem); border-bottom: 1px solid rgb(255 255 255 / 8%); background: rgb(13 10 24 / 72%); }.round-strip > div:first-child { display: flex; align-items: baseline; gap: .35rem; }.round-strip span, .survival-rail header span, .calculation-panel header small, .principal-move > span, .pv-line > span, .puzzle-heading span, .consensus-bar > span { color: #878093; font-size: .54rem; font-weight: 830; letter-spacing: .12em; text-transform: uppercase; }.round-strip > div:first-child strong { color: var(--violet); font-size: 1.3rem; }.round-strip > div:first-child small { color: #666071; font-size: .58rem; }.round-progress { height: .22rem; overflow: hidden; border-radius: 999px; background: #242031; }.round-progress span { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--violet), var(--cyan)); box-shadow: 0 0 .8rem var(--violet); }.survivor-count { display: flex; align-items: baseline; gap: .4rem; }.survivor-count strong { color: var(--cyan); font-size: 1.2rem; }.survivor-count span { color: #8c8698; font-size: .58rem; text-transform: uppercase; }.arena-grid { display: grid; grid-template-columns: minmax(13rem, .58fr) minmax(28rem, 1.45fr) minmax(17rem, .72fr); min-height: calc(100dvh - 8.8rem); }.survival-rail, .calculation-panel { min-width: 0; background: rgb(10 8 19 / 84%); }.survival-rail { display: grid; grid-template-rows: auto 1fr auto; border-right: 1px solid rgb(255 255 255 / 8%); }.survival-rail > header { display: flex; align-items: center; justify-content: space-between; padding: 1rem; border-bottom: 1px solid rgb(255 255 255 / 8%); }.survival-rail header div { display: grid; gap: .16rem; }.survival-rail header strong { font-size: .75rem; }.survival-rail header > small { color: var(--cyan); font-size: .65rem; font-weight: 800; }.engine-stack { align-content: start; display: grid; overflow: auto; }.engine-stack button { position: relative; display: grid; grid-template-columns: 1.5rem 2rem minmax(0, 1fr) auto; align-items: center; gap: .55rem; min-width: 0; padding: .62rem .8rem; border: 0; border-bottom: 1px solid rgb(255 255 255 / 6%); background: transparent; color: var(--ink); cursor: pointer; text-align: left; }.engine-stack button::before { position: absolute; inset: 0 auto 0 0; width: 2px; background: var(--engine, transparent); content: ""; opacity: 0; }.engine-stack button:hover, .engine-stack button.focused { background: rgb(255 255 255 / 4%); }.engine-stack button.focused::before { opacity: 1; }.engine-stack .seed { color: #686273; font-family: ui-monospace, monospace; font-size: .55rem; }.engine-stack button > i { display: grid; width: 2rem; aspect-ratio: 1; place-items: center; border-radius: .45rem; background: color-mix(in srgb, var(--engine) 14%, #171320); color: var(--engine); font-size: .54rem; font-style: normal; font-weight: 900; }.engine-stack .engine-name { display: grid; min-width: 0; }.engine-name strong, .engine-name small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.engine-name strong { font-size: .66rem; }.engine-name small { margin-top: .1rem; color: #726c7d; font-size: .52rem; }.move-call { color: var(--engine); font-family: ui-monospace, monospace; font-size: .56rem; }.engine-stack button.eliminated { color: #696473; filter: grayscale(1); opacity: .58; }.rail-legend { display: flex; gap: .8rem; padding: .7rem .9rem; border-top: 1px solid rgb(255 255 255 / 8%); color: #6f6979; font-size: .5rem; }.rail-legend span { display: flex; align-items: center; gap: .3rem; }.rail-legend i { width: .35rem; height: .35rem; border-radius: 50%; }.live-dot { background: #2dd4bf; }.out-dot { background: #575260; }
.puzzle-board-column { display: grid; align-content: center; justify-items: center; min-width: 0; padding: clamp(1rem, 2vw, 2rem); background: radial-gradient(circle at 50% 48%, rgb(139 92 246 / 13%), transparent 44%); }.puzzle-heading { display: flex; align-items: end; justify-content: space-between; width: min(70vh, 100%); margin-bottom: .65rem; }.puzzle-heading > div:first-child { display: grid; gap: .18rem; }.puzzle-heading strong { font-size: .76rem; }.search-clock { display: grid; grid-template-columns: auto auto; align-items: baseline; gap: .15rem .55rem; text-align: right; }.search-clock small { color: #777180; font-size: .5rem; text-transform: uppercase; }.search-clock strong { color: #e6ddff; font-family: ui-monospace, monospace; font-size: 1.1rem; font-variant-numeric: tabular-nums; }.search-clock > i { grid-column: 1 / -1; width: 7rem; height: .2rem; overflow: hidden; border-radius: 999px; background: #272230; }.search-clock > i span { display: block; height: 100%; background: linear-gradient(90deg, var(--violet), var(--cyan)); transition: width .1s linear; }.search-clock.urgent strong { color: #fb7185; }.search-clock.urgent > i span { background: #fb7185; }.board-frame { position: relative; width: min(70vh, 100%); padding: .55rem; border: 1px solid rgb(167 139 250 / 20%); background: #110d20; box-shadow: 0 1.8rem 5rem rgb(0 0 0 / 36%), 0 0 3rem rgb(139 92 246 / 10%); }.board-shell { position: relative; width: 100%; }.board-shell :deep(.chess-viewer), .board-shell :deep(.board-mount) { width: 100%; }.corner { position: absolute; z-index: 3; width: 1rem; height: 1rem; border-color: var(--cyan); opacity: .7; }.corner--tl { top: -.25rem; left: -.25rem; border-top: 1px solid; border-left: 1px solid; }.corner--tr { top: -.25rem; right: -.25rem; border-top: 1px solid; border-right: 1px solid; }.corner--bl { bottom: -.25rem; left: -.25rem; border-bottom: 1px solid; border-left: 1px solid; }.corner--br { right: -.25rem; bottom: -.25rem; border-right: 1px solid; border-bottom: 1px solid; }.move-count { position: absolute; z-index: 8; display: grid; width: clamp(1.15rem, 3.4%, 1.75rem); aspect-ratio: 1; place-items: center; border: 1px solid rgb(255 255 255 / 30%); border-radius: 50%; background: #444451; color: white; font-size: clamp(.48rem, 1vw, .68rem); font-weight: 900; box-shadow: 0 .15rem .5rem rgb(0 0 0 / 45%); transform: translate(-50%, -50%); }.move-count.focused { background: var(--violet); color: #100b1d; box-shadow: 0 0 1rem rgb(167 139 250 / 70%); }.consensus-bar { display: grid; grid-template-columns: auto minmax(5rem, 1fr) auto; align-items: center; gap: .7rem; width: min(70vh, 100%); margin-top: .7rem; }.consensus-bar > div { display: flex; height: .25rem; overflow: hidden; gap: 2px; border-radius: 999px; background: #211d2b; }.consensus-bar > div i { height: 100%; background: #777181; }.consensus-bar > div i:first-child { background: var(--violet); }.consensus-bar strong { color: #7e778b; font-size: .52rem; font-weight: 700; }
.calculation-panel { display: grid; grid-template-rows: auto auto auto auto 1fr auto; border-left: 1px solid rgb(255 255 255 / 8%); }.calculation-panel > header { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: .7rem; padding: .9rem 1rem; border-bottom: 1px solid rgb(255 255 255 / 8%); }.focus-avatar { display: grid; width: 2.6rem; aspect-ratio: 1; place-items: center; border-radius: .55rem; background: color-mix(in srgb, var(--engine) 18%, #181325); color: var(--engine); font-size: .65rem; font-weight: 900; }.calculation-panel header > div { display: grid; min-width: 0; gap: .05rem; }.calculation-panel header strong, .calculation-panel header span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.calculation-panel header strong { font-size: .72rem; }.calculation-panel header div span { color: #736d7d; font-size: .54rem; }.focus-live { width: .4rem; aspect-ratio: 1; border-radius: 50%; background: var(--engine); box-shadow: 0 0 .7rem var(--engine); }.principal-move { display: grid; padding: 1rem; border-bottom: 1px solid rgb(255 255 255 / 8%); }.principal-move strong { margin-top: .2rem; color: var(--engine); font-family: ui-monospace, monospace; font-size: clamp(1.7rem, 3vw, 2.4rem); letter-spacing: -.06em; }.principal-move small { margin-top: .2rem; overflow: hidden; color: #8a8394; font-size: .56rem; text-overflow: ellipsis; white-space: nowrap; }.engine-metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; margin: 0; border-bottom: 1px solid rgb(255 255 255 / 8%); background: rgb(255 255 255 / 7%); }.engine-metrics div { display: grid; gap: .16rem; padding: .65rem; background: #0d0a18; }.engine-metrics dt { color: #676170; font-size: .48rem; font-weight: 750; text-transform: uppercase; }.engine-metrics dd { margin: 0; color: #d9d4e3; font-family: ui-monospace, monospace; font-size: .64rem; }.engine-metrics dd small { color: #746d7d; }.engine-metrics .status-value { color: var(--engine); font-family: inherit; text-transform: capitalize; }.pv-line { min-width: 0; padding: .85rem 1rem; border-bottom: 1px solid rgb(255 255 255 / 8%); }.pv-line p { margin: .4rem 0 0; overflow: hidden; color: #b2acbd; font-family: ui-monospace, monospace; font-size: .58rem; line-height: 1.6; text-overflow: ellipsis; white-space: nowrap; }.uci-console { display: grid; grid-template-rows: auto 1fr; min-height: 8rem; overflow: hidden; background: #07050d; }.uci-console header { display: flex; align-items: center; justify-content: space-between; padding: .55rem .75rem; border-bottom: 1px solid rgb(255 255 255 / 6%); }.uci-console header span { color: #736d7c; font-size: .5rem; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }.uci-console header i { width: .35rem; aspect-ratio: 1; border-radius: 50%; background: var(--engine); }.uci-console pre { margin: 0; overflow: auto; padding: .75rem; color: #8ee3d4; font-family: ui-monospace, monospace; font-size: .52rem; line-height: 1.65; white-space: pre-wrap; word-break: break-word; }.calculation-panel > footer { display: flex; align-items: center; justify-content: space-between; padding: .65rem .8rem; border-top: 1px solid rgb(255 255 255 / 8%); color: #615b69; font-size: .5rem; }
.puzzle-pattern { position: absolute; z-index: -1; inset: 0; background-color: #090713; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 80 80'%3E%3Cpath d='M40 0v14c0 3-2 5-5 5h-2c-4 0-7 3-7 7s3 7 7 7h2c3 0 5 2 5 5v16c0 3 2 5 5 5h2c4 0 7 3 7 7s-3 7-7 7h-2c-3 0-5 2-5 5v2M0 40h14c3 0 5 2 5 5v2c0 4 3 7 7 7s7-3 7-7v-2c0-3 2-5 5-5h16c3 0 5-2 5-5v-2c0-4 3-7 7-7s7 3 7 7v2c0 3 2 5 5 5h2' fill='none' stroke='%2333274d' stroke-width='1'/%3E%3C/svg%3E"); background-position: center; background-size: 5rem 5rem; }
.rules-trigger { position: absolute; z-index: 5; top: 1rem; left: 1rem; display: grid; width: 2.5rem; height: 2.5rem; place-items: center; padding: 0; border: 1px solid rgb(167 139 250 / 32%); border-radius: 50%; background: rgb(14 10 27 / 88%); box-shadow: 0 .6rem 1.5rem rgb(0 0 0 / 24%); color: #c8b8ff; cursor: pointer; transition: border-color 150ms ease, background 150ms ease, transform 150ms ease; }
.rules-trigger:hover { border-color: var(--violet); background: #211833; transform: translateY(-1px); }
.rules-trigger:focus-visible { outline: 2px solid var(--cyan); outline-offset: 3px; }
.rules-overlay { position: fixed; z-index: 70; inset: 0; }
.rules-backdrop { position: absolute; inset: 0; width: 100%; height: 100%; padding: 0; border: 0; background: rgb(4 3 10 / 70%); cursor: default; backdrop-filter: blur(4px); }
.rules-panel { --violet: #a78bfa; --cyan: #22d3ee; position: absolute; top: 0; bottom: 0; left: 0; display: grid; width: min(23rem, calc(100vw - 1rem)); align-content: start; overflow-y: auto; border-right: 1px solid rgb(167 139 250 / 24%); background-color: #0b0814; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 80 80'%3E%3Cpath d='M40 0v14c0 3-2 5-5 5h-2c-4 0-7 3-7 7s3 7 7 7h2c3 0 5 2 5 5v16c0 3 2 5 5 5h2c4 0 7 3 7 7s-3 7-7 7h-2c-3 0-5 2-5 5v2M0 40h14c3 0 5 2 5 5v2c0 4 3 7 7 7s7-3 7-7v-2c0-3 2-5 5-5h16c3 0 5-2 5-5v-2c0-4 3-7 7-7s7 3 7 7v2c0 3 2 5 5 5h2' fill='none' stroke='%23221937' stroke-width='1'/%3E%3C/svg%3E"); background-size: 5rem 5rem; box-shadow: 1.2rem 0 3rem rgb(0 0 0 / 46%); color: #f8f7ff; }
.rules-panel > header { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: 1.25rem; border-bottom: 1px solid rgb(255 255 255 / 9%); background: rgb(11 8 20 / 82%); backdrop-filter: blur(12px); }
.rules-panel > header div { display: grid; gap: .2rem; }
.rules-panel > header span { color: var(--cyan); font-size: .57rem; font-weight: 850; letter-spacing: .16em; text-transform: uppercase; }
.rules-panel > header h2 { margin: 0; font-size: 1.35rem; letter-spacing: -.035em; }
.rules-panel > header button { display: grid; width: 2.25rem; height: 2.25rem; place-items: center; padding: 0; border: 1px solid rgb(255 255 255 / 12%); border-radius: 50%; background: #171122; color: #b8b1c5; cursor: pointer; }
.rules-panel > header button:hover { border-color: var(--violet); color: white; }
.rules-panel__list { display: grid; gap: 1px; margin: 1rem; overflow: hidden; border: 1px solid rgb(255 255 255 / 9%); border-radius: .85rem; background: rgb(255 255 255 / 9%); }
.rules-panel__list article { display: grid; grid-template-columns: 2rem minmax(0, 1fr); gap: .75rem; padding: 1.15rem; background: rgb(9 7 19 / 92%); }
.rules-panel__list article > span { color: var(--violet); font-size: .65rem; font-weight: 900; }
.rules-panel__list strong { font-size: .82rem; }
.rules-panel__list p { margin: .3rem 0 0; color: #918a9e; font-size: .7rem; line-height: 1.5; }
.rules-panel-enter-active, .rules-panel-leave-active { transition: opacity 180ms ease; }
.rules-panel-enter-active .rules-panel, .rules-panel-leave-active .rules-panel { transition: transform 220ms cubic-bezier(.2, .8, .2, 1); }
.rules-panel-enter-from, .rules-panel-leave-to { opacity: 0; }
.rules-panel-enter-from .rules-panel, .rules-panel-leave-to .rules-panel { transform: translateX(-100%); }
.countdown-clock { justify-content: center; width: min(100%, 50rem); margin-top: 1.1rem; padding: 0; border: 0; border-radius: 0; background: transparent; box-shadow: none; }
.countdown-clock div { position: relative; width: 25%; min-width: 0; justify-items: center; gap: .35rem; padding: clamp(.8rem, 2.6vh, 1.45rem) .75rem 1.2rem; }
.countdown-clock strong { color: #f8f7ff; font-size: clamp(3rem, 5.7vw, 5.2rem); font-weight: 760; letter-spacing: -.065em; }
.countdown-clock div span { margin-top: 0; color: var(--violet); font-size: .69rem; font-weight: 830; letter-spacing: .17em; }
.countdown-schedule { display: flex; align-items: center; gap: .45rem; margin: .75rem 0 0; color: #9891a8; font-size: .78rem; }
.countdown-sound { display: inline-flex; align-items: center; gap: .4rem; min-height: 2.15rem; margin-top: .65rem; padding: 0 .8rem; border: 1px solid rgb(167 139 250 / 34%); border-radius: 999px; background: rgb(26 20 44 / 82%); color: #c8b8ff; cursor: pointer; font: inherit; font-size: .66rem; font-weight: 760; }
.countdown-sound:hover { border-color: var(--violet); background: #211833; }
.countdown-pulse-layer { position: fixed; z-index: 48; inset: 0; display: block; background: radial-gradient(circle at 50% 45%, rgb(167 139 250 / 19%), rgb(139 92 246 / 8%) 52%, transparent 78%); box-shadow: inset 0 0 0 .45rem rgb(167 139 250 / 13%), inset 0 0 9rem rgb(139 92 246 / 18%); pointer-events: none; animation: countdown-page-pulse .58s cubic-bezier(.12, .72, .28, 1) both; }
.winner-lineup article { --engine: var(--engine-alive); }
.confetti-layer { position: fixed; z-index: 46; inset: 0; overflow: hidden; pointer-events: none; }
.confetti-burst { --confetti-color: var(--gauntlet-purple); position: absolute; width: 1px; height: 1px; }
.confetti-burst i { position: absolute; width: var(--confetti-size); height: calc(var(--confetti-size) * 1.6); border-radius: .08rem; background: var(--confetti-color); opacity: 0; transform: translate(var(--confetti-start-x), var(--confetti-start-y)); animation: confetti-pop var(--confetti-duration) cubic-bezier(.16, .72, .28, 1) forwards; animation-delay: var(--confetti-delay); }
.confetti-burst i:nth-child(even) { height: var(--confetti-size); border-radius: 50%; filter: brightness(1.22); }
.tada-button { position: fixed; z-index: 47; bottom: 1.5rem; display: grid; width: 4rem; height: 4rem; place-items: center; padding: 0; border: 1px solid color-mix(in srgb, var(--gauntlet-purple) 58%, #b9a678); border-radius: 50%; background: color-mix(in srgb, var(--gauntlet-purple) 20%, #14100a); box-shadow: inset 0 1px rgb(255 255 255 / 16%), 0 .8rem 1.8rem color-mix(in srgb, var(--gauntlet-purple) 22%, transparent); color: white; cursor: pointer; font: inherit; font-size: 1.55rem; transition: transform 150ms ease, background 150ms ease; }
.tada-button:hover { background: color-mix(in srgb, var(--gauntlet-purple) 32%, #171108); transform: translateY(-.2rem) scale(1.04); }
.tada-button:active { transform: scale(.94); }
.tada-button--left { left: 1.5rem; }
.tada-button--right { right: 1.5rem; transform: scaleX(-1); }
.tada-button--right:hover { transform: translateY(-.2rem) scaleX(-1) scale(1.04); }
.tada-button--right:active { transform: scaleX(-1) scale(.94); }
@keyframes countdown-page-pulse { 0% { opacity: 1; } 42% { opacity: .32; } 100% { opacity: 0; } }
@keyframes confetti-pop { 0% { opacity: 0; transform: translate(var(--confetti-start-x), var(--confetti-start-y)) rotate(0) scale(.5); } 13% { opacity: .95; } 100% { opacity: 0; transform: translate(var(--confetti-end-x), var(--confetti-end-y)) rotate(var(--confetti-rotation)) scale(1); } }
@media (max-width: 70rem) { .arena-grid { grid-template-columns: 12rem minmax(25rem, 1fr); }.calculation-panel { grid-column: 1 / -1; grid-template-columns: 1fr 1fr; grid-template-rows: auto auto auto; border-top: 1px solid rgb(255 255 255 / 8%); border-left: 0; }.calculation-panel > header, .principal-move { border-right: 1px solid rgb(255 255 255 / 8%); }.engine-metrics { grid-column: 1 / -1; }.pv-line { grid-column: 1; }.uci-console { grid-column: 2; grid-row: 3 / 5; }.calculation-panel > footer { display: none; } }
@media (max-width: 48rem) { .countdown-rules { grid-template-columns: 1fr; }.countdown-stage, .finish-stage { min-height: calc(100dvh - 4rem); }.arena-grid { grid-template-columns: 1fr; }.survival-rail { grid-row: 2; border-top: 1px solid rgb(255 255 255 / 8%); border-right: 0; }.engine-stack { grid-template-columns: repeat(2, 1fr); }.puzzle-board-column { grid-row: 1; }.calculation-panel { grid-row: 3; }.round-strip { gap: .65rem; }.round-progress { min-width: 4rem; }.finish-stats div { min-width: 0; }.finish-stats { width: 100%; }.finish-stats div { flex: 1; }.calculation-panel { display: grid; grid-template-columns: 1fr; grid-template-rows: auto; }.calculation-panel > header, .principal-move { border-right: 0; }.engine-metrics, .pv-line, .uci-console { grid-column: 1; grid-row: auto; } }
@media (max-width: 32rem) { .countdown-clock { gap: .3rem; padding-inline: .65rem; }.countdown-clock div { min-width: 3.5rem; }.starting-field ul { grid-template-columns: repeat(2, minmax(0, 1fr)); }.engine-stack { grid-template-columns: 1fr; }.puzzle-board-column { padding: .75rem; }.puzzle-heading { align-items: start; }.search-clock > i { width: 5rem; }.round-strip { grid-template-columns: auto 1fr; }.survivor-count { grid-column: 1 / -1; }.finish-stats span { font-size: .45rem; } }
@media (prefers-reduced-motion: reduce) { .countdown-pulse-layer { display: none; }.confetti-burst i { animation-duration: .18s; } }

.gauntlet--live { overflow: visible; background: var(--color-bg, #f5f7f9); color: var(--color-text, #17202a); }
.gauntlet--live .puzzle-pattern { display: none; }
.viewer-stage.tournament-page {
  --arena-board-size: clamp(24rem, calc(100dvh - 15rem), 42rem);
  --arena-content-height: calc(var(--arena-board-size) + 4rem);
  position: relative;
  display: grid;
  width: 100%;
  gap: var(--space-md, 1rem);
  padding-inline: clamp(.75rem, 1.5vw, 1.75rem);
  padding-block: .55rem 3rem;
  overflow-anchor: none;
}
.viewer-stage .tournament-heading { display: flex; align-items: end; justify-content: space-between; gap: var(--space-xl, 2rem); padding-block-end: .55rem; border-block-end: 1px solid var(--color-border, #d5dbe1); }
.viewer-stage .tournament-heading__title { display: grid; align-items: center; min-width: 0; }
.viewer-stage .title-line { display: flex; align-items: center; gap: var(--space-sm, .5rem); min-width: 0; }
.viewer-stage .tournament-heading h1, .viewer-stage .tournament-heading p { margin: 0; }
.viewer-stage .tournament-heading h1 { max-width: 54rem; overflow-wrap: anywhere; font-size: clamp(1.45rem, 2.7vw, 2.15rem); letter-spacing: -.035em; line-height: 1.05; }
.viewer-stage .tournament-heading__title > p { margin-block-start: .18rem; color: var(--color-text-muted, #607080); font-size: .75rem; }
.viewer-stage .tournament-heading__controls { display: flex; flex-wrap: wrap; align-items: end; justify-content: flex-end; gap: .65rem; }
.viewer-stage .tournament-heading__controls :deep(.follow-engine-picker) { width: clamp(13rem, 19vw, 18rem); }
.live-round-content { display: grid; gap: var(--space-md, 1rem); }
.viewer-stage .arena { display: grid; grid-template-columns: minmax(25rem, 29rem) var(--arena-board-size) minmax(28rem, 1fr); gap: clamp(.7rem, 1.5vw, 1.2rem); align-items: start; height: var(--arena-content-height); min-height: 0; }
.viewer-stage .engine-column, .viewer-stage .activity-column { display: grid; min-height: 0; gap: var(--space-sm, .5rem); }
.viewer-stage .engine-column { grid-template-rows: auto minmax(0, 1fr); align-content: stretch; height: var(--arena-content-height); }
.viewer-stage .board-column { width: var(--arena-board-size); min-width: 0; height: var(--arena-content-height); justify-self: center; }
.viewer-stage .activity-column { grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: minmax(0, 1fr); height: var(--arena-content-height); }
.viewer-stage .activity-column > * { min-width: 0; min-height: 0; height: 100%; }
.viewer-card, .puzzle-log { min-width: 0; min-height: 0; overflow: hidden; border: 1px solid var(--color-border, #d5dbe1); border-radius: var(--radius-md, .5rem); background: var(--color-surface, #fff); }
.viewer-card { display: flex; flex-direction: column; }
.viewer-card > header { display: flex; align-items: center; justify-content: space-between; gap: .8rem; padding: .75rem .85rem; border-block-end: 1px solid var(--color-border, #d5dbe1); }
.viewer-card > header > div { display: grid; min-width: 0; gap: .12rem; }
.viewer-card > header span, .puzzle-clock > span, .puzzle-details dt, .engine-call > span, .focus-pv > span, .overview-primary > span, .consensus-list > span { color: var(--color-text-muted, #607080); font-size: .59rem; font-weight: 750; letter-spacing: .04em; text-transform: uppercase; }
.viewer-card > header strong { overflow: hidden; font-size: .86rem; text-overflow: ellipsis; white-space: nowrap; }
.side-to-move { padding: .28rem .48rem; border-radius: 999px; background: color-mix(in srgb, var(--color-accent, #2f78c4) 9%, transparent); color: var(--color-accent, #2f78c4) !important; white-space: nowrap; }
.puzzle-card { display: grid; grid-template-columns: 9.5rem minmax(0, 1fr); grid-template-rows: auto auto; }
.puzzle-card > header { grid-column: 1 / -1; }
.puzzle-clock { display: grid; grid-column: 1; grid-template-columns: 1fr; align-content: center; align-items: end; gap: .4rem; padding: .7rem .8rem; border-inline-end: 1px solid var(--color-border, #d5dbe1); }
.puzzle-clock time { color: var(--color-text, #17202a); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: clamp(2.5rem, 3.4vw, 3.35rem); font-weight: 780; font-variant-numeric: tabular-nums; letter-spacing: -.07em; line-height: .85; }
.puzzle-clock > i { grid-column: 1; height: .3rem; overflow: hidden; border-radius: 999px; background: var(--color-surface-sunken, #edf2f7); }
.puzzle-clock > i span { display: block; height: 100%; border-radius: inherit; background: var(--color-accent, #2f78c4); transition: width .1s linear; }
.puzzle-card--urgent .puzzle-clock time { color: var(--color-danger, #b42318); }
.puzzle-card--urgent .puzzle-clock > i span { background: var(--color-danger, #b42318); }
.puzzle-details { display: grid; grid-column: 2; align-content: center; gap: .55rem; margin: 0; padding: .65rem .8rem; }
.puzzle-details div { min-width: 0; }
.puzzle-details dd { margin: .2rem 0 0; }
.puzzle-details code { color: var(--color-text, #17202a); font-size: .65rem; line-height: 1.45; }
.fen-value { display: flex; min-width: 0; align-items: center; gap: .35rem; }
.fen-value code { display: block; min-width: 0; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fen-value button { display: grid; width: 1.75rem; height: 1.75rem; flex: 0 0 auto; place-items: center; padding: 0; border: 1px solid var(--color-border, #d5dbe1); border-radius: .38rem; background: var(--color-surface-sunken, #edf2f7); color: var(--color-text-muted, #607080); cursor: pointer; }
.fen-value button:hover { border-color: var(--color-accent, #2f78c4); color: var(--color-accent, #2f78c4); }
.fen-value button:disabled { opacity: .45; cursor: default; }
.puzzle-details .solution-value { color: var(--color-success, #16794b); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .86rem; font-weight: 800; }
.engine-card { --engine: var(--engine-alive); }
.engine-card__heading { display: grid !important; grid-template-columns: auto minmax(0, 1fr) auto; }
.engine-card__heading > div { gap: .05rem !important; }
.engine-card__heading small { overflow: hidden; color: var(--color-text-muted, #607080); font-size: .62rem; text-overflow: ellipsis; white-space: nowrap; }
.engine-card__heading > i { width: .45rem; height: .45rem; border-radius: 50%; background: var(--engine); box-shadow: 0 0 .65rem color-mix(in srgb, var(--engine) 70%, transparent); }
.engine-avatar { display: grid; width: 2.35rem; aspect-ratio: 1; place-items: center; border-radius: .45rem; background: color-mix(in srgb, var(--engine) 14%, var(--color-surface, #fff)); color: var(--engine) !important; font-size: .65rem !important; font-weight: 900; }
.engine-call, .overview-primary { display: grid; padding: .72rem .85rem; border-block-end: 1px solid var(--color-border, #d5dbe1); }
.engine-call strong, .overview-primary strong { margin-block-start: .12rem; color: var(--engine); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 1.65rem; letter-spacing: -.045em; }
.overview-primary small { overflow: hidden; margin-block-start: .12rem; color: var(--color-text-muted, #607080); font-size: .63rem; text-overflow: ellipsis; white-space: nowrap; }
.focus-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; margin: 0; border-block-end: 1px solid var(--color-border, #d5dbe1); background: var(--color-border, #d5dbe1); }
.focus-metrics div { min-width: 0; padding: .55rem .62rem; background: var(--color-surface, #fff); }
.focus-metrics dt { color: var(--color-text-muted, #607080); font-size: .54rem; text-transform: uppercase; }
.focus-metrics dd { overflow: hidden; margin: .15rem 0 0; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .67rem; font-weight: 750; text-overflow: ellipsis; white-space: nowrap; }
.focus-pv { min-width: 0; min-height: 0; flex: 1; overflow: auto; padding: .62rem .85rem; border-block-end: 1px solid var(--color-border, #d5dbe1); }
.focus-pv p { margin: .22rem 0 0; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .62rem; line-height: 1.5; overflow-wrap: anywhere; white-space: normal; }
.overview-heading { align-items: start !important; }
.overview-primary strong { color: var(--color-accent, #2f78c4); }
.consensus-list { min-height: 0; flex: 1; overflow: auto; padding: .7rem .85rem; }
.consensus-list ol { display: grid; gap: .5rem; margin: .55rem 0 0; padding: 0; list-style: none; }
.consensus-list li { display: grid; grid-template-columns: 4.5rem minmax(0, 1fr) 1.3rem; align-items: center; gap: .5rem; }
.consensus-list li strong { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .7rem; }
.consensus-list li i { height: .25rem; overflow: hidden; border-radius: 999px; background: var(--color-surface-sunken, #edf2f7); }
.consensus-list li i span { display: block; height: 100%; background: var(--color-accent, #2f78c4); }
.consensus-list li small, .consensus-list p { color: var(--color-text-muted, #607080); font-size: .62rem; }
.gauntlet-board { --engine: var(--engine-alive); position: relative; width: 100%; aspect-ratio: 1; overflow: hidden; border-radius: var(--radius-md, .5rem); box-shadow: var(--shadow-sm, 0 2px 6px rgb(0 0 0 / 10%)); }
.gauntlet-board :deep(.board-mount), .gauntlet-board :deep(.chess-viewer) { width: 100%; }
.puzzle-progression { display: grid; height: 3.5rem; box-sizing: border-box; align-content: center; gap: .25rem; margin-block-start: .5rem; padding: .35rem .7rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: var(--radius-md, .5rem); background: var(--color-surface, #fff); }
.puzzle-progression > div:first-child, .progress-counts { display: flex; align-items: center; justify-content: space-between; gap: .8rem; }
.puzzle-progression > div:first-child span, .progress-counts span { color: var(--color-text-muted, #607080); font-size: .65rem; }
.puzzle-progression > div:first-child strong, .progress-counts strong { font-size: .72rem; }
.progress-track { height: .35rem; overflow: hidden; border-radius: 999px; background: var(--color-surface-sunken, #edf2f7); }
.progress-track span { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--color-accent, #2f78c4), #7652b5); }
.puzzle-log { display: flex; flex-direction: column; }
.puzzle-log > header { display: flex; min-height: 2.1rem; box-sizing: border-box; align-items: center; justify-content: space-between; gap: .5rem; padding: .35rem .65rem; border-block-end: 1px solid var(--color-border, #d5dbe1); background: color-mix(in srgb, var(--color-bg, #f5f7f9) 72%, var(--color-surface, #fff)); }
.puzzle-log h2 { margin: 0; font-size: .72rem; }
.puzzle-log > header > span { min-width: 1rem; padding: .05rem .25rem; border-radius: 999px; background: color-mix(in srgb, var(--color-border, #d5dbe1) 60%, transparent); font-size: .6rem; text-align: center; }
.puzzle-log ol { min-height: 0; flex: 1; overflow-y: auto; margin: 0; padding: 0; list-style: none; scrollbar-gutter: stable; }
.puzzle-log li { display: grid; gap: .45rem; padding: .75rem; border-block-end: 1px solid var(--color-border, #d5dbe1); }
.puzzle-log li > div { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: baseline; gap: .45rem; }
.puzzle-log li > div > span { color: var(--color-accent, #2f78c4); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .66rem; font-weight: 800; }
.puzzle-log li > div strong { overflow: hidden; font-size: .75rem; text-overflow: ellipsis; white-space: nowrap; }
.puzzle-log li > div small, .puzzle-log li > small { color: var(--color-text-muted, #607080); font-size: .59rem; }
.puzzle-log li code { overflow-wrap: anywhere; color: var(--color-text-muted, #607080); font-size: .56rem; line-height: 1.4; }
.puzzle-log li p { display: flex; align-items: baseline; justify-content: space-between; gap: .5rem; margin: 0; font-size: .64rem; }
.puzzle-log li p span { color: var(--color-text-muted, #607080); }
.puzzle-log li p strong { color: var(--color-success, #16794b); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.puzzle-log__empty { margin: auto; padding: 1.2rem; color: var(--color-text-muted, #607080); font-size: .72rem; line-height: 1.5; text-align: center; }
.viewer-stage .tournament-data { display: grid; gap: var(--space-sm, .5rem); margin-block-start: 0; }
.viewer-stage .data-tabs { display: flex; gap: .3rem; overflow-x: auto; }
.viewer-stage .data-tabs > span { display: inline-flex; min-height: 2.35rem; align-items: center; gap: .45rem; padding-inline: .85rem; border: 1px solid color-mix(in srgb, var(--color-accent, #2f78c4) 24%, transparent); border-radius: var(--radius-sm, .35rem); background: color-mix(in srgb, var(--color-accent, #2f78c4) 9%, var(--color-surface, #fff)); color: var(--color-accent, #2f78c4); font-size: .78rem; font-weight: 730; }
.viewer-stage .data-tabs small { color: inherit; font-size: .64rem; opacity: .75; }
.viewer-stage .data-panel { overflow: hidden; border: 1px solid var(--color-border, #d5dbe1); border-radius: var(--radius-lg, .75rem); background: var(--color-surface, #fff); }
.viewer-stage .data-panel > header { padding: var(--space-md, 1rem); border-block-end: 1px solid var(--color-border, #d5dbe1); }
.viewer-stage .data-panel h2, .viewer-stage .data-panel header p { margin: 0; }
.viewer-stage .data-panel h2 { font-size: 1rem; }
.viewer-stage .data-panel header p { margin-block-start: .18rem; color: var(--color-text-muted, #607080); font-size: .7rem; }
.viewer-stage .table-wrap { overflow-x: auto; }
.viewer-stage .data-panel table { width: 100%; border-collapse: collapse; font-size: .82rem; }
.viewer-stage .data-panel th, .viewer-stage .data-panel td { padding: .72rem .85rem; border-block-end: 1px solid var(--color-border, #d5dbe1); text-align: start; }
.viewer-stage .data-panel th { color: var(--color-text-muted, #607080); font-size: .63rem; letter-spacing: .04em; text-transform: uppercase; white-space: nowrap; }
.viewer-stage .data-panel tbody tr:last-child td { border-block-end: 0; }
.viewer-stage .data-panel tbody tr:hover { background: color-mix(in srgb, var(--color-accent, #2f78c4) 4.5%, transparent); }
.viewer-stage .rank-cell { width: 4rem; color: var(--color-text-muted, #607080); font-variant-numeric: tabular-nums; }
.viewer-stage .number-cell { width: 7rem; font-weight: 730; font-variant-numeric: tabular-nums; }
.standing-engine { --engine: var(--engine-alive); display: inline-grid; grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: .55rem; }
.standing-engine > i { display: grid; width: 2rem; aspect-ratio: 1; place-items: center; border-radius: .4rem; background: color-mix(in srgb, var(--engine) 14%, var(--color-surface, #fff)); color: var(--engine); font-size: .56rem; font-style: normal; font-weight: 900; }
.standing-engine > span { display: grid; }
.standing-engine small { color: var(--color-text-muted, #607080); font-size: .62rem; }
.survival-status { display: inline-flex; align-items: center; gap: .35rem; font-size: .7rem; font-weight: 730; }
.survival-status::before { width: .4rem; height: .4rem; border-radius: 50%; background: var(--color-success, #16794b); content: ""; }
.survival-status[data-status="eliminated"] { color: var(--color-text-muted, #607080); }
.survival-status[data-status="eliminated"]::before { background: var(--color-text-muted, #607080); }
.standing--eliminated { color: var(--color-text-muted, #607080); }
.standing--eliminated .standing-engine { --engine: var(--engine-knocked-out); opacity: .68; }
.viewer-stage td code { font-size: .7rem; }
@media (max-width: 96rem) {
  .viewer-stage .arena { grid-template-columns: minmax(25rem, 1fr) var(--arena-board-size); height: auto; }
  .viewer-stage .engine-column { grid-column: 1; grid-row: 1; }
  .viewer-stage .board-column { grid-column: 2; grid-row: 1; }
  .viewer-stage .activity-column { grid-column: 1 / -1; grid-row: 2; grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: minmax(24rem, 34rem); height: min(34rem, calc(100dvh - 8rem)); }
}
@media (max-width: 58rem) {
  .viewer-stage .tournament-heading { align-items: stretch; flex-direction: column; }
  .viewer-stage .tournament-heading__controls { justify-content: space-between; }
  .viewer-stage .tournament-heading__controls :deep(.follow-engine-picker) { width: auto; min-width: 0; flex: 1; }
  .viewer-stage .arena { grid-template-columns: 1fr; }
  .viewer-stage .engine-column { grid-column: 1; grid-row: 2; grid-template-columns: 1fr 1fr; grid-template-rows: auto; align-items: start; height: auto; }
  .viewer-stage .board-column { grid-column: 1; grid-row: 1; width: min(100%, var(--arena-board-size)); }
  .viewer-stage .activity-column { grid-column: 1; grid-row: 3; }
}
@media (max-width: 40rem) {
  .viewer-stage .tournament-heading__controls { align-items: stretch; flex-direction: column-reverse; }
  .viewer-stage .tournament-heading__controls :deep(.follow-engine-picker) { width: 100%; }
  .viewer-stage .engine-column { grid-template-columns: 1fr; }
  .viewer-stage .activity-column { grid-template-columns: 1fr; grid-template-rows: minmax(18rem, 24rem) minmax(24rem, 32rem); height: auto; }
  .focus-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
