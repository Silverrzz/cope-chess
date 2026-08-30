<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";

import AppIcon from "@/components/ui/AppIcon.vue";
import type { EventSummary } from "@/types/events";

const props = defineProps<{ summary: EventSummary; current?: boolean; nowMs: number }>();

const event = computed(() => props.summary.record);
const isFinale = computed(() => event.value.handler_key === "engine-relay-finale");
const isLive = computed(() => ["live", "intermission"].includes(event.value.status));
const eventStyle = computed(() => ({
  "--archive-primary": themeColor("primary", isFinale.value ? "#d5a72d" : "#f97316"),
  "--archive-accent": themeColor("accent", isFinale.value ? "#f5cf62" : "#22d3ee"),
  "--archive-background": themeColor("background", isFinale.value ? "#100c02" : "#07111f"),
  "--archive-surface": themeColor("surface", isFinale.value ? "#211804" : "#0e1d2e"),
  "--archive-text": themeColor("text", isFinale.value ? "#fff8d8" : "#f8fafc"),
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
  <RouterLink class="relay-archive" :class="{ 'relay-archive--finale': isFinale, 'relay-archive--current': current }" :style="eventStyle" :to="target" :aria-label="`${current ? 'Open' : 'View'} ${event.title}`">
    <div class="relay-grid" aria-hidden="true"></div>
    <div class="relay-orbit" aria-hidden="true"><i></i><i></i><i></i><span>&#9818;</span></div>
    <span v-if="isFinale" class="finale-crown" aria-hidden="true">&#9812;</span>
    <div class="relay-copy">
      <span class="relay-kicker"><AppIcon :name="isFinale ? 'trophy' : 'activity'" :size="16" />{{ current ? isLive ? "Live" : "Upcoming" : "Completed" }}</span>
      <h2>{{ event.title }}</h2>
      <div v-if="current && remainingMs > 0" class="relay-countdown" aria-label="Time until event">
        <span v-for="part in countdown" :key="part.label"><strong>{{ String(part.value).padStart(2, "0") }}</strong><small>{{ part.label }}</small></span>
      </div>
      <div class="relay-meta">
        <span>{{ current && isLive ? "Live now" : dateLabel }}</span>
        <strong>{{ current ? "View event" : "View arena" }} <AppIcon name="arrow-right" :size="18" /></strong>
      </div>
    </div>
    <div class="relay-benches" aria-hidden="true">
      <span><i></i><i></i><i></i><i></i></span>
      <b>VS</b>
      <span><i></i><i></i><i></i><i></i></span>
    </div>
  </RouterLink>
</template>

<style scoped>
.relay-archive { --relay-blue: var(--archive-primary); position: relative; isolation: isolate; display: grid; min-height: clamp(30rem, 72vh, 46rem); overflow: hidden; align-items: end; padding: clamp(2rem, 6vw, 6.5rem); background: radial-gradient(circle at 77% 46%, color-mix(in srgb, var(--archive-accent) 23%, transparent), transparent 24%), linear-gradient(125deg, color-mix(in srgb, var(--archive-background) 92%, black) 5%, var(--archive-surface) 51%, var(--archive-background)); color: var(--archive-text); text-decoration: none; }
.relay-archive::after { position: absolute; z-index: 5; inset: 0; border: 1px solid rgb(110 166 255 / 20%); content: ""; pointer-events: none; }
.relay-grid { position: absolute; z-index: -1; inset: 0; background-image: linear-gradient(rgb(82 140 230 / 8%) 1px, transparent 1px), linear-gradient(90deg, rgb(82 140 230 / 8%) 1px, transparent 1px); background-size: 4rem 4rem; mask-image: linear-gradient(90deg, black, transparent 78%); }
.relay-copy { position: relative; z-index: 2; width: min(55rem, 72%); }
.relay-kicker { display: inline-flex; align-items: center; gap: .55rem; color: var(--archive-accent); font-size: .72rem; font-weight: 850; letter-spacing: .15em; text-transform: uppercase; }
.relay-copy h2 { max-width: 11ch; margin: .8rem 0 0; font-size: clamp(3.5rem, 8.5vw, 8.2rem); font-weight: 860; letter-spacing: -.075em; line-height: .84; text-wrap: balance; }
.relay-countdown { display: flex; width: fit-content; margin-top: 1.8rem; overflow: hidden; border: 1px solid rgb(120 167 255 / 25%); border-radius: .7rem; background: rgb(6 12 22 / 64%); }
.relay-countdown span { display: grid; min-width: clamp(4.1rem, 7vw, 6.2rem); padding: .7rem .9rem; border-left: 1px solid rgb(120 167 255 / 16%); }
.relay-countdown span:first-child { border-left: 0; }
.relay-countdown strong { font: 750 clamp(1.25rem, 2.4vw, 2rem)/1 ui-monospace, monospace; font-variant-numeric: tabular-nums; }
.relay-countdown small { margin-top: .3rem; color: #6482aa; font-size: .52rem; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }
.relay-meta { display: flex; align-items: center; gap: 1.2rem; margin-top: 1.7rem; color: #71839b; font-size: .7rem; font-weight: 680; }
.relay-meta strong { display: inline-flex; align-items: center; gap: .4rem; color: #d8e7ff; }
.relay-orbit { position: absolute; top: 50%; right: clamp(3rem, 10vw, 11rem); width: clamp(17rem, 30vw, 31rem); aspect-ratio: 1; border: 1px solid rgb(91 148 242 / 18%); border-radius: 50%; box-shadow: 0 0 0 4rem rgb(58 124 240 / 3%), 0 0 7rem rgb(38 99 199 / 12%); transform: translateY(-50%); }
.relay-orbit i { position: absolute; inset: 14%; border: 1px solid rgb(91 148 242 / 19%); border-radius: 50%; }
.relay-orbit i:nth-child(2) { inset: 29%; }
.relay-orbit i:nth-child(3) { top: 50%; right: -8%; bottom: auto; left: -8%; border: 0; border-top: 1px solid rgb(91 148 242 / 20%); border-radius: 0; }
.relay-orbit span { position: absolute; inset: 0; display: grid; place-items: center; color: #7ba8ef; font-size: clamp(5rem, 11vw, 10rem); text-shadow: 0 0 3rem rgb(58 124 240 / 52%); }
.relay-benches { position: absolute; right: 3%; bottom: 8%; display: grid; width: min(31rem, 38vw); grid-template-columns: 1fr auto 1fr; align-items: center; gap: 1rem; color: #56749f; }
.relay-benches > span { display: flex; gap: .35rem; }
.relay-benches > span:last-child { justify-content: end; }
.relay-benches i { width: clamp(1.8rem, 3vw, 3rem); aspect-ratio: 1; border: 1px solid rgb(108 164 255 / 23%); border-radius: .45rem; background: #111f34; box-shadow: inset 0 0 1rem rgb(64 128 230 / 9%); }
.relay-benches b { font: 800 .7rem/1 ui-monospace, monospace; }
.relay-archive--finale { --relay-blue: var(--archive-primary); background: radial-gradient(circle at 76% 41%, color-mix(in srgb, var(--archive-accent) 22%, transparent), transparent 25%), linear-gradient(125deg, color-mix(in srgb, var(--archive-background) 92%, black), var(--archive-surface) 55%, var(--archive-background)); }
.relay-archive--finale::after { border-color: rgb(222 190 116 / 22%); }
.relay-archive--finale .relay-grid { background-image: linear-gradient(rgb(221 188 115 / 7%) 1px, transparent 1px), linear-gradient(90deg, rgb(221 188 115 / 7%) 1px, transparent 1px); }
.relay-archive--finale .relay-kicker, .relay-archive--finale .relay-meta strong { color: var(--archive-accent); }
.relay-archive--finale .relay-orbit { border-color: rgb(222 188 116 / 18%); box-shadow: 0 0 0 4rem rgb(210 165 74 / 3%), 0 0 7rem rgb(210 165 74 / 11%); }
.relay-archive--finale .relay-orbit i { border-color: rgb(222 188 116 / 19%); }
.relay-archive--finale .relay-orbit span { color: #d6b766; text-shadow: 0 0 3rem rgb(210 165 74 / 42%); }
.relay-archive--finale .relay-countdown { border-color: rgb(222 190 116 / 24%); }
.relay-archive--finale .relay-countdown span { border-color: rgb(222 190 116 / 15%); }
.finale-crown { position: absolute; z-index: 1; top: 8%; right: 6%; color: rgb(224 192 116 / 12%); font-size: clamp(12rem, 30vw, 32rem); line-height: 1; transform: rotate(9deg); }
.relay-archive:hover .relay-orbit { transform: translateY(-50%) scale(1.025); transition: transform 350ms ease; }
.relay-archive:hover .relay-meta strong { text-decoration: underline; text-underline-offset: .25rem; }
.relay-archive:focus-visible { outline: 3px solid var(--relay-blue); outline-offset: -3px; }
@media (max-width: 58rem) { .relay-archive { min-height: 31rem; padding: 2rem 1.25rem; }.relay-copy { width: 100%; }.relay-copy h2 { max-width: 12ch; }.relay-orbit { top: 35%; right: -5rem; opacity: .55; }.relay-benches { display: none; }.relay-countdown span { min-width: 3.5rem; padding-inline: .55rem; } }
</style>
