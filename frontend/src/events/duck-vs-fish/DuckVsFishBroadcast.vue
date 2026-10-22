<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import wakwak from "@/assets/wakwak-event.png";
import AppIcon from "@/components/ui/AppIcon.vue";
import { useCountdownMusic } from "@/composables/useCountdownMusic";
import fairyStockfish from "@/assets/fairy-stockfish-event.png";
import EngineCheers from "./EngineCheers.vue";
import type { EventDetailResponse } from "@/types/events";
import DuckVsFishArena from "./DuckVsFishArena.vue";
import PondScene from "./PondScene.vue";
import { pondRoster, type PondPayload } from "./types";

const props = defineProps<{ detail: EventDetailResponse; view?: "event" | "arena"; clockOffsetMs?: number }>();
const payload = computed(() => props.detail.custom as PondPayload);
const games = computed(() => payload.value.games);
const now = ref(Date.now());
const live = computed(() => ["live", "intermission"].includes(props.detail.event.status));
const played = computed(() => games.value.filter(game => game.status === "finished" && ["1-0", "0-1", "1/2-1/2"].includes(game.result ?? "")));
const roster = computed(() => pondRoster(props.detail.cast));
const timing = computed(() => props.detail.event.scheduled_start_at
  ? new Intl.DateTimeFormat(undefined, { dateStyle: "long", timeStyle: "short" }).format(new Date(props.detail.event.scheduled_start_at))
  : "Start date TBD");
const remaining = computed(() => Math.max(0, Date.parse(props.detail.event.scheduled_start_at ?? "") - now.value - (props.clockOffsetMs ?? 0)) || 0);
const countdownActive = computed(() => props.view !== "arena" && ["announced", "scheduled"].includes(props.detail.event.status) && remaining.value > 0);
const { label: musicLabel, state: musicState, eventMusicEnabled, toggle: toggleMusic } = useCountdownMusic(remaining, countdownActive);
const arenaOpen = computed(() => ["live", "intermission", "completed"].includes(props.detail.event.status));
const countdown = computed(() => {
  const seconds = Math.ceil(remaining.value / 1000);
  return [{ label: "days", value: Math.floor(seconds / 86400) }, { label: "hours", value: Math.floor(seconds % 86400 / 3600) }, { label: "mins", value: Math.floor(seconds % 3600 / 60) }, { label: "secs", value: seconds % 60 }];
});
const status = computed(() => ({ announced: "Announced", scheduled: "Scheduled", live: "Live", intermission: "Intermission", completed: "Completed", cancelled: "Cancelled", postponed: "Postponed", draft: "Draft" }[props.detail.event.status]));
const timer = window.setInterval(() => { now.value = Date.now(); }, 250);
onBeforeUnmount(() => { window.clearInterval(timer); });

function score(engineId: string | number | null): string {
  let total = 0;
  for (const game of played.value) {
    if (game.result === "1/2-1/2") total += .5;
    else if ((game.result === "1-0" && String(game.white_engine_id) === String(engineId)) || (game.result === "0-1" && String(game.black_engine_id) === String(engineId))) total += 1;
  }
  return String(total).replace(".5", "½").replace(/^0½$/, "½");
}

</script>

<template>
  <div class="duck-event" :class="{ 'duck-event--arena': view === 'arena' }">
    <section v-if="view !== 'arena'" class="duck-hero" aria-labelledby="duck-event-title">
      <PondScene />
      <div class="duck-hero__content">
        <header class="duck-heading"><span class="duck-kicker"><AppIcon name="trophy" :size="15" /> Duck Chess</span><h1 id="duck-event-title">{{ detail.event.title }}</h1></header>
        <div v-if="countdownActive" class="duck-countdown" aria-label="Time until event"><div v-for="part in countdown" :key="part.label"><strong>{{ String(part.value).padStart(2, '0') }}</strong><span>{{ part.label }}</span></div></div>
        <div class="duck-timing"><p class="duck-schedule"><AppIcon name="clock" :size="15" /> {{ timing }}</p><button v-if="countdownActive" class="duck-music" type="button" :aria-pressed="eventMusicEnabled" :title="musicState === 'unavailable' ? 'Audio could not load. Click to retry.' : 'Music plays during the final minute.'" @click="toggleMusic"><AppIcon :name="musicState === 'playing' ? 'pause' : 'play'" :size="14" />{{ musicLabel }}</button></div>
        <div class="duck-orbit duck-orbit--2">
          <span class="duck-vs" aria-hidden="true">VS</span>
          <article v-for="(member, index) in roster" :key="member.id" class="duck-node" :class="`duck-node--${index + 1}`">
            <div class="duck-node__beacon"><span><img v-if="index === 0" :src="wakwak" alt="" /><img v-else class="fish-mark" :src="fairyStockfish" alt="" /></span></div>
            <div class="duck-node__card" :data-cheer-origin="member.id"><strong>{{ member.display_name }}</strong><span>{{ member.profile }}</span><b v-if="played.length || live" class="duck-score">{{ score(member.engine_version_id) }}</b><small v-else>64 threads</small></div>
          </article>
        </div>
        <RouterLink v-if="arenaOpen" :to="{ name: 'event-arena', params: { slug: detail.event.slug } }" class="duck-primary-action"><AppIcon name="trophy" :size="18" /> View arena <AppIcon name="arrow-right" :size="18" /></RouterLink>
        <EngineCheers class="duck-hero-cheers" :event-id="detail.event.id" :slug="detail.event.slug" :members="roster" />
      </div>
      <footer class="duck-status"><aside class="duck-status-card duck-status-card--systems"><span class="duck-status-card__icon"><AppIcon name="activity" :size="25" /></span><div><small>Status</small><strong>{{ status }}</strong></div><i :class="{ active: live }"></i></aside>
      <aside class="duck-status-card duck-status-card--audience"><span class="duck-status-card__icon"><AppIcon name="user" :size="25" /></span><div><small>Current spectators</small><strong>{{ (detail.spectator_count ?? 0).toLocaleString() }}</strong></div></aside></footer>
    </section>
    <DuckVsFishArena v-else :detail="detail" />
  </div>
</template>

<style scoped>
.duck-event { margin-block: calc(0px - clamp(var(--space-6), 4vw, var(--space-12))) -3rem; }
.duck-event--arena { min-height: calc(100dvh - 2.85rem); margin: 0; background: var(--color-bg, #f3f6fa); }
.duck-hero { position: relative; isolation: isolate; display: grid; grid-template-rows: 1fr auto; min-height: calc(100dvh - var(--header-height, 4rem) - 1px); overflow: hidden; border-bottom: 1px solid #29443a; background: radial-gradient(ellipse at 50% 45%, #244c3c60, transparent 65%), #070f12; color: #eff3e9; }
.duck-hero__content { position: relative; z-index: 2; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: clamp(.65rem, 1.8dvh, 1.2rem); width: min(100%, 84rem); margin-inline: auto; padding: clamp(1rem, 2.5dvh, 2rem) 1.25rem; }
.duck-heading { display: grid; justify-items: center; gap: .6rem; text-align: center; }
.duck-kicker { display: inline-flex; align-items: center; gap: .45rem; padding: .35rem .85rem; border: 1px solid #b4c98d36; border-radius: 999px; background: #101d18bf; color: #b8cea1; font-size: .65rem; font-weight: 820; letter-spacing: .075em; text-transform: uppercase; }
.duck-heading h1 { margin: 0; color: #f0e5b5; font-size: clamp(2.5rem, 7.5dvh, 5.5rem); font-weight: 760; letter-spacing: -.065em; line-height: 1; }
.duck-countdown { display: flex; justify-content: center; width: min(100%, 38rem); }
.duck-countdown > div { display: grid; width: 25%; justify-items: center; gap: .3rem; }
.duck-countdown strong { color: #eee5bd; font-size: clamp(2.25rem, 6dvh, 4.25rem); font-weight: 760; font-variant-numeric: tabular-nums; letter-spacing: -.065em; line-height: 1; }
.duck-countdown span { color: #a6b88d; font-size: .6rem; font-weight: 830; letter-spacing: .17em; text-transform: uppercase; }
.duck-timing { display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: .55rem 1rem; }
.duck-schedule { display: flex; align-items: center; gap: .4rem; margin: 0; color: #8faaa0; font-size: .72rem; }
.duck-music { display: inline-flex; align-items: center; gap: .4rem; min-height: 2rem; padding: .25rem .65rem; border: 1px solid #b4c98d36; border-radius: 999px; background: #101d18bf; color: #b8cea1; font: inherit; font-size: .7rem; cursor: pointer; }
.duck-music:hover { border-color: #b8cea1; }
.duck-orbit { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); align-items: center; gap: clamp(.75rem, 5vw, 6rem); width: min(100%, 52rem); margin-top: .5rem; }
.duck-vs { grid-column: 2; grid-row: 1; display: grid; width: 3rem; height: 3rem; place-items: center; border: 1px solid #a5bf8e38; border-radius: 50%; background: #0c1716; color: #b6c69b; font-size: .7rem; font-weight: 850; letter-spacing: .1em; }
.duck-node { --node-color: #d9ba51; display: grid; justify-items: center; min-width: 0; }
.duck-node--1 { grid-column: 1; grid-row: 1; }
.duck-node--2 { --node-color: #85b8af; grid-column: 3; grid-row: 1; }
.duck-node__beacon { position: relative; z-index: 1; width: 4.5rem; height: 4.5rem; }
.duck-node__beacon > span { display: grid; width: 100%; height: 100%; place-items: center; border: 1px solid color-mix(in srgb, var(--node-color) 50%, transparent); border-radius: 50%; background: #10231e; box-shadow: 0 0 2rem color-mix(in srgb, var(--node-color) 14%, transparent); }
.duck-node__beacon img { width: 4.25rem; height: 4.25rem; object-fit: contain; }
.duck-node__card { display: grid; justify-items: center; align-content: center; gap: .35rem; width: 100%; min-height: 6.5rem; margin-top: -1rem; padding: 1.4rem .65rem .7rem; border: 1px solid color-mix(in srgb, var(--node-color) 23%, transparent); border-radius: .75rem; background: linear-gradient(#192e2480, #0d1b18d9); }
.duck-node__card strong { max-width: 100%; color: #f0f1df; font-size: 1rem; }
.duck-node__card > span { max-width: 100%; color: #81988c; font-size: .62rem; text-align: center; overflow-wrap: anywhere; }
.duck-node__card small { color: var(--node-color); font-size: .6rem; font-weight: 720; }
.duck-score { color: var(--node-color); font-size: 1.5rem; font-variant-numeric: tabular-nums; }
.duck-primary-action { display: inline-flex; min-height: 3rem; align-items: center; justify-content: center; gap: .75rem; padding: 0 1.8rem; border: 1px solid #b7b78033; border-radius: .65rem; background: linear-gradient(135deg, #365b3d, #4c7144); color: #f3eed5; font-size: .95rem; font-weight: 730; text-decoration: none; }
.duck-primary-action:hover { background: #507747; }
.duck-hero-cheers { --color-surface: #102019; --color-text: #e1e8d8; --color-text-muted: #8a9e90; }
.duck-status { position: relative; z-index: 2; display: flex; justify-content: space-between; gap: .75rem; padding: 0 clamp(1rem, 3vw, 3rem) 1rem; }
.duck-status-card { display: flex; align-items: center; gap: .65rem; padding: .65rem .85rem; border: 1px solid #a9be8e29; border-radius: .75rem; background: #102019c9; }
.duck-status-card__icon { display: grid; width: 2.3rem; height: 2.3rem; place-items: center; border-radius: .65rem; background: #b7c98b0d; color: #a5bc94; }
.duck-status-card > div { display: grid; gap: .15rem; }
.duck-status-card small { color: #758f7f; font-size: .55rem; font-weight: 760; letter-spacing: .06em; text-transform: uppercase; }
.duck-status-card strong { color: #d9e3ce; font-size: .75rem; }
.duck-status-card > i { width: .4rem; height: .4rem; border-radius: 50%; background: #5b7365; }
.duck-status-card > i.active { background: #a1ce86; }
.duck-event a:focus-visible, .duck-event button:focus-visible { outline: 3px solid #b8c98b; outline-offset: 4px; }
@media (max-width: 38rem) { .duck-heading h1 { font-size: clamp(2.25rem, 10vw, 3.5rem); }.duck-orbit { gap: .65rem; }.duck-vs { width: 2rem; height: 2rem; font-size: .6rem; }.duck-node__card strong { font-size: .85rem; }.duck-node__card > span { font-size: .55rem; }.duck-status { padding-inline: .75rem; }.duck-status-card { gap: .4rem; padding: .5rem; }.duck-status-card__icon { width: 1.75rem; height: 1.75rem; }.duck-status-card small { font-size: .48rem; }.duck-status-card strong { font-size: .68rem; } }
</style>
