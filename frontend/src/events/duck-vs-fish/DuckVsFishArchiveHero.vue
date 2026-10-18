<script setup lang="ts">
import { computed } from "vue";
import wakwak from "@/assets/wakwak.svg";
import AppIcon from "@/components/ui/AppIcon.vue";
import type { EventSummary } from "@/types/events";
import FishMark from "./FishMark.vue";
import PondScene from "./PondScene.vue";

const props = defineProps<{ summary: EventSummary; current?: boolean; nowMs: number }>();
const event = computed(() => props.summary.record);
const live = computed(() => ["live", "intermission"].includes(event.value.status));
const target = computed(() => ({ name: props.current ? "event" : "event-arena", params: { slug: event.value.slug } }));
const remaining = computed(() => Math.max(0, Date.parse(event.value.scheduled_start_at ?? "") - props.nowMs) || 0);
const countdown = computed(() => {
  const seconds = Math.ceil(remaining.value / 1000);
  return [{ label: "days", value: Math.floor(seconds / 86400) }, { label: "hours", value: Math.floor(seconds % 86400 / 3600) }, { label: "mins", value: Math.floor(seconds % 3600 / 60) }, { label: "secs", value: seconds % 60 }];
});
const timing = computed(() => {
  if (props.current && live.value) return "Live now";
  const date = props.current ? event.value.scheduled_start_at : event.value.finished_at ?? event.value.scheduled_start_at;
  return date ? new Intl.DateTimeFormat(undefined, { dateStyle: "long" }).format(new Date(date)) : "Start date TBD";
});
</script>

<template>
  <RouterLink class="duck-archive" :to="target" :aria-label="`${current ? 'Open' : 'View'} ${event.title}`">
    <PondScene />
    <div class="duck-orbit" aria-hidden="true"><i></i><i></i><i></i><img :src="wakwak" alt="" /><FishMark /><span>VS</span></div>
    <div class="duck-copy">
      <span class="duck-kicker"><AppIcon name="trophy" :size="16" />{{ current ? live ? 'Live' : 'Upcoming' : event.status === 'cancelled' ? 'Cancelled' : 'Completed' }}</span>
      <h2>{{ event.title }}</h2>
      <div v-if="current && remaining > 0" class="duck-countdown" aria-label="Time until event"><span v-for="part in countdown" :key="part.label"><strong>{{ String(part.value).padStart(2, '0') }}</strong><small>{{ part.label }}</small></span></div>
      <div class="duck-meta"><span>{{ timing }}</span><strong>{{ current ? 'View event' : 'View arena' }} <AppIcon name="arrow-right" :size="18" /></strong></div>
    </div>
  </RouterLink>
</template>

<style scoped>
.duck-archive { --duck-blue: var(--archive-primary); position: relative; isolation: isolate; display: grid; min-height: clamp(30rem, 72vh, 46rem); overflow: hidden; align-items: end; padding: clamp(2rem, 6vw, 6.5rem); background: radial-gradient(circle at 77% 46%, color-mix(in srgb, var(--archive-accent) 23%, transparent), transparent 24%), linear-gradient(125deg, color-mix(in srgb, var(--archive-background) 92%, black) 5%, var(--archive-surface) 51%, var(--archive-background)); color: var(--archive-text); text-decoration: none; }
.duck-archive::after { position: absolute; z-index: 5; inset: 0; border: 1px solid rgb(110 166 255 / 20%); content: ""; pointer-events: none; }
.duck-grid { position: absolute; z-index: -1; inset: 0; background-image: linear-gradient(rgb(82 140 230 / 8%) 1px, transparent 1px), linear-gradient(90deg, rgb(82 140 230 / 8%) 1px, transparent 1px); background-size: 4rem 4rem; mask-image: linear-gradient(90deg, black, transparent 78%); }
.duck-copy { position: relative; z-index: 2; width: min(55rem, 72%); }
.duck-kicker { display: inline-flex; align-items: center; gap: .55rem; color: var(--archive-accent); font-size: .72rem; font-weight: 850; letter-spacing: .15em; text-transform: uppercase; }
.duck-copy h2 { max-width: 11ch; margin: .8rem 0 0; font-size: clamp(3.5rem, 8.5vw, 8.2rem); font-weight: 860; letter-spacing: -.075em; line-height: .84; text-wrap: balance; }
.duck-countdown { display: flex; width: fit-content; margin-top: 1.8rem; overflow: hidden; border: 1px solid rgb(120 167 255 / 25%); border-radius: .7rem; background: rgb(6 12 22 / 64%); }
.duck-countdown span { display: grid; min-width: clamp(4.1rem, 7vw, 6.2rem); padding: .7rem .9rem; border-left: 1px solid rgb(120 167 255 / 16%); }
.duck-countdown span:first-child { border-left: 0; }
.duck-countdown strong { font: 750 clamp(1.25rem, 2.4vw, 2rem)/1 ui-monospace, monospace; font-variant-numeric: tabular-nums; }
.duck-countdown small { margin-top: .3rem; color: #6482aa; font-size: .52rem; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }
.duck-meta { display: flex; align-items: center; gap: 1.2rem; margin-top: 1.7rem; color: #71839b; font-size: .7rem; font-weight: 680; }
.duck-meta strong { display: inline-flex; align-items: center; gap: .4rem; color: #d8e7ff; }
.duck-orbit { position: absolute; top: 50%; right: clamp(3rem, 10vw, 11rem); width: clamp(17rem, 30vw, 31rem); aspect-ratio: 1; border: 1px solid rgb(91 148 242 / 18%); border-radius: 50%; box-shadow: 0 0 0 4rem rgb(58 124 240 / 3%), 0 0 7rem rgb(38 99 199 / 12%); transform: translateY(-50%); }
.duck-orbit i { position: absolute; inset: 14%; border: 1px solid rgb(91 148 242 / 19%); border-radius: 50%; }
.duck-orbit i:nth-child(2) { inset: 29%; }
.duck-orbit i:nth-child(3) { top: 50%; right: -8%; bottom: auto; left: -8%; border: 0; border-top: 1px solid rgb(91 148 242 / 20%); border-radius: 0; }
.duck-orbit span { position: absolute; inset: 0; display: grid; place-items: center; color: #7ba8ef; font-size: clamp(5rem, 11vw, 10rem); text-shadow: 0 0 3rem rgb(58 124 240 / 52%); }

.duck-archive { --archive-primary: #d9bd57; --archive-accent: #aac18d; --archive-background: #080f13; --archive-surface: #142b24; --archive-text: #eff3e9; }
.duck-archive::after { border-color: #a8bf8c29; }.duck-copy h2 { color: var(--archive-text); }.duck-orbit { border-color: #a5c09130; box-shadow: 0 0 0 4rem #8ea96d05, 0 0 7rem #71945010; }.duck-orbit i { border-color: #a5c09130; }.duck-orbit img, .duck-orbit > svg { position: absolute; top: 50%; width: 32%; height: 32%; transform: translateY(-50%); }.duck-orbit img { left: 10%; }.duck-orbit > svg { right: 8%; }.duck-orbit > span { color: #acc18e; font-size: .8rem; font-weight: 850; text-shadow: none; }.duck-meta { color: #7f988b; }.duck-meta strong { color: #c8d9b4; }.duck-countdown { border-color: #a8bf8c38; background: #0b1813b8; }.duck-countdown span { border-color: #a8bf8c21; }.duck-countdown small { color: #859a75; }
.duck-archive:hover .duck-orbit { transform: translateY(-50%) scale(1.025); transition: transform 350ms ease; }.duck-archive:hover .duck-meta strong { text-decoration: underline; text-underline-offset: .25rem; }.duck-archive:focus-visible { outline: 3px solid #a8bf8c; outline-offset: -3px; }
@media (max-width: 58rem) { .duck-archive { min-height: 31rem; padding: 2rem 1.25rem; }.duck-copy { width: 100%; }.duck-copy h2 { max-width: 12ch; }.duck-orbit { top: 35%; right: -5rem; opacity: .35; }.duck-countdown span { min-width: 3.5rem; padding-inline: .55rem; } }
@media (prefers-reduced-motion: reduce) { .duck-archive:hover .duck-orbit { transition: none; } }
</style>
