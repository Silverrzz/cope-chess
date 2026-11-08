<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import { useViewerSettings } from "@/composables/useViewerSettings";
import type { EventDetailResponse } from "@/types/events";
import HandBrainArt from "./HandBrainArt.vue";
import HandBrainAnnouncement from "./HandBrainAnnouncement.vue";
import HandBrainArena from "./HandBrainArena.vue";
import HandBrainResults from "./HandBrainResults.vue";
import type { HandBrainPayload } from "./types";
const props = withDefaults(defineProps<{ detail: EventDetailResponse; clockOffsetMs?: number; view?: string }>(), { clockOffsetMs: 0 });
const now = ref(Date.now());
let timer: number | undefined;
onMounted(() => { timer = window.setInterval(() => { now.value = Date.now(); }, 1000); });
onBeforeUnmount(() => { if (timer !== undefined) window.clearInterval(timer); });
const event = computed(() => props.detail.event);
const completed = computed(() => event.value.status === "completed");
const payload = computed(() => props.detail.custom as HandBrainPayload);
const announcementOnly = computed(() => !payload.value?.teams?.length && ["draft", "announced", "scheduled", "postponed"].includes(event.value.status));
const final = computed(() => payload.value.matches.find(match => match.key === "F"));
const winner = computed(() => payload.value.teams.find(team => team.id === final.value?.winner_team_id));
const remaining = computed(() => Math.max(0, Date.parse(event.value.scheduled_start_at ?? "") - now.value - props.clockOffsetMs));
const showCountdown = computed(() => ["announced", "scheduled"].includes(event.value.status) && remaining.value > 0);
const showArenaLink = computed(() =>
  (completed.value && payload.value.matches.some(match => match.quads.some(quad => quad.games.length)))
  || ["live", "intermission"].includes(event.value.status)
  || (event.value.status === "scheduled" && !!event.value.scheduled_start_at && remaining.value === 0),
);
const { eventMusicEnabled, setEventMusicEnabled } = useViewerSettings();
const countdownAudio = ref<HTMLAudioElement>();
const musicBlocked = ref(false);
const musicVisible = computed(() => showCountdown.value && props.view !== "arena" && !announcementOnly.value);
const musicLengthMs = 60_000;
let pendingAudio = false;

async function playMusic(prime = false) {
  const audio = countdownAudio.value;
  if (!audio || pendingAudio || !audio.paused) return;
  pendingAudio = true;
  try {
    audio.volume = prime ? 0 : 1;
    audio.currentTime = prime ? 0 : Math.max(0, (musicLengthMs - remaining.value) / 1000);
    await audio.play();
    if (prime || !musicVisible.value || !eventMusicEnabled.value) audio.pause();
    musicBlocked.value = false;
  } catch {
    if (musicVisible.value && eventMusicEnabled.value) musicBlocked.value = true;
  } finally {
    audio.volume = 1;
    pendingAudio = false;
  }
}

function toggleMusic() {
  if (eventMusicEnabled.value && !musicBlocked.value) {
    setEventMusicEnabled(false);
    return;
  }
  setEventMusicEnabled(true);
  musicBlocked.value = false;
  now.value = Date.now();
  void playMusic(remaining.value > musicLengthMs);
}

watch([musicVisible, remaining, eventMusicEnabled, countdownAudio], () => {
  if (!musicVisible.value || !eventMusicEnabled.value) {
    countdownAudio.value?.pause();
    musicBlocked.value = false;
  } else if (remaining.value <= musicLengthMs && !musicBlocked.value && !countdownAudio.value?.ended) {
    void playMusic();
  }
}, { flush: "post" });
onBeforeUnmount(() => countdownAudio.value?.pause());
const countdown = computed(() => {
  const seconds = Math.ceil(remaining.value / 1000);
  return [{ label: "days", value: Math.floor(seconds / 86400) }, { label: "hours", value: Math.floor(seconds % 86400 / 3600) }, { label: "mins", value: Math.floor(seconds % 3600 / 60) }, { label: "secs", value: seconds % 60 }];
});
const schedule = computed(() => event.value.scheduled_start_at ? new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", hour: "numeric", minute: "2-digit", hour12: true, timeZone: "Europe/London", timeZoneName: "short" }).format(new Date(event.value.scheduled_start_at)) : "Date TBD");
const status = computed(() => ({ draft: "Draft", announced: "Announced", scheduled: "Scheduled", live: "Live", intermission: "Intermission", completed: "Completed", cancelled: "Cancelled", postponed: "Postponed" }[event.value.status]));
</script>

<template>
  <HandBrainAnnouncement v-if="announcementOnly" :detail="detail" :clock-offset-ms="clockOffsetMs" />
  <HandBrainArena v-else-if="view === 'arena'" :detail="detail" :clock-offset-ms="clockOffsetMs" />
  <div v-else class="hb-page">
    <section class="hb-stage" :class="{ 'hb-stage--complete': completed, 'hb-stage--ready': !completed && payload.teams.length }" aria-labelledby="hb-title">
      <div class="hb-content">
        <div class="hb-emblem"><HandBrainArt /></div>
        <header class="hb-heading"><span class="hb-kicker"><template v-if="completed">Hand &amp; Brain · Champions</template><template v-else>Engine event</template></span><h1 v-if="completed" id="hb-title" class="hb-winner">{{ winner?.name ?? 'Hand & Brain' }}</h1><h1 v-else id="hb-title">Hand <span>&amp;</span> Brain</h1></header>
        <template v-if="completed">
          <div class="hb-winning-engines" aria-label="Winning engines"><strong v-for="member in winner?.members" :key="member.id">{{ member.name }}</strong></div>
          <HandBrainResults :detail="detail" />
        </template>
        <div v-if="showCountdown" class="hb-countdown" aria-label="Time until event">
          <div v-for="part in countdown" :key="part.label"><strong>{{ String(part.value).padStart(2, '0') }}</strong><span>{{ part.label }}</span></div>
        </div>
        <div v-if="!completed" class="hb-schedule">
          <time :datetime="event.scheduled_start_at ?? undefined"><AppIcon name="clock" :size="15" />{{ schedule }}</time>
          <button v-if="musicVisible" type="button" class="hb-music" :aria-pressed="eventMusicEnabled && !musicBlocked" @click="toggleMusic">
            <AppIcon name="radio" :size="14" />
            {{ eventMusicEnabled && !musicBlocked ? 'Countdown music on' : 'Enable countdown music' }}
          </button>
        </div>
        <RouterLink v-if="showArenaLink" :to="`/events/${event.slug}/arena`" class="hb-arena-link">
          <AppIcon name="arrow-right" :size="16" />
          {{ completed ? 'Replay games' : 'Enter arena' }}
        </RouterLink>
        <audio ref="countdownAudio" src="/audio/openbench-engine-clash-countdown.wav" preload="none" />
        <HandBrainResults v-if="!completed && payload.matches.length" :detail="detail" />
      </div>
      <footer class="hb-status">
        <div><AppIcon name="activity" :size="20" /><span><small>Status</small><strong>{{ status }}</strong></span></div>
        <div><AppIcon name="user" :size="20" /><span><small>Current spectators</small><strong>{{ (detail.spectator_count ?? 0).toLocaleString() }}</strong></span></div>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.hb-page { margin-block: calc(0px - clamp(var(--space-6), 4vw, var(--space-12))) -3rem; color: #fff0f3; background: #14090f; }
.hb-stage { display: grid; grid-template-rows: 1fr auto; min-height: calc(100dvh - var(--header-height, 4rem) - 1px); background: radial-gradient(ellipse at 50% 35%, #51112780, transparent 65%); border-bottom: 1px solid #ff729326; }
.hb-content { min-width: 0; display: flex; flex-direction: column; justify-content: center; align-items: center; width: min(100%, 72rem); margin-inline: auto; padding: 2rem 1.25rem; box-sizing: border-box; }
.hb-emblem { width: clamp(6rem, 17dvh, 9rem); margin-bottom: .75rem; }
.hb-heading { text-align: center; }
.hb-kicker { color: #ff7293; font-size: .62rem; font-weight: 820; letter-spacing: .18em; text-transform: uppercase; }
.hb-heading h1 { margin: .6rem 0 0; font-size: clamp(2.7rem, 7.5dvh, 5.5rem); font-weight: 850; letter-spacing: -.065em; line-height: 1; }
.hb-heading h1 span { color: #ff7293; }
.hb-heading .hb-winner { max-width: 100%; overflow-wrap: anywhere; }
.hb-winning-engines { display: flex; flex-wrap: wrap; justify-content: center; gap: .65rem; margin-top: 1.5rem; }.hb-winning-engines strong { padding: .65rem 1.2rem; border: 1px solid #ff72934d; border-radius: .5rem; background: #29101b; color: #f5c7d4; font-size: 1rem; font-weight: 700; }
.hb-stage--complete .hb-content { width: min(100%, 76rem); padding-block: 1.5rem; }
.hb-stage--complete .hb-emblem { width: 5rem; margin-bottom: .5rem; }
.hb-stage--complete .hb-heading h1 { font-size: clamp(2rem, 5dvh, 3.5rem); }
.hb-stage--complete .hb-winning-engines { margin-top: .8rem; }
.hb-stage--complete .hb-winning-engines strong { padding: .45rem .85rem; font-size: .85rem; }
.hb-countdown { display: grid; grid-template-columns: repeat(4, 1fr); width: min(100%, 36rem); margin-top: 2rem; text-align: center; }
.hb-countdown > div { display: grid; gap: .4rem; }
.hb-countdown strong { font-size: clamp(2.2rem, 6dvh, 4.2rem); font-weight: 750; font-variant-numeric: tabular-nums; letter-spacing: -.05em; line-height: 1; }
.hb-countdown span { color: #b98696; font-size: .6rem; font-weight: 800; letter-spacing: .16em; text-transform: uppercase; }
.hb-schedule { display: flex; align-items: center; justify-content: center; gap: .5rem; margin-top: .65rem; color: #c397a7; font-size: .75rem; text-align: center; }
.hb-schedule { flex-wrap: wrap; }
.hb-schedule time, .hb-music { display: inline-flex; align-items: center; gap: .35rem; }
.hb-music { padding: .2rem .45rem; border: 1px solid #ff72934d; border-radius: .35rem; background: #29101b; color: #fff0f3; font: inherit; cursor: pointer; }
.hb-music:hover, .hb-music:focus-visible { border-color: #ff7293; }
.hb-arena-link { display: inline-flex; align-items: center; gap: .45rem; margin-top: .65rem; padding: .45rem .8rem; border: 1px solid #ff72934d; border-radius: .45rem; background: #ff7293; color: #200b14; font-size: .8rem; font-weight: 750; text-decoration: none; }
.hb-arena-link:hover, .hb-arena-link:focus-visible { background: #ff9eb5; }
.hb-stage--ready .hb-content { width: min(100%, 76rem); padding-block: .5rem .15rem; }
.hb-stage--ready .hb-emblem { width: 2.5rem; margin-bottom: .25rem; }
.hb-stage--ready .hb-kicker { display: block; line-height: 1; }
.hb-stage--ready .hb-heading h1 { margin-top: .3rem; font-size: clamp(2.5rem, 5dvh, 3.5rem); }
.hb-stage--ready .hb-countdown { margin-top: .7rem; width: min(100%, 28rem); }
.hb-stage--ready .hb-countdown strong { font-size: 2.2rem; }
.hb-status { display: flex; justify-content: space-between; gap: 1rem; padding: .375rem clamp(1rem, 3vw, 3rem); }
.hb-status > div { display: flex; align-items: center; gap: .7rem; color: #d18a9e; }
.hb-status > div > span { display: grid; gap: .2rem; }
.hb-status small { color: #ab7a8a; font-size: .55rem; font-weight: 750; letter-spacing: .06em; text-transform: uppercase; }
.hb-status strong { color: #f5c7d4; font-size: .75rem; }
@media(max-width: 450px) { .hb-heading h1 { font-size: clamp(2.5rem, 11vw, 3.5rem); }.hb-content { padding-inline: 1rem; }.hb-schedule { font-size: .67rem; }.hb-countdown { margin-top: 1.6rem; } }
</style>
