<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";

import AppIcon from "@/components/ui/AppIcon.vue";
import type { EventSummary } from "@/types/events";

const props = defineProps<{ summary: EventSummary; current?: boolean; nowMs: number }>();

const event = computed(() => props.summary.record);
const isLive = computed(() => ["live", "intermission"].includes(event.value.status));
const eventStyle = computed(() => ({
  "--archive-primary": themeColor("primary", "#8b5cf6"),
  "--archive-accent": themeColor("accent", "#22d3ee"),
  "--archive-background": themeColor("background", "#090713"),
  "--archive-surface": themeColor("surface", "#151126"),
  "--archive-text": themeColor("text", "#f8f4ff"),
}));
const target = computed(() => props.current
  ? { name: "event", params: { slug: event.value.slug } }
  : { name: "event-arena", params: { slug: event.value.slug } });
const remainingMs = computed(() => {
  const start = event.value.scheduled_start_at ? Date.parse(event.value.scheduled_start_at) : Number.NaN;
  return Number.isFinite(start) ? Math.max(0, start - props.nowMs) : 0;
});
const countdown = computed(() => {
  const seconds = Math.ceil(remainingMs.value / 1000);
  return [
    { label: "days", value: Math.floor(seconds / 86400) },
    { label: "hours", value: Math.floor((seconds % 86400) / 3600) },
    { label: "mins", value: Math.floor((seconds % 3600) / 60) },
    { label: "secs", value: seconds % 60 },
  ];
});
const dateLabel = computed(() => {
  const value = event.value.finished_at ?? event.value.scheduled_start_at;
  if (!value) return "Date unavailable";
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "long", year: "numeric" }).format(new Date(value));
});

function themeColor(key: string, fallback: string): string {
  const value = event.value.theme[key];
  return typeof value === "string" && /^#[0-9a-f]{3,8}$/i.test(value) ? value : fallback;
}
</script>

<template>
  <RouterLink class="gauntlet-archive" :class="{ 'gauntlet-archive--current': current }" :style="eventStyle" :to="target" :aria-label="`${current ? 'Open' : 'View'} ${event.title}`">
    <div class="puzzle-field" aria-hidden="true"></div>
    <div class="gauntlet-mark" aria-hidden="true"><span></span><i></i><b>G</b></div>
    <div class="gauntlet-copy">
      <span class="gauntlet-kicker"><AppIcon name="trophy" :size="16" />{{ current ? isLive ? "Gauntlet live" : "Next challenge" : "Puzzle archive" }}</span>
      <h2>{{ event.title }}</h2>
      <p>{{ event.summary || event.subtitle }}</p>
      <div v-if="current && remainingMs > 0" class="gauntlet-countdown" aria-label="Time until event">
        <span v-for="part in countdown" :key="part.label"><strong>{{ String(part.value).padStart(2, "0") }}</strong><small>{{ part.label }}</small></span>
      </div>
      <div class="gauntlet-meta">
        <span>{{ current && isLive ? "Engines are solving now" : dateLabel }}</span>
        <strong>{{ current ? "Open event" : "Inspect the gauntlet" }} <AppIcon name="arrow-right" :size="18" /></strong>
      </div>
    </div>
    <div class="survivor-stack" aria-hidden="true">
      <span v-for="index in 7" :key="index" :class="{ out: index > 4 }"><i>{{ String(index).padStart(2, "0") }}</i><b></b><small>{{ index > 4 ? "OUT" : "ACTIVE" }}</small></span>
    </div>
  </RouterLink>
</template>

<style scoped>
.gauntlet-archive { position: relative; isolation: isolate; display: grid; min-height: clamp(30rem, 72vh, 46rem); overflow: hidden; align-items: end; padding: clamp(2rem, 6vw, 6.5rem); background: radial-gradient(circle at 78% 45%, color-mix(in srgb, var(--archive-primary) 27%, transparent), transparent 25%), radial-gradient(circle at 68% 67%, color-mix(in srgb, var(--archive-accent) 10%, transparent), transparent 23%), var(--archive-background); color: var(--archive-text); text-decoration: none; }
.gauntlet-archive::after { position: absolute; z-index: 5; inset: 0; border: 1px solid rgb(167 139 250 / 24%); content: ""; pointer-events: none; }
.puzzle-field { position: absolute; z-index: -1; inset: 0; background-image: radial-gradient(circle, rgb(167 139 250 / 18%) 1px, transparent 1.5px); background-size: 2rem 2rem; mask-image: linear-gradient(90deg, black, transparent 75%); opacity: .75; }
.gauntlet-copy { position: relative; z-index: 2; width: min(55rem, 72%); }
.gauntlet-kicker { display: inline-flex; align-items: center; gap: .55rem; color: var(--archive-primary); font-size: .72rem; font-weight: 850; letter-spacing: .17em; text-transform: uppercase; }
.gauntlet-copy h2 { max-width: 10ch; margin: .8rem 0 0; font-size: clamp(3.5rem, 8.5vw, 8.2rem); font-weight: 900; letter-spacing: -.075em; line-height: .84; text-transform: uppercase; text-wrap: balance; }
.gauntlet-copy h2::first-line { color: #f8f7ff; }
.gauntlet-copy > p { max-width: 48rem; margin: 1.4rem 0 0; color: #aaa5b9; font-size: clamp(.86rem, 1.35vw, 1.05rem); line-height: 1.65; }
.gauntlet-countdown { display: flex; width: fit-content; margin-top: 1.8rem; overflow: hidden; border: 1px solid rgb(167 139 250 / 28%); border-radius: .7rem; background: rgb(14 10 27 / 72%); box-shadow: 0 1rem 3rem rgb(0 0 0 / 25%); }
.gauntlet-countdown span { display: grid; min-width: clamp(4.1rem, 7vw, 6.2rem); padding: .7rem .9rem; border-left: 1px solid rgb(167 139 250 / 17%); }
.gauntlet-countdown span:first-child { border-left: 0; }
.gauntlet-countdown strong { font: 750 clamp(1.25rem, 2.4vw, 2rem)/1 ui-monospace, monospace; font-variant-numeric: tabular-nums; }
.gauntlet-countdown small { margin-top: .3rem; color: #8375a9; font-size: .52rem; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }
.gauntlet-meta { display: flex; align-items: center; gap: 1.2rem; margin-top: 1.7rem; color: #777183; font-size: .7rem; font-weight: 680; }
.gauntlet-meta strong { display: inline-flex; align-items: center; gap: .4rem; color: var(--archive-primary); }
.gauntlet-mark { position: absolute; top: 50%; right: clamp(2rem, 9vw, 10rem); width: clamp(18rem, 31vw, 33rem); aspect-ratio: 1; border: 1px solid rgb(167 139 250 / 25%); transform: translateY(-50%) rotate(45deg); }
.gauntlet-mark span, .gauntlet-mark i { position: absolute; inset: 12%; border: 1px solid rgb(34 211 238 / 15%); }
.gauntlet-mark i { inset: 27%; border-color: rgb(167 139 250 / 27%); }
.gauntlet-mark b { position: absolute; inset: 0; display: grid; place-items: center; color: #b8a3f6; font: 900 clamp(6rem, 13vw, 12rem)/1 ui-monospace, monospace; text-shadow: 0 0 3rem rgb(139 92 246 / 55%); transform: rotate(-45deg); }
.survivor-stack { position: absolute; right: 2.5%; bottom: 7%; display: grid; width: min(27rem, 34vw); gap: .3rem; }
.survivor-stack span { display: grid; grid-template-columns: 2rem minmax(0, 1fr) auto; align-items: center; gap: .5rem; color: #ada1c8; font: 700 .55rem/1 ui-monospace, monospace; }
.survivor-stack span > b { height: 2px; background: linear-gradient(90deg, #a78bfa, #22d3ee); box-shadow: 0 0 .7rem rgb(167 139 250 / 35%); }
.survivor-stack span > small { width: 3.2rem; color: #22d3ee; font-size: .48rem; }
.survivor-stack span.out { opacity: .34; }
.survivor-stack span.out > b { background: #5e5968; box-shadow: none; }
.survivor-stack span.out > small { color: #7f7989; }
.gauntlet-archive:hover .gauntlet-mark { transform: translateY(-50%) rotate(47deg) scale(1.02); transition: transform 350ms ease; }
.gauntlet-archive:hover .gauntlet-meta strong { text-decoration: underline; text-underline-offset: .25rem; }
.gauntlet-archive:focus-visible { outline: 3px solid #a78bfa; outline-offset: -3px; }
@media (max-width: 58rem) { .gauntlet-archive { min-height: 31rem; padding: 2rem 1.25rem; }.gauntlet-copy { width: 100%; }.gauntlet-copy h2 { max-width: 11ch; }.gauntlet-copy > p { max-width: 34rem; }.gauntlet-mark { top: 34%; right: -6rem; opacity: .55; }.survivor-stack { display: none; }.gauntlet-countdown span { min-width: 3.5rem; padding-inline: .55rem; } }
</style>
