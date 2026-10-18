<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api } from "@/api/client";
import duck from "@/assets/duck.svg";
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
const error = ref("");
const busy = ref(false);
const localMessages = ref<ChatMessage[]>([]);
const messages = computed(() => [...props.detail.chat_messages, ...localMessages.value.filter(message => !props.detail.chat_messages.some(existing => existing.id === message.id))]);
const live = computed(() => ["live", "intermission"].includes(props.detail.event.status));
const complete = computed(() => ["completed", "cancelled"].includes(props.detail.event.status));
const played = computed(() => games.value.filter(game => game.status === "finished" && ["1-0", "0-1", "1/2-1/2"].includes(game.result ?? "")));
const roster = computed(() => props.detail.cast.filter(member => member.parent_id === null));
const timing = computed(() => props.detail.event.scheduled_start_at
  ? new Intl.DateTimeFormat(undefined, { dateStyle: "long", timeStyle: "short" }).format(new Date(props.detail.event.scheduled_start_at))
  : "Start date TBD");
const gameReady = computed(() => viewer.value?.viewer_game && ["live", "assigned", "finished"].includes(viewer.value.viewer_game.status));
let request: AbortController | null = null;
const timer = window.setInterval(() => { if (live.value && !document.hidden) void loadViewer(); }, 3000);

watch(() => payload.value.games, value => { games.value = value; }, { immediate: true });
watch(() => [tournament.value?.id, props.detail.event.revision], () => { void loadViewer(); }, { immediate: true });
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
    viewer.value = response;
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
  <div class="pond-event">
    <header class="pond-hero">
      <PondScene />
      <div class="pond-hero__shade"></div>
      <div class="pond-hero__content">
        <nav><RouterLink to="/events">← All events</RouterLink><span class="pond-status" :class="{ 'pond-status--live': live }"><i></i>{{ live ? detail.event.status === 'intermission' ? 'Intermission' : 'Live at the pond' : complete ? detail.event.status === 'cancelled' ? 'Event cancelled' : 'Match complete' : 'Coming to the pond' }}</span></nav>
        <div class="pond-title"><span class="eyebrow">A COPE Chess special event</span><h1>Duck <em>vs</em><br />Fish<span>.</span></h1><p>{{ detail.event.subtitle }}</p><div class="pond-hero__date"><span aria-hidden="true">◷</span>{{ timing }}</div></div>
        <dl class="pond-facts"><div><dt>The game</dt><dd>Duck Chess</dd></div><div><dt>The clock</dt><dd>20 min + 12 sec</dd></div><div><dt>The power</dt><dd>64 threads <small>/ engine</small></dd></div><div><dt>The match</dt><dd>20 games</dd></div></dl>
      </div>
    </header>

    <main class="pond-content">
      <section class="pond-matchup" aria-label="Match score">
        <div v-for="(member, index) in roster" :key="member.id" class="pond-player" :class="{ 'pond-player--fish': index === 1 }">
          <div class="pond-avatar"><img v-if="index === 0" :src="duck" alt="" /><svg v-else viewBox="0 0 100 64" aria-hidden="true"><path d="M8 32Q30 0 66 26L92 8 84 32 92 56 66 38Q30 64 8 32Z" fill="#78a89b" stroke="#365e53" stroke-width="2" /><path d="M38 15 48 4 59 20M38 49 48 60 59 44" fill="#78a89b" /><circle cx="23" cy="28" r="3" fill="#243e34" /></svg></div>
          <div><span class="eyebrow">{{ index === 0 ? 'The duck' : 'The fish' }}</span><h2>{{ member.display_name }}</h2><p>{{ member.profile }}</p></div>
          <strong class="pond-score">{{ score(member.engine_version_id) }}</strong>
        </div>
        <div class="pond-versus"><span>vs</span><small>{{ played.length }} / 20 played</small></div>
      </section>

      <div class="pond-body">
        <div class="pond-main">
          <section v-if="gameReady" class="pond-board" aria-label="Match board">
            <header><span class="eyebrow">{{ viewer?.viewer_game?.status === 'finished' ? 'Game replay' : 'On the board' }}</span><button v-if="selected && live" type="button" @click="selected = null; loadViewer()">Follow live ↗</button></header>
            <div class="pond-board__players">{{ name(viewer!.viewer_game!.white_engine_id) }} <span>vs</span> {{ name(viewer!.viewer_game!.black_engine_id) }}<strong>{{ viewer?.viewer_game?.result || 'In play' }}</strong></div>
            <ChessViewer :key="viewer?.viewer_game?.id ?? 'pond'" variant="duck" :opening="viewer?.opening ?? null" :moves="viewer?.viewer_moves ?? []" label="Duck vs Fish match board" />
          </section>
          <section v-else class="pond-welcome"><span class="eyebrow">{{ complete ? 'Back on shore' : 'Still waters. Deep calculation.' }}</span><h2>{{ complete ? 'A match for the pond.' : 'Something is stirring at the pond.' }}</h2><p>{{ detail.event.description }}</p><div class="pond-wait"><span aria-hidden="true">≈</span><div><strong>{{ complete ? 'Explore the games below' : timing }}</strong><span>{{ complete ? 'Select a finished game to watch it again.' : 'Pull up a lily pad. The match begins here.' }}</span></div></div></section>
          <p v-if="error" role="alert" class="pond-error">{{ error }}</p>

          <section class="pond-programme"><header><div><span class="eyebrow">Ten openings. Both colours.</span><h2>The match</h2></div><span>{{ played.length }} of 20 games</span></header>
            <div class="pond-game-grid"><button v-for="(game, index) in games" :key="game.id" type="button" :disabled="!['live', 'assigned', 'finished'].includes(game.status)" :class="{ selected: selected === String(game.id), 'is-live': ['live', 'assigned'].includes(game.status) }" @click="loadViewer(String(game.id))"><span class="pond-game-number">{{ String(index + 1).padStart(2, '0') }}</span><span><strong>Pair {{ Math.floor(index / 2) + 1 }}</strong><small>{{ name(game.white_engine_id) }} as White</small></span><b>{{ game.status === 'finished' ? game.result || 'Finished' : ['live', 'assigned'].includes(game.status) ? 'Live ↗' : 'TBD' }}</b></button></div>
          </section>

          <section class="pond-rules"><span class="eyebrow">The lay of the pond</span><h2>A little more depth.</h2><p>{{ detail.event.rules }}</p><div class="pond-book"><span>Opening book</span><strong>{{ payload.opening_book }}</strong></div></section>
        </div>
        <aside class="pond-sidebar"><div class="pond-note"><span class="eyebrow">Meet the contenders</span><h3>Home water.<br />New territory.</h3><p>Wakwak and Fairy-Stockfish go head to head in Duck Chess. One neutral duck, two kings, and twenty games to settle the score.</p><a href="https://wakwak.live/" target="_blank" rel="noopener noreferrer">Visit Wakwak’s pond ↗</a></div><ChatPanel :messages="messages" :settings="detail.chat_settings" :event-slug="detail.event.slug" @sent="localMessages.push($event)" /></aside>
      </div>
      <footer class="pond-footer"><span>Duck vs Fish</span><span>A little ripple in the world of computer chess.</span><RouterLink to="/events">Explore COPE events ↗</RouterLink></footer>
    </main>
  </div>
</template>

<style scoped>
.pond-event { --pond-ink: #243e34; --pond-muted: #5e7260; --pond-paper: #f1ecd9; --pond-line: #2d514328; margin-block: calc(0px - clamp(var(--space-6), 4vw, var(--space-12))) -3rem; color: var(--pond-ink); background: var(--pond-paper); }
.pond-title h1 { color: #f4ecd3; }.pond-avatar svg { width: 3rem; }
.pond-status { background: #163f3de6; }
.pond-hero { position: relative; isolation: isolate; overflow: hidden; color: #f4ecd3; background: #163f3d; }.pond-hero__shade { position: absolute; inset: 0; background: linear-gradient(90deg, #163f3de8, #214c3e70 50%, transparent 80%); pointer-events: none; }
.pond-hero__content { position: relative; max-width: 90rem; margin: auto; padding: 1.8rem clamp(1.5rem, 6vw, 6rem) 0; }.pond-hero nav { display: flex; justify-content: space-between; align-items: center; gap: 1rem; font-size: .75rem; }.pond-hero nav a { color: #dce2c8; text-decoration: none; }.pond-status { display: flex; align-items: center; gap: .5rem; padding: .45rem .7rem; border: 1px solid #c1d5a64d; border-radius: 2rem; color: #d4dfbd; font-size: .65rem; }.pond-status i { width: .4rem; height: .4rem; background: #edc653; border-radius: 50%; }.pond-status--live i { background: #b8ef9e; box-shadow: 0 0 8px #b8ef9e; }
.eyebrow { font: 700 .65rem/1.5 ui-monospace, monospace; letter-spacing: .12em; text-transform: uppercase; }.pond-title { position: relative; padding: clamp(3rem, 6vw, 5.5rem) 0 3rem; }.pond-title > .eyebrow { color: #bcd3ac; }h1 { margin: 1.3rem 0; font: 850 clamp(5.5rem, 11vw, 10.5rem)/.84 var(--font-sans, sans-serif); letter-spacing: -.07em; }h1 em { color: #bfd6b0; font: italic 400 .48em Georgia, serif; letter-spacing: -.08em; }h1 > span { color: #edc653; }.pond-title p { margin: 1.75rem 0 1.25rem; color: #e3dfbc; font-size: clamp(1rem, 1.7vw, 1.25rem); }.pond-hero__date { display: flex; gap: .65rem; align-items: center; color: #edc653; font-weight: 650; font-size: .85rem; }.pond-hero__date > span { font-size: 1.25rem; }
.pond-facts { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1.5rem; margin: 0; padding: 1.5rem 0; border-top: 1px solid #cfdeb04d; }.pond-facts dt { margin-bottom: .4rem; color: #b4cab0; font: .65rem ui-monospace, monospace; text-transform: uppercase; letter-spacing: .1em; }.pond-facts dd { margin: 0; font-size: clamp(.85rem, 1.6vw, 1.2rem); font-weight: 650; }.pond-facts small { font-size: .65rem; font-weight: 400; color: #bfd0b8; }
.pond-content { max-width: 90rem; margin: auto; padding: 0 clamp(1rem, 5vw, 5rem); }.pond-matchup { position: relative; display: grid; grid-template-columns: 1fr 1fr; gap: clamp(2rem, 5vw, 5rem); padding: 2.5rem 0; border-bottom: 1px solid var(--pond-line); }.pond-player { display: flex; align-items: center; gap: 1rem; min-width: 0; }.pond-avatar { display: grid; place-items: center; width: 4rem; height: 4rem; flex: 0 0 auto; border-radius: 50%; background: #e2dfbd; }.pond-avatar img { width: 3rem; }.pond-avatar span { color: #497e75; font-size: 3.2rem; }.pond-player .eyebrow { color: var(--pond-muted); }h2 { margin: .2rem 0 .35rem; font-size: clamp(1.5rem, 2.5vw, 2rem); letter-spacing: -.045em; }.pond-player p { margin: 0; color: var(--pond-muted); font-size: .7rem; overflow-wrap: anywhere; }.pond-score { margin-left: auto; font: 500 3rem/.9 Georgia, serif; }.pond-versus { position: absolute; top: 50%; left: 50%; display: grid; gap: .4rem; justify-items: center; transform: translate(-50%, -50%); }.pond-versus > span { color: #86917a; font: italic 1.4rem Georgia, serif; }.pond-versus small { color: var(--pond-muted); font-size: .55rem; white-space: nowrap; }
.pond-body { display: grid; grid-template-columns: minmax(0, 1fr) 17rem; gap: clamp(1.5rem, 4vw, 4rem); padding: 2.5rem 0; }.pond-main { min-width: 0; }.pond-welcome { padding: 1.5rem 0 2.5rem; }.pond-welcome .eyebrow, .pond-rules .eyebrow, .pond-programme .eyebrow { color: #6d805e; }.pond-welcome h2 { max-width: 16ch; margin: .7rem 0 1rem; font: 500 clamp(2.5rem, 4vw, 3.8rem)/1.07 Georgia, serif; letter-spacing: -.045em; }.pond-welcome p, .pond-rules p { max-width: 65ch; color: var(--pond-muted); font-size: .88rem; line-height: 1.85; }.pond-wait { display: flex; gap: 1rem; align-items: center; margin-top: 1.8rem; padding: 1rem 1.2rem; border: 1px solid var(--pond-line); background: #e5e7cf; border-radius: .5rem; }.pond-wait > span { color: #658266; font: 3rem/1 Georgia, serif; }.pond-wait > div { display: grid; gap: .3rem; font-size: .82rem; }.pond-wait > div > span { color: var(--pond-muted); font-size: .72rem; }
.pond-programme { border-top: 1px solid var(--pond-line); padding-top: 1.8rem; }.pond-programme header, .pond-board header { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin-bottom: 1.2rem; }.pond-programme header > span { color: var(--pond-muted); font-size: .7rem; }.pond-game-grid { display: grid; grid-template-columns: 1fr 1fr; gap: .5rem; }.pond-game-grid button { display: flex; align-items: center; gap: .7rem; width: 100%; padding: .85rem; border: 1px solid var(--pond-line); border-radius: .35rem; background: #f8f4e7; color: var(--pond-ink); text-align: left; cursor: pointer; }.pond-game-grid button:disabled { cursor: default; opacity: 1; background: transparent; }.pond-game-grid button:not(:disabled):hover, .pond-game-grid button.selected { border-color: #658266; background: #e4e7ce; }.pond-game-number { color: #829078; font: .75rem ui-monospace, monospace; }.pond-game-grid button > span:nth-child(2) { display: grid; gap: .25rem; }.pond-game-grid strong { font-size: .72rem; }.pond-game-grid small { color: var(--pond-muted); font-size: .6rem; }.pond-game-grid b { margin-left: auto; color: var(--pond-muted); font: .65rem ui-monospace, monospace; }.pond-game-grid .is-live b { color: #277844; }
.pond-rules { margin-top: 2rem; padding-top: 1.8rem; border-top: 1px solid var(--pond-line); }.pond-rules h2 { margin-top: .5rem; }.pond-book { display: flex; justify-content: space-between; gap: 1rem; margin-top: 1.4rem; padding: .8rem 0; border-block: 1px solid var(--pond-line); font-size: .75rem; }.pond-book > span { color: var(--pond-muted); }.pond-sidebar { display: flex; flex-direction: column; gap: 1.5rem; }.pond-note { padding: 1.5rem; background: #dae0c4; border-radius: .45rem; }.pond-note .eyebrow { color: #607650; }.pond-note h3 { margin: .7rem 0; font: 500 2rem/1.1 Georgia, serif; letter-spacing: -.04em; }.pond-note p { color: #5e7059; font-size: .75rem; line-height: 1.8; }.pond-note a { color: #2f5b43; font-size: .72rem; font-weight: 700; text-underline-offset: .3em; }.pond-sidebar :deep(.chat-panel) { --color-surface: #f8f4e7; --color-text: #243e34; --color-text-muted: #5e7260; --color-border: #2d514328; --color-surface-raised: #f8f4e7; }
.pond-board { margin-bottom: 2rem; padding: 1.2rem; background: #e0e3cc; border-radius: .5rem; }.pond-board header { margin-bottom: .5rem; }.pond-board button { border: 0; background: transparent; color: #315c45; font: inherit; font-size: .7rem; cursor: pointer; }.pond-board__players { display: flex; align-items: center; gap: .5rem; padding: .5rem 0 1rem; font-weight: 650; font-size: .8rem; }.pond-board__players span { color: #7b8871; font: italic .9rem Georgia, serif; }.pond-board__players strong { margin-left: auto; font-size: .7rem; }.pond-error { color: #994721; font-size: .8rem; }
.pond-footer { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 1rem; padding: 1.5rem 0 2rem; border-top: 1px solid var(--pond-line); color: var(--pond-muted); font-size: .65rem; }.pond-footer > span:first-child { color: var(--pond-ink); font-weight: 800; }.pond-footer a { color: var(--pond-ink); text-decoration: none; }.pond-event button:focus-visible, .pond-event a:focus-visible { outline: 3px solid #bd982e; outline-offset: 4px; }
@media (max-width: 65rem) { .pond-body { grid-template-columns: minmax(0, 1fr) 14rem; gap: 1.5rem; }.pond-avatar { width: 3rem; height: 3rem; }.pond-avatar img { width: 2.5rem; }.pond-player { gap: .6rem; }.pond-player h2 { font-size: 1.3rem; }.pond-score { font-size: 2.2rem; } }
@media (max-width: 48rem) { .pond-body { grid-template-columns: 1fr; }.pond-sidebar { display: grid; grid-template-columns: 1fr 1fr; }.pond-facts { grid-template-columns: 1fr 1fr; gap: 1.25rem; }.pond-title { padding-top: 5rem; }.pond-matchup { gap: 2.5rem; padding-block: 1.5rem; }.pond-avatar { display: none; }.pond-player h2 { font-size: 1.25rem; }.pond-player p { font-size: .6rem; }.pond-versus small { display: none; }.pond-game-grid { gap: .4rem; }.pond-game-grid button { gap: .5rem; padding: .65rem; } }
@media (max-width: 32rem) { .pond-sidebar { grid-template-columns: 1fr; }.pond-player { flex-wrap: wrap; }.pond-score { font-size: 1.8rem; }.pond-hero nav { font-size: .65rem; }.pond-game-grid { grid-template-columns: 1fr; } }
</style>
