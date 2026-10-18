<script setup lang="ts">
import { computed } from "vue";
import type { EventSummary } from "@/types/events";
import PondScene from "./PondScene.vue";

const props = defineProps<{ summary: EventSummary; current?: boolean; nowMs: number }>();
const event = computed(() => props.summary.record);
const live = computed(() => ["live", "intermission"].includes(event.value.status));
const timing = computed(() => {
  if (live.value) return "Live at the pond";
  if (!event.value.scheduled_start_at) return "Start date TBD";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "long", timeStyle: "short" }).format(new Date(event.value.scheduled_start_at));
});
</script>

<template>
  <RouterLink class="pond-banner" :to="`/events/${event.slug}`">
    <PondScene />
    <div class="pond-banner__copy">
      <span class="pond-eyebrow">{{ current ? live ? "On the water · Live" : "Coming to the pond" : "From the pond · Event archive" }}</span>
      <h2>Duck <em>vs</em><br />Fish<span>.</span></h2>
      <p>Wakwak × Stockfish</p>
      <div class="pond-banner__facts"><span>Duck Chess</span><span>64 threads</span><span>20 games</span></div>
      <div class="pond-banner__footer"><span>{{ timing }}</span><strong>{{ current ? "Meet at the pond" : "Revisit the match" }} <span aria-hidden="true">↗</span></strong></div>
    </div>
  </RouterLink>
</template>

<style scoped>
.pond-banner { position: relative; isolation: isolate; display: grid; align-items: center; min-height: clamp(30rem, 65vh, 43rem); padding: clamp(2rem, 7vw, 7rem); overflow: hidden; color: #f4ecd3; background: #163f3d; text-decoration: none; }
.pond-banner h2 { color: #f4ecd3; }
.pond-banner::after { position: absolute; z-index: 0; inset: 0; content: ""; background: linear-gradient(90deg, #153f3dea, #204b3e80 45%, transparent 75%); pointer-events: none; }
.pond-banner__copy { position: relative; z-index: 1; width: min(42rem, 75%); }
.pond-eyebrow { color: #bbd6ae; font: 700 .7rem/1.5 ui-monospace, monospace; letter-spacing: .15em; text-transform: uppercase; }
h2 { margin: 1.1rem 0; font: 850 clamp(5.5rem, 10vw, 10rem)/.83 var(--font-sans, sans-serif); letter-spacing: -.07em; }h2 em { color: #bad2ae; font: italic 400 .5em Georgia, serif; letter-spacing: -.08em; }h2 > span { color: #edc653; }
p { margin: 1.5rem 0 1rem; color: #edc653; font-size: clamp(1.1rem, 2vw, 1.5rem); font-weight: 650; letter-spacing: -.03em; }
.pond-banner__facts { display: flex; flex-wrap: wrap; gap: .5rem; }.pond-banner__facts span { border: 1px solid #c0d0a84d; border-radius: 2rem; padding: .4rem .7rem; color: #d2dfc2; font-size: .72rem; }
.pond-banner__footer { display: flex; flex-wrap: wrap; align-items: center; gap: 1rem 2rem; margin-top: 2rem; font-size: .8rem; color: #c4d2b7; }.pond-banner__footer strong { color: #f4ecd3; }.pond-banner__footer strong span { margin-left: .5rem; color: #edc653; }
.pond-banner:hover strong { text-decoration: underline; text-underline-offset: .25em; }.pond-banner:focus-visible { outline: 3px solid #edc653; outline-offset: -4px; }
@media (max-width: 44rem) { .pond-banner { min-height: 34rem; padding: 4rem 1.5rem 2.5rem; align-items: end; }.pond-banner__copy { width: 100%; }h2 { font-size: 6rem; } }
</style>
