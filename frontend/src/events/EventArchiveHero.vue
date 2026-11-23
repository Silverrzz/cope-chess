<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";
import AppIcon from "@/components/ui/AppIcon.vue";
import type { EventSummary } from "@/types/events";
const props = defineProps<{ summary: EventSummary }>();
const record = computed(() => props.summary.record);
const completed = computed(() => record.value.status === "completed");
const status = computed(() => {
  if (completed.value) return "Completed";
  if (record.value.status === "cancelled") return "Cancelled";
  return ["live", "intermission"].includes(record.value.status) ? "Live" : "Upcoming";
});
const timestamp = computed(() => completed.value
  ? record.value.finished_at ?? record.value.scheduled_start_at
  : record.value.scheduled_start_at);
const date = computed(() => {
  if (!timestamp.value) return "";
  const options: Intl.DateTimeFormatOptions = completed.value
    ? { day: "numeric", month: "long", year: "numeric" }
    : { weekday: "long", day: "numeric", month: "long", hour: "numeric", minute: "2-digit", hour12: true, timeZoneName: "short" };
  return new Intl.DateTimeFormat("en-GB", { ...options, timeZone: "Europe/London" }).format(new Date(timestamp.value));
});
</script>

<template>
  <RouterLink class="event-archive-hero" :to="{ name: 'event', params: { slug: record.slug } }" :aria-label="`${completed ? 'View results for' : 'Open'} ${record.title}`">
    <div class="event-hero-copy">
      <span class="event-hero-kicker"><AppIcon name="trophy" :size="16" />{{ status }}</span>
      <h2><slot name="title">{{ record.title }}</slot></h2>
      <div class="event-hero-meta">
        <time v-if="timestamp" :datetime="timestamp">{{ date }}</time>
        <slot v-else name="date-placeholder" />
        <strong>{{ completed ? 'View results' : 'View event' }} <span aria-hidden="true">&#8599;</span></strong>
      </div>
    </div>
    <div class="event-hero-art"><slot name="art" /></div>
  </RouterLink>
</template>

<style scoped>
.event-archive-hero { position: relative; display: grid; align-items: end; overflow: hidden; min-height: clamp(30rem, 72vh, 46rem); padding: clamp(2rem, 6vw, 6.5rem); box-sizing: border-box; color: var(--event-view-text, #fff0f3); text-decoration: none; background: radial-gradient(ellipse at 85% 45%, var(--event-view-glow, #481024) 0, transparent 65%), var(--event-view-background, #14090f); }
.event-hero-copy { position: relative; z-index: 1; width: min(55rem, 72%); }
.event-hero-kicker { display: inline-flex; align-items: center; gap: .55rem; color: var(--event-view-accent, #ff7293); font-size: .72rem; font-weight: 850; letter-spacing: .17em; text-transform: uppercase; }
h2 { margin: 1rem 0 0; font-size: clamp(3.5rem, 8.5vw, 8.2rem); font-weight: 900; letter-spacing: -.075em; line-height: .84; text-transform: uppercase; }
.event-hero-meta { display: flex; align-items: center; flex-wrap: wrap; gap: .7rem 1.4rem; margin-top: 1.7rem; color: var(--event-view-muted, #b98696); font-size: .7rem; font-weight: 680; }
.event-hero-meta strong { color: var(--event-view-accent, #ff7293); text-underline-offset: .3em; }
.event-hero-meta strong span { display: inline-block; margin-left: .5rem; }
.event-archive-hero:hover .event-hero-meta strong { color: var(--event-view-highlight, #ffc3d4); }
.event-archive-hero:focus-visible { outline: 2px solid var(--event-view-accent, #ff7293); outline-offset: 6px; }
.event-hero-art { position: absolute; top: 50%; right: clamp(2rem, 7vw, 8rem); width: clamp(18rem, 32vw, 33rem); transform: translateY(-50%); }
@media(max-width: 700px) { .event-archive-hero { min-height: 31rem; padding: 2rem 1.25rem; }.event-hero-copy { width: 100%; }.event-hero-art { width: 18rem; top: 30%; right: -3rem; opacity: .45; }h2 { font-size: clamp(4rem, 14vw, 6rem); } }
</style>
