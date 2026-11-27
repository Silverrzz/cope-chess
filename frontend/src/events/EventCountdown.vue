<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ remainingMs: number; compact?: boolean }>();
const countdown = computed(() => {
  const seconds = Math.ceil(Math.max(0, props.remainingMs) / 1000);
  return [
    { label: "days", value: Math.floor(seconds / 86400) },
    { label: "hours", value: Math.floor(seconds % 86400 / 3600) },
    { label: "mins", value: Math.floor(seconds % 3600 / 60) },
    { label: "secs", value: seconds % 60 },
  ];
});
</script>

<template>
  <div role="timer" aria-label="Time until event" class="event-countdown" :class="{ 'event-countdown--compact': compact }">
    <div v-for="part in countdown" :key="part.label">
      <strong>{{ String(part.value).padStart(2, '0') }}</strong>
      <span>{{ part.label }}</span>
    </div>
  </div>
</template>

<style scoped>
.event-countdown { display: grid; grid-template-columns: repeat(4, 1fr); width: min(100%, 36rem); margin-top: 2rem; text-align: center; }
.event-countdown > div { display: grid; gap: .4rem; }
.event-countdown strong { font-size: clamp(2.2rem, 6dvh, 4.2rem); font-weight: 750; font-variant-numeric: tabular-nums; letter-spacing: -.05em; line-height: 1; }
.event-countdown span { color: var(--event-view-muted, #b98696); font-size: .6rem; font-weight: 800; letter-spacing: .16em; text-transform: uppercase; }
.event-countdown--compact { width: fit-content; max-width: 100%; margin-top: 1.8rem; overflow: hidden; border: 1px solid var(--event-view-border); border-radius: .7rem; background: color-mix(in srgb, var(--event-view-background) 75%, transparent); }
.event-countdown--compact > div { min-width: clamp(3.55rem, 7vw, 6.2rem); padding: .7rem clamp(.5rem, 1.25vw, .9rem); border-inline-start: 1px solid var(--event-view-border); }
.event-countdown--compact > div:first-child { border-inline-start: 0; }
.event-countdown--compact strong { font: 750 clamp(1.25rem, 2.4vw, 2rem)/1 ui-monospace, monospace; }
.event-countdown--compact span { font-size: .52rem; letter-spacing: .1em; }
@media (max-width: 450px) { .event-countdown { margin-top: 1.6rem; } }
</style>
