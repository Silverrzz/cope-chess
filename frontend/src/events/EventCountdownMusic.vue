<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import AppIcon from "@/components/ui/AppIcon.vue";
import { useViewerSettings } from "@/composables/useViewerSettings";

const props = defineProps<{ targetMs: number; clockOffsetMs: number; remainingMs: number }>();
const { eventMusicEnabled, setEventMusicEnabled } = useViewerSettings();
const audio = ref<HTMLAudioElement>();
const musicBlocked = ref(false);
const musicLengthMs = 60_000;
const finalMinute = computed(() => props.remainingMs > 0 && props.remainingMs <= musicLengthMs);
let pendingAudio = false;
let active = true;

function remainingTime(): number {
  return props.targetMs - (Date.now() + props.clockOffsetMs);
}

async function playMusic(prime = false): Promise<void> {
  const track = audio.value;
  const remaining = remainingTime();
  if (!track || !track.paused || pendingAudio || remaining <= 0) return;
  pendingAudio = true;
  try {
    track.volume = prime ? 0 : 1;
    track.currentTime = prime ? 0 : Math.max(0, (musicLengthMs - remaining) / 1000);
    await track.play();
    if (prime || !active || !eventMusicEnabled.value || remainingTime() <= 0) track.pause();
    musicBlocked.value = false;
  } catch {
    if (active && eventMusicEnabled.value) musicBlocked.value = true;
  } finally {
    track.volume = 1;
    pendingAudio = false;
  }
  if (prime && active && eventMusicEnabled.value && remainingTime() <= musicLengthMs && !musicBlocked.value) {
    void playMusic();
  }
}

function toggleMusic(): void {
  if (eventMusicEnabled.value && !musicBlocked.value) {
    setEventMusicEnabled(false);
    return;
  }
  setEventMusicEnabled(true);
  musicBlocked.value = false;
  void playMusic(remainingTime() > musicLengthMs);
}

function syncMusic(): void {
  const track = audio.value;
  if (!track) return;
  const remaining = remainingTime();
  if (!eventMusicEnabled.value || remaining <= 0 || remaining > musicLengthMs) {
    if (!pendingAudio) track.pause();
    if (!eventMusicEnabled.value) musicBlocked.value = false;
    return;
  }
  if (pendingAudio || musicBlocked.value || track.ended) return;
  if (track.paused) {
    void playMusic();
  } else {
    const expected = (musicLengthMs - remaining) / 1000;
    if (Math.abs(track.currentTime - expected) > .65) track.currentTime = expected;
  }
}

watch([finalMinute, eventMusicEnabled, () => props.clockOffsetMs], syncMusic, { flush: "post" });
onMounted(() => {
  syncMusic();
  window.addEventListener("focus", syncMusic);
  document.addEventListener("visibilitychange", syncMusic);
});
onBeforeUnmount(() => {
  active = false;
  audio.value?.pause();
  window.removeEventListener("focus", syncMusic);
  document.removeEventListener("visibilitychange", syncMusic);
});
</script>

<template>
  <button type="button" class="event-countdown-music" :aria-pressed="eventMusicEnabled && !musicBlocked" title="Music plays during the final minute" @click="toggleMusic">
    <AppIcon name="radio" :size="14" />
    {{ eventMusicEnabled && !musicBlocked ? 'Countdown music on' : 'Enable countdown music' }}
  </button>
  <audio ref="audio" src="/audio/openbench-engine-clash-countdown.wav" :preload="eventMusicEnabled ? 'auto' : 'none'" />
</template>

<style scoped>
.event-countdown-music { display: inline-flex; align-items: center; gap: .35rem; padding: .3rem .6rem; border: 1px solid var(--event-view-border, #ff72934d); border-radius: .35rem; background: var(--event-countdown-music-background, #29101b); color: var(--event-view-text, #fff0f3); font: inherit; cursor: pointer; }
.event-countdown-music:hover, .event-countdown-music:focus-visible { border-color: var(--event-view-accent, #ff7293); }
</style>
