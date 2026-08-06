<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

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

const props = defineProps<{ detail: EventDetailResponse }>();

const payload = computed(() => props.detail.custom as EngineRelayPayload);
const fixtureId = ref<number | null>(null);
const gameId = ref<string>("");
const gameData = ref<TournamentDetailResponse | null>(null);
const loading = ref(false);
const loadError = ref("");
const selectedPly = ref(0);
const currentPositionFen = ref("startpos");
const activityTab = ref<"moves" | "chat">("moves");
const streamState = ref<"connecting" | "live" | "reconnecting" | "closed">("closed");
const nowMs = ref(Date.now());

let controller: AbortController | null = null;
let stream: EventSource | null = null;
let refreshTimer: number | undefined;
let countdownTimer: number | undefined;

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
const engineCount = computed(() => payload.value.teams.reduce((total, team) => total + team.roster.length, 0));
const isLive = computed(() => props.detail.event.status === "live" || gameStatus.value === "live" || fixture.value?.tournament?.status === "running");
const targetTime = computed(() => {
  const value = fixture.value?.tournament?.scheduled_start_at ?? props.detail.event.scheduled_start_at;
  const parsed = value ? Date.parse(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : null;
});
const countdown = computed(() => {
  const remaining = Math.max(0, (targetTime.value ?? nowMs.value) - nowMs.value);
  const totalSeconds = Math.floor(remaining / 1000);
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
const scheduleChip = computed(() => {
  if (isLive.value) return "Live now";
  if (!targetTime.value) return "Schedule TBA";
  return new Intl.DateTimeFormat(undefined, { weekday: "long", hour: "numeric", minute: "2-digit" }).format(new Date(targetTime.value));
});
const heroDeck = computed(() => props.detail.event.subtitle || props.detail.event.summary || `A ${engineCount.value}-instance engine-relay event. ${engineCount.value} engines. One relay. Pure competition.`);
const spectatorCount = computed(() => gameData.value?.spectator_count ?? 0);
const systemStatus = computed(() => {
  if (streamState.value === "live") return "Relay feed connected";
  if (streamState.value === "reconnecting") return "Reconnecting relay feed";
  if (isLive.value) return "Synchronising live relay";
  return payload.value.fixtures.length ? "All systems ready" : "Relay setup in progress";
});
const showcaseNodes = computed(() => payload.value.teams.flatMap((team) => team.roster.map((member) => {
  const isWhiteCurrent = whiteTeam.value?.id === team.id && String(whiteMember.value?.engine_id ?? "") === String(member.engine_id);
  const isBlackCurrent = blackTeam.value?.id === team.id && String(blackMember.value?.engine_id ?? "") === String(member.engine_id);
  const thinking = isWhiteCurrent && activeSide.value === "white" || isBlackCurrent && activeSide.value === "black";
  return {
    id: `${team.id}-${member.id}`,
    name: member.label || member.name || member.display_name,
    version: member.version,
    team: team.short_name || team.name,
    color: team.primary_color,
    secondary: team.secondary_color,
    thinking,
    current: isWhiteCurrent || isBlackCurrent,
  };
})).slice(0, 4));

onMounted(() => {
  selectInitial();
  countdownTimer = window.setInterval(() => { nowMs.value = Date.now(); }, 1000);
});
onBeforeUnmount(() => {
  closeStream();
  if (countdownTimer !== undefined) window.clearInterval(countdownTimer);
});

watch(() => payload.value.fixtures, () => selectInitial(), { deep: true });
watch([fixtureId, gameId], () => { void loadGame(); });

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
  stream = new EventSource(`/tournaments/${fixture.value.tournament_id}/events?game_id=${encodeURIComponent(gameId.value)}`);
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

function memberIsCurrent(side: "white" | "black", member: RelayRosterMember): boolean {
  const current = side === "white" ? whiteMember.value : blackMember.value;
  return String(current?.engine_id ?? "") === String(member.engine_id);
}

function appendChat(message: EventDetailResponse["chat_messages"][number]): void {
  if (message.id !== undefined && props.detail.chat_messages.some((item) => String(item.id) === String(message.id))) return;
  props.detail.chat_messages.push(message);
}

function nodeStyle(node: { color: string; secondary: string }): Record<string, string> {
  return { "--node-color": node.color, "--node-secondary": node.secondary };
}

function scrollToSection(id: string): void {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}
</script>

<template>
  <div class="engine-clash-page">
    <section class="clash-hero" aria-labelledby="clash-title">
      <div class="clash-hero__wash" aria-hidden="true"></div>
      <span class="chess-piece chess-piece--king" aria-hidden="true">♚</span>
      <span class="chess-piece chess-piece--knight" aria-hidden="true">♞</span>
      <span class="chess-piece chess-piece--pawn" aria-hidden="true">♟</span>
      <span class="chess-piece chess-piece--bishop" aria-hidden="true">♝</span>

      <div class="clash-hero__content">
        <header class="clash-heading">
          <span class="clash-kicker"><AppIcon name="trophy" :size="15" /> Featured showcase event</span>
          <h1 id="clash-title">{{ detail.event.title }}</h1>
          <p>{{ heroDeck }}</p>
          <div class="clash-facts" aria-label="Event highlights">
            <span><AppIcon name="engine" :size="17" /> {{ engineCount }} Engines</span>
            <span><AppIcon name="refresh" :size="17" /> Relay format</span>
            <span><AppIcon name="clock" :size="17" /> {{ scheduleChip }}</span>
            <span><AppIcon name="radio" :size="17" /> Live showcase</span>
          </div>
        </header>

        <div class="clash-countdown" :class="{ 'clash-countdown--live': isLive }" aria-live="polite">
          <div v-for="part in countdown" :key="part.label">
            <strong>{{ String(part.value).padStart(2, "0") }}</strong>
            <span>{{ part.label }}</span>
          </div>
        </div>
        <p class="clash-schedule"><AppIcon :name="isLive ? 'radio' : 'clock'" :size="18" /> {{ scheduleLabel }}</p>

        <div class="clash-orbit" :class="`clash-orbit--${Math.min(showcaseNodes.length, 4)}`">
          <div class="clash-orbit__rings" aria-hidden="true"><span></span><span></span><span></span></div>
          <article v-for="(node, index) in showcaseNodes" :key="node.id" class="clash-node" :class="[`clash-node--${index + 1}`, { 'clash-node--thinking': node.thinking }]" :style="nodeStyle(node)">
            <div class="clash-node__beacon"><span><AppIcon name="engine" :size="31" /></span></div>
            <div class="clash-node__card">
              <strong>{{ node.name }}</strong>
              <span>{{ node.team }} · Instance {{ index + 1 }}</span>
              <small><i></i>{{ node.thinking ? "Thinking now" : node.current ? "On relay" : isLive ? "Ready" : "Relay node" }}</small>
            </div>
          </article>
          <p v-if="!showcaseNodes.length" class="clash-orbit__empty">Engine instances will join the circuit when the teams are locked.</p>
        </div>

        <div class="clash-actions">
          <button type="button" class="clash-primary-action" @click="scrollToSection('relay-arena-section')"><AppIcon name="trophy" :size="19" /> Enter arena <AppIcon name="arrow-right" :size="18" /></button>
          <button type="button" class="clash-detail-action" @click="scrollToSection('event-details')">View event details <AppIcon name="arrow-right" :size="16" /></button>
        </div>
      </div>

      <aside class="clash-status-card clash-status-card--systems">
        <span class="clash-status-card__icon"><AppIcon name="activity" :size="25" /></span>
        <div><small>Live status</small><strong>{{ systemStatus }}</strong></div>
        <i :class="{ active: streamState === 'live' || !isLive }"></i>
      </aside>
      <aside class="clash-status-card clash-status-card--audience">
        <span class="clash-status-card__icon"><AppIcon name="user" :size="25" /></span>
        <div><small>Live audience</small><strong>{{ spectatorCount.toLocaleString() }}</strong><span>{{ spectatorCount === 1 ? "viewer" : "viewers" }}</span></div>
      </aside>
    </section>

    <section id="relay-arena-section" class="relay-showcase">
      <div class="relay-showcase__inner">
        <header class="relay-showcase__heading">
          <div><span><i></i> Live relay control</span><h2>The arena</h2><p>Every handoff, move and engine thought streamed from the relay in real time.</p></div>
          <RouterLink to="/events" class="relay-showcase__back"><AppIcon name="arrow-left" :size="16" /> All events</RouterLink>
        </header>

  <section class="relay-broadcast">
    <header class="relay-broadcast__bar">
      <div>
        <span>Engine relay broadcast</span>
        <strong>{{ fixture?.title ?? "No fixture registered" }}</strong>
      </div>
      <div class="relay-broadcast__selectors">
        <label v-if="payload.fixtures.length > 1"><span>Fixture</span><select :value="fixture?.id ?? ''" @change="selectFixture(($event.target as HTMLSelectElement).value)"><option v-for="item in payload.fixtures" :key="item.id" :value="item.id">{{ item.title }}</option></select></label>
        <label v-if="fixture?.games.length"><span>Board</span><select :value="gameId" @change="selectGame(($event.target as HTMLSelectElement).value)"><option v-for="game in fixture.games" :key="game.id" :value="String(game.id)">Game {{ game.game_number ?? game.id }} · {{ statusLabel(game.status) }}<template v-if="game.result"> · {{ resultLabel(game.result) }}</template></option></select></label>
        <div class="relay-broadcast__state"><StreamIndicator v-if="gameData && !['finished', 'aborted'].includes(gameData.tournament.status)" :state="streamState" /><span>{{ gameLabel }}</span></div>
      </div>
    </header>

    <ContentState v-if="!payload.fixtures.length" kind="empty" title="The first relay fixture is being assembled" message="Teams and engines will appear here as soon as the control room registers them." />
    <ContentState v-else-if="!fixture?.games.length" kind="empty" title="This relay is not on the board yet" message="The fixture exists as an unrated tournament draft. Its games appear here when it is scheduled or started." />
    <ContentState v-else-if="loading && !gameData" kind="loading" title="Synchronising the relay" />
    <ContentState v-else-if="loadError && !gameData" kind="error" :message="loadError" action-label="Try again" @action="loadGame" />

    <template v-else-if="gameData && viewerGame">
      <div class="handoff-strip">
        <div v-for="side in (['white', 'black'] as const)" :key="side" :style="teamStyle(side === 'white' ? whiteTeam : blackTeam)">
          <span>{{ side }} relay</span>
          <strong>{{ (side === 'white' ? whiteMember : blackMember)?.display_name || (side === 'white' ? whiteTeam : blackTeam)?.name }}</strong>
          <small>{{ (side === 'white' ? whiteMember : blackMember)?.relay_moves ?? 0 }} team moves · {{ ((side === 'white' ? whiteMember : blackMember)?.nodes ?? 0).toLocaleString() }} nodes</small>
          <i :class="{ live: activeSide === side }"></i>
        </div>
      </div>

      <div class="relay-arena">
        <aside class="relay-activity">
          <nav aria-label="Game activity"><button type="button" :class="{ active: activityTab === 'moves' }" @click="activityTab = 'moves'">Moves <span>{{ moves.length }}</span></button><button type="button" :class="{ active: activityTab === 'chat' }" @click="activityTab = 'chat'">Chat <span>{{ detail.chat_messages.length }}</span></button></nav>
          <MoveList v-if="activityTab === 'moves'" class="relay-activity__panel" :moves="moves.map((move) => move.san || move.uci)" :uci-moves="moves.map((move) => move.uci)" :fen="opening.fen" :book-plies="bookPlies" :model-value="selectedPly" title="Relay move log" @update:model-value="selectedPly = $event" />
          <ChatPanel v-else class="relay-activity__panel" :messages="detail.chat_messages" :settings="detail.chat_settings" :event-slug="detail.event.slug" @sent="appendChat" />
        </aside>

        <div class="relay-board">
          <ChessViewer :opening="opening" :moves="moves" :model-value="selectedPly" :label="`${whiteTeam?.name ?? 'White'} versus ${blackTeam?.name ?? 'Black'} relay game`" @update:model-value="selectedPly = $event" @position="currentPositionFen = $event.fen" />
          <div class="relay-board__caption"><span>Game {{ viewerGame.game_number ?? viewerGame.id }}</span><strong>{{ whiteTeam?.short_name || whiteTeam?.name }} — {{ blackTeam?.short_name || blackTeam?.name }}</strong><span>{{ statusLabel(gameStatus) }}</span></div>
        </div>

        <div class="relay-engines">
          <section v-for="side in (['black', 'white'] as const)" :key="side" class="relay-team" :style="teamStyle(side === 'white' ? whiteTeam : blackTeam)">
            <header><div><span>{{ side }} team</span><h3>{{ (side === 'white' ? whiteTeam : blackTeam)?.name }}</h3></div><strong>{{ (side === 'white' ? whiteTeam : blackTeam)?.motto }}</strong></header>
            <div class="relay-roster">
              <article v-for="(member, index) in (side === 'white' ? whiteTeam : blackTeam)?.roster ?? []" :key="member.id" :class="{ current: memberIsCurrent(side, member), thinking: memberIsCurrent(side, member) && activeSide === side }">
                <span>{{ index + 1 }}</span><div><strong>{{ member.label || member.name }}</strong><small>{{ member.version }}</small></div><dl><div><dt>Run</dt><dd>{{ member.relay_moves }}</dd></div><div><dt>Nodes</dt><dd>{{ member.nodes.toLocaleString() }}</dd></div></dl>
              </article>
            </div>
            <EnginePanel :side="side" :name="(side === 'white' ? whiteMember : blackMember)?.display_name || (side === 'white' ? whiteTeam : blackTeam)?.name || ''" :engine-id="(side === 'white' ? whiteMember : blackMember)?.engine_id ?? null" :clock="`${((side === 'white' ? whiteMember : blackMember)?.nodes ?? 0).toLocaleString()} nodes`" :analysis="side === 'white' ? whiteAnalysis : blackAnalysis" :position-fen="currentPositionFen" :active="activeSide === side" />
          </section>
        </div>
      </div>
    </template>
  </section>
      </div>
    </section>

    <section id="event-details" class="clash-details">
      <div class="clash-details__inner">
        <header><span>Inside the clash</span><h2>A relay built for spectacle</h2></header>
        <div class="clash-details__grid">
          <article><AppIcon name="refresh" :size="24" /><span>Format</span><h3>Engine relay</h3><p>Each team rotates through its configured engines after a fixed run of team moves, turning every handoff into part of the contest.</p></article>
          <article><AppIcon name="engine" :size="24" /><span>Field</span><h3>{{ engineCount }} engine instances</h3><p>Every engine keeps its own identity, relay length and node-time allowance while contributing to one shared game.</p></article>
          <article><AppIcon name="trophy" :size="24" /><span>Stakes</span><h3>Showcase, unrated</h3><p>This is a special event outside the rating circuit, designed around drama, personality and team strategy.</p></article>
        </div>
        <div v-if="detail.event.description || detail.event.rules" class="clash-details__notes">
          <div v-if="detail.event.description"><span>The concept</span><p>{{ detail.event.description }}</p></div>
          <div v-if="detail.event.rules"><span>Rules of the relay</span><p>{{ detail.event.rules }}</p></div>
        </div>
        <div v-if="payload.teams.length" class="clash-team-list">
          <article v-for="team in payload.teams" :key="team.id" :style="teamStyle(team)">
            <i></i><div><span>Relay team</span><h3>{{ team.name }}</h3><p>{{ team.profile || team.motto || `${team.roster.length} engines ready for the handoff.` }}</p></div>
            <ul><li v-for="member in team.roster" :key="member.id"><strong>{{ member.label || member.name }}</strong><span>{{ member.relay_moves }} moves · {{ member.nodes.toLocaleString() }} nodes</span></li></ul>
          </article>
        </div>
      </div>
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

.clash-hero__content {
  z-index: 2;
  display: grid;
  width: min(100%, 100rem);
  margin: 0 auto;
  justify-items: center;
  align-content: center;
  padding: clamp(2rem, 4vh, 3.5rem) clamp(1rem, 4vw, 4rem) 2rem;
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

.clash-heading > p {
  max-width: 54rem;
  margin: .9rem 0 0;
  color: #60708b;
  font-size: clamp(.95rem, 1.45vw, 1.18rem);
  line-height: 1.5;
}

.clash-facts {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: .6rem;
  margin-top: 1.2rem;
}

.clash-facts span {
  display: inline-flex;
  min-height: 2.15rem;
  align-items: center;
  gap: .45rem;
  padding: 0 .95rem;
  border: 1px solid rgb(255 255 255 / 84%);
  border-radius: 999px;
  background: rgb(255 255 255 / 66%);
  box-shadow: 0 .4rem 1.4rem rgb(62 96 151 / 5%);
  color: #27344b;
  font-size: .76rem;
  font-weight: 710;
  backdrop-filter: blur(12px);
}

.clash-facts :deep(.app-icon) { color: #5d7fac; }

.clash-countdown {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  width: min(100%, 50rem);
  margin-top: 1.1rem;
  overflow: hidden;
  border: 1px solid rgb(255 255 255 / 94%);
  border-radius: 1rem;
  background: linear-gradient(145deg, rgb(255 255 255 / 76%), rgb(239 246 255 / 58%));
  box-shadow: inset 0 0 0 1px rgb(112 147 196 / 8%), 0 1.25rem 4rem rgb(47 81 132 / 9%);
  backdrop-filter: blur(22px);
}

.clash-countdown > div {
  position: relative;
  display: grid;
  justify-items: center;
  gap: .35rem;
  padding: clamp(.8rem, 2.6vh, 1.45rem) .75rem 1.2rem;
}

.clash-countdown > div + div::before {
  position: absolute;
  top: 18%;
  bottom: 18%;
  left: 0;
  width: 1px;
  background: #c9d7ea;
  content: "";
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

.clash-countdown--live { box-shadow: inset 0 0 0 1px rgb(54 142 221 / 16%), 0 0 3rem rgb(65 143 230 / 14%); }
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
  display: grid;
  width: 10.5rem;
  justify-items: center;
  color: var(--clash-ink);
}

.clash-node--1 { top: 14%; left: 4%; }
.clash-node--2 { bottom: 0; left: 30%; }
.clash-node--3 { right: 30%; bottom: 0; }
.clash-node--4 { top: 14%; right: 4%; }

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
  box-shadow: 0 0 0 .2rem color-mix(in srgb, var(--node-color) 16%, transparent), 0 0 1.8rem color-mix(in srgb, var(--node-color) 55%, transparent);
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

.clash-actions { z-index: 4; display: grid; justify-items: center; gap: .55rem; margin-top: -2.1rem; }
.clash-actions button { display: inline-flex; align-items: center; justify-content: center; border: 0; cursor: pointer; font: inherit; font-weight: 730; }
.clash-primary-action { min-height: 3.2rem; gap: .65rem; padding: 0 1.35rem; border-radius: .55rem; background: linear-gradient(135deg, #215bb9, #3a7add); box-shadow: 0 .7rem 1.6rem rgb(29 86 180 / 26%); color: white; transition: transform .18s ease, box-shadow .18s ease; }
.clash-primary-action:hover { transform: translateY(-2px); box-shadow: 0 1rem 2rem rgb(29 86 180 / 32%); }
.clash-detail-action { gap: .35rem; padding: .25rem; background: transparent; color: #2362be; font-size: .78rem; }

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

.relay-showcase { scroll-margin-top: var(--header-height, 4rem); background: linear-gradient(180deg, #f7f9fd, #eef3fa); }
.relay-showcase__inner { width: min(100%, 100rem); margin: 0 auto; padding: clamp(3rem, 6vw, 5rem) clamp(1rem, 3vw, 2.5rem); }
.relay-showcase__heading { display: flex; align-items: end; justify-content: space-between; gap: 2rem; margin-bottom: 1.4rem; }
.relay-showcase__heading > div > span { display: flex; align-items: center; gap: .4rem; color: #2464c2; font-size: .64rem; font-weight: 800; letter-spacing: .11em; text-transform: uppercase; }
.relay-showcase__heading > div > span i { width: .42rem; height: .42rem; border-radius: 50%; background: #2fcb67; box-shadow: 0 0 0 .3rem rgb(47 203 103 / 12%); }
.relay-showcase__heading h2 { margin: .35rem 0 0; font-size: clamp(2.2rem, 4.5vw, 4rem); letter-spacing: -.055em; }
.relay-showcase__heading p { margin: .45rem 0 0; color: #65748b; font-size: .86rem; }
.relay-showcase__back { display: inline-flex; align-items: center; gap: .4rem; color: #49627f; font-size: .73rem; font-weight: 700; text-decoration: none; }

.clash-details { scroll-margin-top: var(--header-height, 4rem); background: #071426; color: #dce7f7; }
.clash-details__inner { width: min(100%, 90rem); margin: 0 auto; padding: clamp(4rem, 8vw, 7rem) clamp(1rem, 4vw, 3rem); }
.clash-details > .clash-details__inner > header { text-align: center; }
.clash-details header > span { color: #70a7ff; font-size: .66rem; font-weight: 820; letter-spacing: .14em; text-transform: uppercase; }
.clash-details header h2 { margin: .45rem 0 0; color: white; font-size: clamp(2rem, 4.5vw, 3.8rem); letter-spacing: -.055em; }
.clash-details__grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1rem; margin-top: 2.5rem; }
.clash-details__grid article { padding: 1.5rem; border: 1px solid #253750; border-radius: 1rem; background: linear-gradient(145deg, rgb(24 45 73 / 82%), rgb(12 28 48 / 72%)); }
.clash-details__grid :deep(.app-icon) { color: #6ea5fb; }
.clash-details__grid article > span { display: block; margin-top: 1.2rem; color: #7790b2; font-size: .6rem; font-weight: 800; letter-spacing: .11em; text-transform: uppercase; }
.clash-details__grid h3 { margin: .3rem 0 0; color: white; font-size: 1.2rem; }
.clash-details__grid p { margin: .65rem 0 0; color: #9fb0c8; font-size: .78rem; line-height: 1.65; }
.clash-details__notes { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; margin-top: 1rem; }
.clash-details__notes > div { padding: 1.25rem 1.5rem; border-left: 2px solid #4f8ae4; background: rgb(18 39 65 / 65%); }
.clash-details__notes span { color: #73a8fa; font-size: .62rem; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }
.clash-details__notes p { margin: .45rem 0 0; color: #adbad0; font-size: .8rem; line-height: 1.7; white-space: pre-line; }
.clash-team-list { display: grid; gap: .8rem; margin-top: 2rem; }
.clash-team-list > article { position: relative; display: grid; grid-template-columns: auto minmax(15rem, .75fr) minmax(20rem, 1.25fr); gap: 1.3rem; align-items: center; overflow: hidden; padding: 1.2rem 1.4rem; border: 1px solid color-mix(in srgb, var(--relay-primary) 35%, #253750); border-radius: .9rem; background: linear-gradient(100deg, color-mix(in srgb, var(--relay-primary) 11%, #102038), #0d1c30 55%); }
.clash-team-list > article > i { width: .55rem; height: 3.4rem; border-radius: 999px; background: var(--relay-primary); box-shadow: 0 0 1.4rem color-mix(in srgb, var(--relay-primary) 55%, transparent); }
.clash-team-list article > div > span { color: var(--relay-primary); font-size: .57rem; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }
.clash-team-list h3 { margin: .15rem 0 0; color: white; font-size: 1rem; }
.clash-team-list p { margin: .3rem 0 0; color: #94a7c2; font-size: .7rem; }
.clash-team-list ul { display: grid; grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr)); gap: .45rem; margin: 0; padding: 0; list-style: none; }
.clash-team-list li { display: grid; padding: .55rem .65rem; border: 1px solid #283c57; border-radius: .55rem; background: rgb(4 15 29 / 30%); }
.clash-team-list li strong { color: #e5edf8; font-size: .67rem; }
.clash-team-list li span { margin-top: .15rem; color: #7f92ae; font-size: .56rem; }

.relay-broadcast { --relay-ink: #101827; display: grid; gap: .85rem; margin: 1.25rem 0 2rem; color: var(--relay-ink); }.relay-broadcast__bar { display: flex; align-items: end; justify-content: space-between; gap: 1rem; padding: .8rem .9rem; border: 1px solid var(--color-border, #d9e0ea); border-radius: .7rem; background: var(--color-surface, #fff); }.relay-broadcast__bar > div:first-child { display: grid; gap: .16rem; }.relay-broadcast__bar > div:first-child span, .relay-broadcast__selectors label > span { color: var(--color-text-muted, #64748b); font-size: .58rem; font-weight: 760; letter-spacing: .08em; text-transform: uppercase; }.relay-broadcast__bar strong { font-size: .92rem; }.relay-broadcast__selectors { display: flex; align-items: end; gap: .65rem; }.relay-broadcast__selectors label { display: grid; gap: .28rem; }.relay-broadcast__selectors select { min-width: 9rem; height: 2rem; border: 1px solid var(--color-border, #d9e0ea); border-radius: .4rem; background: var(--color-surface, #fff); color: var(--color-text, #172033); font-size: .69rem; }.relay-broadcast__state { display: flex; align-items: center; gap: .5rem; min-height: 2rem; padding: 0 .65rem; border-radius: .4rem; background: var(--color-surface-subtle, #f1f5f9); font-size: .65rem; font-weight: 700; }
.handoff-strip { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; overflow: hidden; border: 1px solid var(--color-border, #d9e0ea); border-radius: .65rem; background: var(--color-border, #d9e0ea); }.handoff-strip > div { position: relative; display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: .2rem .8rem; padding: .65rem .8rem .65rem 1rem; background: linear-gradient(90deg, color-mix(in srgb, var(--relay-primary) 10%, white), white 32%); }.handoff-strip > div::before { position: absolute; inset: 0 auto 0 0; width: 4px; background: var(--relay-primary); content: ""; }.handoff-strip span { color: var(--relay-primary); font-size: .56rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }.handoff-strip strong { overflow: hidden; font-size: .78rem; text-overflow: ellipsis; white-space: nowrap; }.handoff-strip small { grid-column: 2; color: var(--color-text-muted, #64748b); font-size: .58rem; }.handoff-strip i { grid-column: 3; grid-row: 1 / 3; width: .55rem; height: .55rem; border-radius: 50%; background: var(--color-border-strong, #9ca8b8); }.handoff-strip i.live { background: var(--relay-primary); box-shadow: 0 0 0 .3rem color-mix(in srgb, var(--relay-primary) 18%, transparent); animation: relay-pulse 1.5s ease-in-out infinite; }
.relay-arena { display: grid; grid-template-columns: minmax(15rem, .58fr) minmax(24rem, 1fr) minmax(25rem, 1.12fr); align-items: stretch; gap: .85rem; min-width: 0; }.relay-activity { display: grid; grid-template-rows: auto minmax(0, 1fr); min-width: 0; min-height: 42rem; overflow: hidden; border: 1px solid var(--color-border, #d9e0ea); border-radius: .65rem; background: var(--color-surface, #fff); }.relay-activity nav { display: grid; grid-template-columns: 1fr 1fr; border-bottom: 1px solid var(--color-border, #d9e0ea); }.relay-activity nav button { display: flex; align-items: center; justify-content: center; gap: .35rem; min-height: 2.45rem; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--color-text-muted, #64748b); cursor: pointer; font-size: .69rem; font-weight: 720; }.relay-activity nav button.active { border-color: var(--color-accent, #315fcc); color: var(--color-text, #172033); }.relay-activity nav span { font-size: .55rem; }.relay-activity__panel { min-height: 0; border: 0; border-radius: 0; }.relay-board { display: grid; align-content: start; gap: .55rem; min-width: 0; }.relay-board__caption { display: flex; align-items: center; justify-content: space-between; gap: .5rem; padding: .5rem .65rem; border: 1px solid var(--color-border, #d9e0ea); border-radius: .45rem; background: var(--color-surface, #fff); }.relay-board__caption span { color: var(--color-text-muted, #64748b); font-size: .6rem; }.relay-board__caption strong { font-size: .7rem; }
.relay-engines { display: grid; grid-template-rows: repeat(2, minmax(0, 1fr)); gap: .85rem; min-width: 0; }.relay-team { display: grid; grid-template-rows: auto auto minmax(0, 1fr); gap: .6rem; min-width: 0; padding: .65rem; border: 1px solid color-mix(in srgb, var(--relay-primary) 35%, var(--color-border, #d9e0ea)); border-radius: .7rem; background: linear-gradient(145deg, color-mix(in srgb, var(--relay-primary) 6%, white), white 38%); }.relay-team > header { display: flex; align-items: end; justify-content: space-between; gap: .7rem; padding: 0 .15rem; }.relay-team > header div { display: grid; gap: .08rem; }.relay-team > header span { color: var(--relay-primary); font-size: .53rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }.relay-team > header h3 { margin: 0; font-size: .82rem; }.relay-team > header > strong { color: var(--color-text-muted, #64748b); font-size: .58rem; font-weight: 600; }.relay-roster { display: grid; grid-template-columns: repeat(auto-fit, minmax(8.5rem, 1fr)); gap: .35rem; }.relay-roster article { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: .25rem .5rem; min-width: 0; padding: .45rem; border: 1px solid var(--color-border, #d9e0ea); border-radius: .45rem; background: color-mix(in srgb, var(--color-surface, #fff) 94%, var(--relay-primary)); transition: border-color .16s, box-shadow .16s, transform .16s; }.relay-roster article > span { display: grid; grid-row: 1 / 3; width: 1.3rem; height: 1.3rem; place-items: center; border-radius: 50%; background: var(--color-surface-subtle, #f1f5f9); color: var(--color-text-muted, #64748b); font-size: .55rem; font-weight: 800; }.relay-roster article > div { display: grid; min-width: 0; }.relay-roster article strong, .relay-roster article small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.relay-roster article strong { font-size: .61rem; }.relay-roster article small { color: var(--color-text-muted, #64748b); font-size: .52rem; }.relay-roster article dl { grid-column: 2; display: flex; gap: .55rem; margin: .1rem 0 0; }.relay-roster article dl div { display: flex; gap: .2rem; }.relay-roster dt { color: var(--color-text-muted, #64748b); font-size: .49rem; text-transform: uppercase; }.relay-roster dd { margin: 0; font-size: .51rem; font-weight: 700; }.relay-roster article.current { border-color: var(--relay-primary); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--relay-primary) 35%, transparent); }.relay-roster article.thinking { transform: translateY(-1px); box-shadow: 0 0 0 2px color-mix(in srgb, var(--relay-primary) 22%, transparent), 0 .35rem .8rem color-mix(in srgb, var(--relay-primary) 13%, transparent); }.relay-roster article.thinking > span { background: var(--relay-primary); color: white; }.relay-team :deep(.engine-panel) { min-height: 13.5rem; grid-template-rows: auto minmax(3rem, .5fr) minmax(5.5rem, 1fr); border-color: color-mix(in srgb, var(--relay-primary) 24%, var(--color-border, #d9e0ea)); border-inline-start-color: var(--relay-primary); }.relay-team :deep(.engine-panel--active) { box-shadow: 0 0 0 2px color-mix(in srgb, var(--relay-primary) 20%, transparent); }
@keyframes relay-pulse { 50% { opacity: .55; transform: scale(.85); } }
@keyframes node-thinking { 50% { transform: translateY(-3px) scale(1.04); box-shadow: 0 0 0 .3rem color-mix(in srgb, var(--node-color) 14%, transparent), 0 0 2.8rem color-mix(in srgb, var(--node-color) 75%, transparent); } }
@media (max-width: 80rem) { .clash-status-card { display: none; }.clash-node--2 { left: 27%; }.clash-node--3 { right: 27%; } }
@media (max-width: 88rem) { .relay-arena { grid-template-columns: minmax(14rem, .55fr) minmax(22rem, 1fr); }.relay-engines { grid-column: 1 / -1; grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: none; }.relay-activity { min-height: 34rem; } }
@media (max-width: 60rem) { .clash-hero__content { align-content: start; }.clash-orbit { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .8rem; height: auto; margin-top: 1.2rem; }.clash-orbit__rings { display: none; }.clash-node { position: relative; inset: auto; width: auto; }.clash-node__card { width: min(100%, 12rem); }.clash-actions { margin-top: 1.5rem; }.relay-showcase__heading { align-items: start; flex-direction: column; }.clash-details__grid { grid-template-columns: 1fr; }.clash-team-list > article { grid-template-columns: auto minmax(0, 1fr); }.clash-team-list ul { grid-column: 2; }.relay-broadcast__bar { align-items: stretch; flex-direction: column; }.relay-broadcast__selectors { flex-wrap: wrap; }.relay-arena { grid-template-columns: 1fr; }.relay-board { grid-row: 1; }.relay-activity { min-height: 26rem; }.relay-engines { grid-template-columns: 1fr; }.handoff-strip { grid-template-columns: 1fr; } }
@media (max-width: 38rem) { .clash-hero__content { padding-top: 1.4rem; }.clash-heading h1 { font-size: clamp(2.7rem, 13vw, 4.2rem); }.clash-heading > p { font-size: .85rem; }.clash-facts { gap: .4rem; }.clash-facts span { min-height: 1.85rem; padding: 0 .65rem; font-size: .65rem; }.clash-countdown { margin-top: 1rem; }.clash-countdown > div { padding: .85rem .25rem; }.clash-countdown strong { font-size: clamp(2rem, 13vw, 3.2rem); }.clash-countdown span { font-size: .5rem; letter-spacing: .1em; }.clash-schedule { text-align: center; }.clash-orbit { grid-template-columns: 1fr 1fr; }.clash-node__beacon > span { width: 3.8rem; height: 3.8rem; }.clash-node__card { min-width: 0; padding-inline: .4rem; }.clash-node__card strong { max-width: 7.5rem; }.clash-details__notes { grid-template-columns: 1fr; }.clash-team-list > article { grid-template-columns: auto minmax(0, 1fr); }.clash-team-list ul { grid-column: 1 / -1; }.relay-broadcast__selectors { align-items: stretch; flex-direction: column; }.relay-broadcast__selectors select { width: 100%; }.handoff-strip > div { grid-template-columns: minmax(0, 1fr) auto; }.handoff-strip > div > span { grid-column: 1 / -1; }.handoff-strip small { grid-column: 1; }.handoff-strip i { grid-column: 2; }.relay-roster { grid-template-columns: 1fr; } }
:global(:root[data-theme="dark"]) .relay-showcase { background: linear-gradient(180deg, #0d141e, #101923); }
:global(:root[data-theme="dark"]) .clash-hero { background: radial-gradient(circle at 50% 42%, #182843, #0d1624 58%, #09111c); }
:global(:root[data-theme="dark"]) .clash-heading h1 { color: #f6f9ff; }
:global(:root[data-theme="dark"]) .clash-heading > p,
:global(:root[data-theme="dark"]) .clash-schedule { color: #a6b8d1; }
:global(:root[data-theme="dark"]) .clash-countdown { border-color: rgb(125 166 225 / 24%); background: rgb(14 29 49 / 72%); }
:global(:root[data-theme="dark"]) .clash-countdown strong { color: #f3f7ff; }
:global(:root[data-theme="dark"]) .clash-facts span,
:global(:root[data-theme="dark"]) .clash-node__card { border-color: rgb(127 165 216 / 18%); background: rgb(15 31 52 / 66%); color: #eef4ff; }
</style>
