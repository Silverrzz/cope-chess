<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { api } from "@/api/client";
import ChessViewer from "@/components/chess/ChessViewer.vue";
import MoveList from "@/components/chess/MoveList.vue";
import ChatPanel from "@/components/public/ChatPanel.vue";
import ContentState from "@/components/public/ContentState.vue";
import EnginePanel from "@/components/public/EnginePanel.vue";
import StreamIndicator from "@/components/public/StreamIndicator.vue";
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

let controller: AbortController | null = null;
let stream: EventSource | null = null;
let refreshTimer: number | undefined;

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

onMounted(() => selectInitial());
onBeforeUnmount(closeStream);

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
</script>

<template>
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
</template>

<style scoped>
.relay-broadcast { --relay-ink: #101827; display: grid; gap: .85rem; margin: 1.25rem 0 2rem; color: var(--relay-ink); }.relay-broadcast__bar { display: flex; align-items: end; justify-content: space-between; gap: 1rem; padding: .8rem .9rem; border: 1px solid var(--color-border, #d9e0ea); border-radius: .7rem; background: var(--color-surface, #fff); }.relay-broadcast__bar > div:first-child { display: grid; gap: .16rem; }.relay-broadcast__bar > div:first-child span, .relay-broadcast__selectors label > span { color: var(--color-text-muted, #64748b); font-size: .58rem; font-weight: 760; letter-spacing: .08em; text-transform: uppercase; }.relay-broadcast__bar strong { font-size: .92rem; }.relay-broadcast__selectors { display: flex; align-items: end; gap: .65rem; }.relay-broadcast__selectors label { display: grid; gap: .28rem; }.relay-broadcast__selectors select { min-width: 9rem; height: 2rem; border: 1px solid var(--color-border, #d9e0ea); border-radius: .4rem; background: var(--color-surface, #fff); color: var(--color-text, #172033); font-size: .69rem; }.relay-broadcast__state { display: flex; align-items: center; gap: .5rem; min-height: 2rem; padding: 0 .65rem; border-radius: .4rem; background: var(--color-surface-subtle, #f1f5f9); font-size: .65rem; font-weight: 700; }
.handoff-strip { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; overflow: hidden; border: 1px solid var(--color-border, #d9e0ea); border-radius: .65rem; background: var(--color-border, #d9e0ea); }.handoff-strip > div { position: relative; display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: .2rem .8rem; padding: .65rem .8rem .65rem 1rem; background: linear-gradient(90deg, color-mix(in srgb, var(--relay-primary) 10%, white), white 32%); }.handoff-strip > div::before { position: absolute; inset: 0 auto 0 0; width: 4px; background: var(--relay-primary); content: ""; }.handoff-strip span { color: var(--relay-primary); font-size: .56rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }.handoff-strip strong { overflow: hidden; font-size: .78rem; text-overflow: ellipsis; white-space: nowrap; }.handoff-strip small { grid-column: 2; color: var(--color-text-muted, #64748b); font-size: .58rem; }.handoff-strip i { grid-column: 3; grid-row: 1 / 3; width: .55rem; height: .55rem; border-radius: 50%; background: var(--color-border-strong, #9ca8b8); }.handoff-strip i.live { background: var(--relay-primary); box-shadow: 0 0 0 .3rem color-mix(in srgb, var(--relay-primary) 18%, transparent); animation: relay-pulse 1.5s ease-in-out infinite; }
.relay-arena { display: grid; grid-template-columns: minmax(15rem, .58fr) minmax(24rem, 1fr) minmax(25rem, 1.12fr); align-items: stretch; gap: .85rem; min-width: 0; }.relay-activity { display: grid; grid-template-rows: auto minmax(0, 1fr); min-width: 0; min-height: 42rem; overflow: hidden; border: 1px solid var(--color-border, #d9e0ea); border-radius: .65rem; background: var(--color-surface, #fff); }.relay-activity nav { display: grid; grid-template-columns: 1fr 1fr; border-bottom: 1px solid var(--color-border, #d9e0ea); }.relay-activity nav button { display: flex; align-items: center; justify-content: center; gap: .35rem; min-height: 2.45rem; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--color-text-muted, #64748b); cursor: pointer; font-size: .69rem; font-weight: 720; }.relay-activity nav button.active { border-color: var(--color-accent, #315fcc); color: var(--color-text, #172033); }.relay-activity nav span { font-size: .55rem; }.relay-activity__panel { min-height: 0; border: 0; border-radius: 0; }.relay-board { display: grid; align-content: start; gap: .55rem; min-width: 0; }.relay-board__caption { display: flex; align-items: center; justify-content: space-between; gap: .5rem; padding: .5rem .65rem; border: 1px solid var(--color-border, #d9e0ea); border-radius: .45rem; background: var(--color-surface, #fff); }.relay-board__caption span { color: var(--color-text-muted, #64748b); font-size: .6rem; }.relay-board__caption strong { font-size: .7rem; }
.relay-engines { display: grid; grid-template-rows: repeat(2, minmax(0, 1fr)); gap: .85rem; min-width: 0; }.relay-team { display: grid; grid-template-rows: auto auto minmax(0, 1fr); gap: .6rem; min-width: 0; padding: .65rem; border: 1px solid color-mix(in srgb, var(--relay-primary) 35%, var(--color-border, #d9e0ea)); border-radius: .7rem; background: linear-gradient(145deg, color-mix(in srgb, var(--relay-primary) 6%, white), white 38%); }.relay-team > header { display: flex; align-items: end; justify-content: space-between; gap: .7rem; padding: 0 .15rem; }.relay-team > header div { display: grid; gap: .08rem; }.relay-team > header span { color: var(--relay-primary); font-size: .53rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }.relay-team > header h3 { margin: 0; font-size: .82rem; }.relay-team > header > strong { color: var(--color-text-muted, #64748b); font-size: .58rem; font-weight: 600; }.relay-roster { display: grid; grid-template-columns: repeat(auto-fit, minmax(8.5rem, 1fr)); gap: .35rem; }.relay-roster article { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: .25rem .5rem; min-width: 0; padding: .45rem; border: 1px solid var(--color-border, #d9e0ea); border-radius: .45rem; background: color-mix(in srgb, var(--color-surface, #fff) 94%, var(--relay-primary)); transition: border-color .16s, box-shadow .16s, transform .16s; }.relay-roster article > span { display: grid; grid-row: 1 / 3; width: 1.3rem; height: 1.3rem; place-items: center; border-radius: 50%; background: var(--color-surface-subtle, #f1f5f9); color: var(--color-text-muted, #64748b); font-size: .55rem; font-weight: 800; }.relay-roster article > div { display: grid; min-width: 0; }.relay-roster article strong, .relay-roster article small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.relay-roster article strong { font-size: .61rem; }.relay-roster article small { color: var(--color-text-muted, #64748b); font-size: .52rem; }.relay-roster article dl { grid-column: 2; display: flex; gap: .55rem; margin: .1rem 0 0; }.relay-roster article dl div { display: flex; gap: .2rem; }.relay-roster dt { color: var(--color-text-muted, #64748b); font-size: .49rem; text-transform: uppercase; }.relay-roster dd { margin: 0; font-size: .51rem; font-weight: 700; }.relay-roster article.current { border-color: var(--relay-primary); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--relay-primary) 35%, transparent); }.relay-roster article.thinking { transform: translateY(-1px); box-shadow: 0 0 0 2px color-mix(in srgb, var(--relay-primary) 22%, transparent), 0 .35rem .8rem color-mix(in srgb, var(--relay-primary) 13%, transparent); }.relay-roster article.thinking > span { background: var(--relay-primary); color: white; }.relay-team :deep(.engine-panel) { min-height: 13.5rem; grid-template-rows: auto minmax(3rem, .5fr) minmax(5.5rem, 1fr); border-color: color-mix(in srgb, var(--relay-primary) 24%, var(--color-border, #d9e0ea)); border-inline-start-color: var(--relay-primary); }.relay-team :deep(.engine-panel--active) { box-shadow: 0 0 0 2px color-mix(in srgb, var(--relay-primary) 20%, transparent); }
@keyframes relay-pulse { 50% { opacity: .55; transform: scale(.85); } }
@media (max-width: 88rem) { .relay-arena { grid-template-columns: minmax(14rem, .55fr) minmax(22rem, 1fr); }.relay-engines { grid-column: 1 / -1; grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: none; }.relay-activity { min-height: 34rem; } }
@media (max-width: 60rem) { .relay-broadcast__bar { align-items: stretch; flex-direction: column; }.relay-broadcast__selectors { flex-wrap: wrap; }.relay-arena { grid-template-columns: 1fr; }.relay-board { grid-row: 1; }.relay-activity { min-height: 26rem; }.relay-engines { grid-template-columns: 1fr; }.handoff-strip { grid-template-columns: 1fr; } }
@media (max-width: 38rem) { .relay-broadcast__selectors { align-items: stretch; flex-direction: column; }.relay-broadcast__selectors select { width: 100%; }.handoff-strip > div { grid-template-columns: minmax(0, 1fr) auto; }.handoff-strip > div > span { grid-column: 1 / -1; }.handoff-strip small { grid-column: 1; }.handoff-strip i { grid-column: 2; }.relay-roster { grid-template-columns: 1fr; } }
</style>
