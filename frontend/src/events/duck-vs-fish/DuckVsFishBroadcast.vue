<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api } from "@/api/client";
import wakwak from "@/assets/wakwak.svg";
import MoveList from "@/components/chess/MoveList.vue";
import ContentState from "@/components/public/ContentState.vue";
import EnginePanel from "@/components/public/EnginePanel.vue";
import StatusPill from "@/components/public/StatusPill.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import FishMark from "./FishMark.vue";
import EngineCheers from "./EngineCheers.vue";
import ChessViewer from "@/components/chess/ChessViewer.vue";
import ChatPanel from "@/components/public/ChatPanel.vue";
import type { ChatMessage, GameRecord, TournamentDetailResponse } from "@/components/public/types";
import type { EventDetailResponse } from "@/types/events";
import PondScene from "./PondScene.vue";
import type { PondPayload } from "./types";

const props = defineProps<{ detail: EventDetailResponse; view?: "event" | "arena"; clockOffsetMs?: number }>();
const payload = computed(() => props.detail.custom as PondPayload);
const tournament = computed(() => payload.value.tournament);
const games = ref<GameRecord[]>([]);
const viewer = ref<TournamentDetailResponse | null>(null);
const selected = ref<string | null>(null);
const selectedPly = ref(0);
const activeTab = ref<"games" | "settings">("games");
const now = ref(Date.now());
const currentFen = ref("startpos");
const error = ref("");
const busy = ref(false);
const localMessages = ref<ChatMessage[]>([]);
const messages = computed(() => [...props.detail.chat_messages, ...localMessages.value.filter(message => !props.detail.chat_messages.some(existing => existing.id === message.id))]);
const live = computed(() => ["live", "intermission"].includes(props.detail.event.status));
const played = computed(() => games.value.filter(game => game.status === "finished" && ["1-0", "0-1", "1/2-1/2"].includes(game.result ?? "")));
const roster = computed(() => props.detail.cast.filter(member => member.parent_id === null));
const timing = computed(() => props.detail.event.scheduled_start_at
  ? new Intl.DateTimeFormat(undefined, { dateStyle: "long", timeStyle: "short" }).format(new Date(props.detail.event.scheduled_start_at))
  : "Start date TBD");
const remaining = computed(() => Math.max(0, Date.parse(props.detail.event.scheduled_start_at ?? "") - now.value - (props.clockOffsetMs ?? 0)) || 0);
const countdown = computed(() => {
  const seconds = Math.ceil(remaining.value / 1000);
  return [{ label: "days", value: Math.floor(seconds / 86400) }, { label: "hours", value: Math.floor(seconds % 86400 / 3600) }, { label: "mins", value: Math.floor(seconds % 3600 / 60) }, { label: "secs", value: seconds % 60 }];
});
const status = computed(() => ({ announced: "Announced", scheduled: "Scheduled", live: "Live", intermission: "Intermission", completed: "Completed", cancelled: "Cancelled", postponed: "Postponed", draft: "Draft" }[props.detail.event.status]));
const gameReady = computed(() => viewer.value?.viewer_game && ["live", "assigned", "finished"].includes(viewer.value.viewer_game.status));
let request: AbortController | null = null;
let tick = 0;
const timer = window.setInterval(() => {
  now.value = Date.now();
  if (++tick % 3 === 0 && props.view === "arena" && live.value && !document.hidden) void loadViewer();
}, 1000);

watch(() => payload.value.games, value => { games.value = value; }, { immediate: true });
watch(() => [tournament.value?.id, props.detail.event.revision, props.view], () => { if (props.view === "arena") void loadViewer(); }, { immediate: true });
onBeforeUnmount(() => { window.clearInterval(timer); request?.abort(); });

function score(engineId: string | number | null): string {
  let total = 0;
  for (const game of played.value) {
    if (game.result === "1/2-1/2") total += .5;
    else if ((game.result === "1-0" && String(game.white_engine_id) === String(engineId)) || (game.result === "0-1" && String(game.black_engine_id) === String(engineId))) total += 1;
  }
  return String(total).replace(".5", "½").replace(/^0½$/, "½");
}

function name(engineId: string | number): string {
  return roster.value.find(member => String(member.engine_version_id) === String(engineId))?.display_name ?? "Engine";
}

async function loadViewer(gameId?: string): Promise<void> {
  if (!tournament.value || tournament.value.status === "draft") return;
  if (busy.value && gameId === undefined) return;
  if (gameId !== undefined) selected.value = gameId;
  request?.abort();
  const currentRequest = new AbortController();
  request = currentRequest;
  busy.value = true;
  try {
    const response = await api.get<TournamentDetailResponse>(`/api/events/${encodeURIComponent(props.detail.event.slug)}/tournaments/${tournament.value.id}`, {
      query: { game_id: selected.value ?? undefined, page_size: 25 }, signal: currentRequest.signal,
    });
    if (request !== currentRequest) return;
    const followLatest = !viewer.value || String(viewer.value.viewer_game?.id) !== String(response.viewer_game?.id) || selectedPly.value >= viewer.value.viewer_moves.length;
    viewer.value = response;
    if (followLatest) selectedPly.value = response.viewer_moves.length;
    const updates = [...response.games, ...response.active_games];
    games.value = games.value.map(game => updates.find(update => String(update.id) === String(game.id)) ?? game);
    error.value = "";
  } catch (cause) {
    if (request === currentRequest && (cause as Error).name !== "AbortError") error.value = "The board could not be loaded. Select a game to try again.";
  } finally {
    if (request === currentRequest) busy.value = false;
  }
}
</script>

<template>
  <div class="duck-event" :class="{ 'duck-event--arena': view === 'arena' }">
    <section v-if="view !== 'arena'" class="duck-hero" aria-labelledby="duck-event-title">
      <PondScene />
      <div class="duck-hero__content">
        <header class="duck-heading"><span class="duck-kicker"><AppIcon name="trophy" :size="15" /> Duck Chess</span><h1 id="duck-event-title">{{ detail.event.title }}</h1></header>
        <div v-if="remaining > 0" class="duck-countdown" aria-label="Time until event"><div v-for="part in countdown" :key="part.label"><strong>{{ String(part.value).padStart(2, '0') }}</strong><span>{{ part.label }}</span></div></div>
        <p class="duck-schedule"><AppIcon name="clock" :size="17" /> {{ timing }}</p>
        <div class="duck-orbit duck-orbit--2">
          <div class="duck-orbit__rings" aria-hidden="true"><span></span><span></span><span></span></div><span class="duck-vs" aria-hidden="true">VS</span>
          <article v-for="(member, index) in roster" :key="member.id" class="duck-node" :class="`duck-node--${index + 1}`">
            <div class="duck-node__beacon"><span><img v-if="index === 0" :src="wakwak" alt="" /><FishMark v-else /></span></div>
            <div class="duck-node__card"><strong>{{ member.display_name }}</strong><span>{{ member.profile }}</span><b v-if="played.length || live" class="duck-score">{{ score(member.engine_version_id) }}</b><small v-else>64 threads</small></div>
          </article>
        </div>
        <p class="duck-format">20 games <i>·</i> 20m + 12s <i>·</i> Lichess UHO</p>
        <RouterLink :to="{ name: 'event-arena', params: { slug: detail.event.slug } }" class="duck-primary-action"><AppIcon name="trophy" :size="18" /> View arena <AppIcon name="arrow-right" :size="18" /></RouterLink>
        <EngineCheers class="duck-hero-cheers" :event-id="detail.event.id" :slug="detail.event.slug" :members="roster" />
      </div>
      <aside class="duck-status-card duck-status-card--systems"><span class="duck-status-card__icon"><AppIcon name="activity" :size="25" /></span><div><small>Status</small><strong>{{ status }}</strong></div><i :class="{ active: live }"></i></aside>
      <aside class="duck-status-card duck-status-card--audience"><span class="duck-status-card__icon"><AppIcon name="user" :size="25" /></span><div><small>Current spectators</small><strong>{{ (detail.spectator_count ?? 0).toLocaleString() }}</strong></div></aside>
    </section>
    <main v-else class="duck-arena">
      <header class="tournament-heading"><div><div class="title-line"><h1>{{ detail.event.title }}</h1><StatusPill :status="detail.event.status" /></div><p>Duck Chess · {{ played.length }} / 20 games · 20m + 12s · 64 threads</p></div><RouterLink :to="{ name: 'event', params: { slug: detail.event.slug } }"><AppIcon name="arrow-left" :size="15" /> Event page</RouterLink></header>
      <EngineCheers class="duck-arena-cheers" :event-id="detail.event.id" :slug="detail.event.slug" :members="roster" />
      <p v-if="error" role="alert" class="duck-error">{{ error }}</p>
      <div v-if="gameReady && viewer?.viewer_game" class="arena">
        <div class="engine-column"><EnginePanel v-for="side in (['black', 'white'] as const)" :key="side" :side="side" variant="duck" :name="name(side === 'white' ? viewer.viewer_game.white_engine_id : viewer.viewer_game.black_engine_id)" :clock="viewer.clocks?.[side] ?? '—'" :analysis="viewer.engine_data?.[side] ?? null" :position-fen="currentFen" /></div>
        <div class="board-column"><button v-if="selected && live" type="button" class="follow-live" @click="selected = null; loadViewer()">Follow live <AppIcon name="arrow-right" :size="14" /></button><ChessViewer :key="viewer.viewer_game.id" variant="duck" :opening="viewer.opening ?? null" :moves="viewer.viewer_moves" :model-value="selectedPly" label="Duck vs Fish" @update:model-value="selectedPly = $event" @position="currentFen = $event.fen" /></div>
        <aside class="activity-column"><MoveList :moves="viewer.viewer_moves.map(move => move.san || move.uci)" :uci-moves="viewer.viewer_moves.map(move => move.uci)" :fen="viewer.opening?.fen ?? 'startpos'" :book-plies="viewer.opening?.book_moves?.length ?? 0" :model-value="selectedPly" @update:model-value="selectedPly = $event" /><ChatPanel :messages="messages" :settings="detail.chat_settings" :event-slug="detail.event.slug" @sent="localMessages.push($event)" /></aside>
      </div>
      <div v-else class="arena-pending"><ContentState :kind="busy ? 'loading' : 'empty'" :title="busy ? 'Loading game' : detail.event.scheduled_start_at ? timing : 'Start date TBD'" /><ChatPanel :messages="messages" :settings="detail.chat_settings" :event-slug="detail.event.slug" @sent="localMessages.push($event)" /></div>
      <section class="tournament-data">
        <nav class="data-tabs" aria-label="Match information"><button type="button" :aria-pressed="activeTab === 'games'" @click="activeTab = 'games'">Games <span>{{ games.length }}</span></button><button type="button" :aria-pressed="activeTab === 'settings'" @click="activeTab = 'settings'">Settings</button></nav>
        <div v-if="activeTab === 'games'" class="data-panel table-wrap"><table><thead><tr><th>Game</th><th>White</th><th>Black</th><th>Result</th><th></th></tr></thead><tbody><tr v-for="(game, index) in games" :key="game.id"><td>{{ index + 1 }}</td><td>{{ name(game.white_engine_id) }}</td><td>{{ name(game.black_engine_id) }}</td><td>{{ game.result || (['live', 'assigned'].includes(game.status) ? 'Live' : 'Pending') }}</td><td><button v-if="['live', 'assigned', 'finished'].includes(game.status)" type="button" @click="loadViewer(String(game.id))">{{ game.status === 'finished' ? 'Replay' : 'View' }} <AppIcon name="arrow-right" :size="13" /></button></td></tr></tbody></table></div>
        <section v-else class="data-panel"><dl class="settings-list"><div><dt>Variant</dt><dd>Duck Chess</dd></div><div><dt>Time control</dt><dd>20m + 12s</dd></div><div><dt>Threads per engine</dt><dd>64</dd></div><div><dt>Hash per engine</dt><dd>4096 MiB</dd></div><div><dt>Games</dt><dd>20 · 10 colour-swapped pairs</dd></div><div><dt>Opening book</dt><dd>{{ payload.opening_book }}</dd></div><div><dt>Rated</dt><dd>No</dd></div><div><dt>Start</dt><dd>{{ timing }}</dd></div></dl></section>
      </section>
    </main>
  </div>
</template>

<style scoped>
.duck-hero-cheers { --color-surface: #102019; --color-text: #e1e8d8; --color-text-muted: #8a9e90; margin-top: 1.15rem; }
.duck-arena-cheers { justify-content: start; }

.duck-hero {
  border-color: #a8c8f5;
  background:
    radial-gradient(circle at 50% 44%, rgb(255 255 255 / 98%) 0, rgb(238 247 255 / 86%) 24%, transparent 51%),
    radial-gradient(circle at 20% 76%, rgb(88 160 255 / 26%), transparent 34%),
    radial-gradient(circle at 82% 74%, rgb(105 173 255 / 24%), transparent 35%),
    linear-gradient(155deg, #f5f9ff 0%, #e5f0ff 50%, #d5e6fd 100%);
}

.duck-hero {
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

.duck-hero__content {
  z-index: 2;
  display: grid;
  width: min(100%, 100rem);
  margin: 0 auto;
  justify-items: center;
  align-content: center;
  padding: clamp(2rem, 4vh, 3.5rem) clamp(1rem, 4vw, 4rem) 7rem;
}

.duck-heading { display: grid; justify-items: center; text-align: center; }
.duck-kicker {
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

.duck-heading h1 {
  margin: .65rem 0 0;
  color: var(--duck-ink);
  font-size: clamp(3rem, 6.15vw, 5.75rem);
  font-weight: 760;
  letter-spacing: -.065em;
  line-height: .98;
}

.duck-countdown {
  display: flex;
  justify-content: center;
  width: min(100%, 50rem);
  margin-top: 1.1rem;
}

.duck-countdown > div {
  position: relative;
  display: grid;
  width: 25%;
  justify-items: center;
  gap: .35rem;
  padding: clamp(.8rem, 2.6vh, 1.45rem) .75rem 1.2rem;
}

.duck-countdown strong {
  color: #071938;
  font-size: clamp(3rem, 5.7vw, 5.2rem);
  font-weight: 760;
  font-variant-numeric: tabular-nums;
  letter-spacing: -.065em;
  line-height: 1;
}

.duck-countdown span {
  color: #2361bf;
  font-size: .69rem;
  font-weight: 830;
  letter-spacing: .17em;
  text-transform: uppercase;
}

.duck-schedule { display: flex; align-items: center; gap: .45rem; margin: .75rem 0 0; color: #70809a; font-size: .78rem; }

.duck-orbit {
  position: relative;
  width: 100%;
  height: clamp(14rem, 27vh, 18rem);
  max-height: 19rem;
  margin-top: -1.2rem;
}

.duck-orbit__rings { position: absolute; inset: 7% 6% 0; }
.duck-orbit__rings span { position: absolute; border-radius: 50%; }
.duck-orbit__rings span:nth-child(1) { inset: 6% 2% 16%; border: 3px solid rgb(255 255 255 / 94%); box-shadow: 0 0 1.3rem rgb(255 255 255 / 90%); }
.duck-orbit__rings span:nth-child(2) { inset: 0 8% 3%; border: 1px dashed rgb(255 255 255 / 74%); }
.duck-orbit__rings span:nth-child(3) { inset: 17% 15% 1%; border: 1px dashed rgb(99 145 208 / 24%); }

.duck-node {
  position: absolute;
  z-index: 2;
  isolation: isolate;
  display: grid;
  width: 10.5rem;
  justify-items: center;
  color: var(--duck-ink);
  cursor: pointer;
}

.duck-node::before {
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

.duck-node--1 { top: 14%; left: 4%; }
.duck-node--2 { bottom: 0; left: 30%; }
.duck-orbit--2 .duck-node--1 { top: 14%; left: 14%; }
.duck-orbit--2 .duck-node--2 { top: 14%; right: 14%; bottom: auto; left: auto; }

.duck-node__beacon {
  position: relative;
  display: grid;
  width: 7.2rem;
  height: 3.5rem;
  place-items: start center;
  border-radius: 50%;
  background: radial-gradient(ellipse, color-mix(in srgb, var(--node-color) 38%, transparent), transparent 65%);
  box-shadow: 0 .7rem 1.9rem color-mix(in srgb, var(--node-color) 30%, transparent);
}

.duck-node__beacon::before,
.duck-node__beacon::after {
  position: absolute;
  right: .15rem;
  bottom: .05rem;
  left: .15rem;
  height: 1.5rem;
  border: 2px solid color-mix(in srgb, var(--node-color) 48%, white);
  border-radius: 50%;
  content: "";
}

.duck-node__beacon::after { right: 1.05rem; bottom: .35rem; left: 1.05rem; height: 1rem; opacity: .7; }
.duck-node__beacon > span {
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

.duck-node__card {
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

.duck-node__beacon + .duck-node__card { margin-top: -1.15rem; }
.duck-node__card strong { max-width: 9rem; overflow: hidden; font-size: .9rem; text-overflow: ellipsis; white-space: nowrap; }
.duck-node__card > span { margin-top: .22rem; color: var(--node-color); font-size: .63rem; font-weight: 650; }
.duck-node__card small { display: inline-flex; align-items: center; gap: .32rem; margin-top: .52rem; padding: .25rem .5rem; border-radius: 999px; background: color-mix(in srgb, var(--node-color) 9%, white); color: color-mix(in srgb, var(--node-color) 77%, #142136); font-size: .59rem; font-weight: 720; }
.duck-node__card small i { width: .35rem; height: .35rem; border-radius: 50%; background: var(--node-color); }
.duck-status-card {
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

.duck-status-card--systems { left: 3.2%; }
.duck-status-card--audience { right: 3.2%; }
.duck-status-card__icon { display: grid; width: 2.7rem; height: 2.7rem; place-items: center; border-radius: .65rem; background: rgb(255 255 255 / 54%); color: #3a6eb8; }
.duck-status-card > div { display: grid; }
.duck-status-card small { color: #7183a1; font-size: .59rem; font-weight: 760; letter-spacing: .06em; text-transform: uppercase; }
.duck-status-card strong { margin-top: .15rem; font-size: .75rem; }
.duck-status-card div > span { margin-top: .05rem; color: #71819a; font-size: .6rem; }
.duck-status-card > i { width: .45rem; height: .45rem; border-radius: 50%; background: #a7b6c9; }

.duck-event { margin-block: calc(0px - clamp(var(--space-6), 4vw, var(--space-12))) -3rem; }.duck-event--arena { margin: 0; }
.duck-hero { min-height: calc(100dvh - var(--header-height, 4rem)); border-color: #29443a; background: radial-gradient(ellipse at 50% 45%, #244c3c60, transparent 65%), #070f12; color: #eff3e9; }.duck-hero__content { width: min(100%, 84rem); padding-bottom: 7rem; }.duck-kicker { border-color: #b4c98d36; background: #101d18bf; color: #b8cea1; }.duck-heading h1 { color: #f0e5b5; }.duck-schedule { margin-top: 1.15rem; color: #8faaa0; }.duck-countdown strong { color: #eee5bd; font-size: clamp(2.5rem, 5vw, 4.25rem); }.duck-countdown span { color: #a6b88d; }
.duck-orbit { width: min(100%, 64rem); height: clamp(16rem, 31vh, 21rem); max-height: none; margin-top: 1.2rem; }.duck-orbit__rings { inset: 10% 7% 3%; }.duck-orbit__rings span:nth-child(1) { border: 1px solid #b4c98d42; box-shadow: 0 0 2rem #b4c98d0a; }.duck-orbit__rings span:nth-child(2) { border-color: #b4c98d21; }.duck-orbit__rings span:nth-child(3) { border-color: #8bbcb619; }.duck-vs { position: absolute; z-index: 1; top: 43%; left: 50%; display: grid; width: 3.6rem; height: 3.6rem; place-items: center; border: 1px solid #a5bf8e38; border-radius: 50%; background: #0c1716; color: #b6c69b; font-size: .72rem; font-weight: 850; letter-spacing: .1em; transform: translate(-50%, -50%); }
.duck-node { --node-color: #d9ba51; width: 16rem; }.duck-orbit--2 .duck-node--1 { top: 15%; left: 8%; }.duck-orbit--2 .duck-node--2 { --node-color: #85b8af; top: 15%; right: 8%; }.duck-node::before { opacity: .2; }.duck-node__beacon { width: 6rem; box-shadow: none; }.duck-node__beacon::before, .duck-node__beacon::after { border-width: 1px; border-color: color-mix(in srgb, var(--node-color) 35%, transparent); }.duck-node__beacon > span { border: 1px solid color-mix(in srgb, var(--node-color) 50%, transparent); background: #10231e; box-shadow: 0 0 3rem color-mix(in srgb, var(--node-color) 17%, transparent); }.duck-node__beacon img, .duck-node__beacon svg { width: 3rem; height: 3rem; }.duck-node__card { width: 100%; min-height: 8rem; border-color: color-mix(in srgb, var(--node-color) 23%, transparent); background: linear-gradient(#192e2480, #0d1b18d9); box-shadow: none; }.duck-node__card strong { max-width: 100%; color: #f0f1df; font-size: 1rem; }.duck-node__card > span { max-width: 100%; color: #81988c; overflow-wrap: anywhere; font-size: .62rem; text-align: center; }.duck-node__card small { color: var(--node-color); background: transparent; }.duck-score { margin-top: .5rem; color: var(--node-color); font-size: 1.5rem; font-variant-numeric: tabular-nums; }
.duck-format { display: flex; flex-wrap: wrap; justify-content: center; gap: .65rem; margin: .25rem 0 1.25rem; color: #8aa196; font-size: .74rem; }.duck-format i { font-style: normal; color: #53695f; }.duck-primary-action { display: inline-flex; min-height: 3.5rem; align-items: center; justify-content: center; gap: .75rem; padding: 0 1.8rem; border: 1px solid #b7b78033; border-radius: .65rem; background: linear-gradient(135deg, #365b3d, #4c7144); box-shadow: 0 .8rem 1.8rem #0004; color: #f3eed5; font-size: .95rem; font-weight: 730; text-decoration: none; }.duck-primary-action:hover { background: #507747; }
.duck-status-card { min-width: 14rem; border-color: #a9be8e29; background: #102019c9; box-shadow: none; }.duck-status-card__icon { background: #b7c98b0d; color: #a5bc94; }.duck-status-card small { color: #758f7f; }.duck-status-card strong { color: #d9e3ce; }.duck-status-card > i { background: #5b7365; }.duck-status-card > i.active { background: #a1ce86; }
.duck-arena { display: grid; gap: 1rem; }.tournament-heading { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }.title-line { display: flex; flex-wrap: wrap; align-items: center; gap: .7rem; }.title-line h1 { margin: 0; font-size: 1.6rem; letter-spacing: -.04em; }.tournament-heading p { margin: .3rem 0 0; color: var(--color-text-muted); font-size: .75rem; }.tournament-heading > a { display: inline-flex; align-items: center; gap: .4rem; color: var(--color-text-muted); font-size: .75rem; text-decoration: none; }
.arena { display: grid; grid-template-columns: minmax(14rem, .85fr) minmax(20rem, 1.3fr) minmax(16rem, .9fr); align-items: start; gap: 1rem; }.engine-column, .activity-column { display: grid; min-width: 0; gap: .7rem; }.board-column { min-width: 0; }.activity-column :deep(.move-list) { max-height: 18rem; }.follow-live { display: inline-flex; align-items: center; gap: .4rem; margin-bottom: .5rem; padding: .4rem .7rem; border: 1px solid var(--color-border); border-radius: .35rem; background: var(--color-surface); color: var(--color-text); font: inherit; font-size: .72rem; cursor: pointer; }.arena-pending { display: grid; grid-template-columns: minmax(0, 1fr) 20rem; gap: 1rem; min-height: 16rem; }.duck-error { color: var(--color-danger); font-size: .8rem; }
.data-tabs { display: flex; gap: 1rem; margin-bottom: .7rem; border-bottom: 1px solid var(--color-border); }.data-tabs button { padding: .6rem 0; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--color-text-muted); font: inherit; font-size: .8rem; font-weight: 700; cursor: pointer; }.data-tabs button[aria-pressed='true'] { border-bottom-color: var(--color-accent); color: var(--color-accent); }.data-tabs span { margin-left: .3rem; font-size: .64rem; opacity: .75; }.data-panel { overflow: hidden; border: 1px solid var(--color-border); border-radius: var(--radius-lg, .75rem); background: var(--color-surface); }.table-wrap { overflow-x: auto; }.data-panel table { width: 100%; border-collapse: collapse; font-size: .8rem; }.data-panel th, .data-panel td { padding: .72rem .85rem; border-bottom: 1px solid var(--color-border); text-align: start; white-space: nowrap; }.data-panel th { color: var(--color-text-muted); font-size: .63rem; letter-spacing: .04em; text-transform: uppercase; }.data-panel tbody tr:last-child td { border-bottom: 0; }.data-panel td button { display: inline-flex; align-items: center; gap: .3rem; padding: 0; border: 0; background: transparent; color: var(--color-accent); font: inherit; font-size: .72rem; font-weight: 700; cursor: pointer; }.settings-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr)); gap: 1px; margin: 0; background: var(--color-border); }.settings-list div { padding: .85rem 1rem; background: var(--color-surface); }.settings-list dt { color: var(--color-text-muted); font-size: .62rem; font-weight: 750; text-transform: uppercase; }.settings-list dd { margin: .22rem 0 0; font-size: .8rem; font-weight: 650; }.duck-event a:focus-visible, .duck-event button:focus-visible { outline: 3px solid var(--color-focus, #b8c98b); outline-offset: 4px; }
@media (max-width: 75rem) { .duck-status-card { min-width: 11rem; }.arena { grid-template-columns: minmax(14rem, 1fr) minmax(20rem, 1.3fr); }.activity-column { grid-column: 1 / -1; grid-template-columns: 1fr 1fr; } }
@media (max-width: 60rem) { .duck-hero__content { padding-bottom: 8rem; }.duck-node { width: 13rem; }.duck-orbit--2 .duck-node--1 { left: 3%; }.duck-orbit--2 .duck-node--2 { right: 3%; }.duck-format { font-size: .68rem; }.duck-status-card { min-width: 0; bottom: 1.25rem; padding: .7rem; }.arena { grid-template-columns: 1fr; }.board-column { grid-row: 1; }.engine-column, .activity-column { grid-column: 1; grid-template-columns: 1fr 1fr; }.arena-pending { grid-template-columns: 1fr; } }
@media (max-width: 38rem) { .duck-heading h1 { font-size: clamp(2.7rem, 12vw, 4.2rem); }.duck-orbit { height: 16rem; }.duck-node { width: 40%; }.duck-orbit--2 .duck-node--1 { left: 0; }.duck-orbit--2 .duck-node--2 { right: 0; }.duck-node__card { min-width: 0; min-height: 8rem; padding-inline: .35rem; }.duck-node__card strong { font-size: .9rem; }.duck-node__card > span { font-size: .55rem; }.duck-vs { width: 2.6rem; height: 2.6rem; font-size: .6rem; }.duck-node__beacon > span { width: 4rem; height: 4rem; }.duck-format { gap: .4rem; }.duck-status-card { gap: .5rem; }.duck-status-card small { font-size: .5rem; }.duck-status-card strong { font-size: .7rem; }.duck-status-card > i { display: none; }.engine-column, .activity-column { grid-template-columns: 1fr; }.tournament-heading { align-items: start; flex-direction: column; } }
</style>
