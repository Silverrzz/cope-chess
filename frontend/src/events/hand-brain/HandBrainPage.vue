<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import type { EventDetailResponse } from "@/types/events";
import HandBrainArt from "./HandBrainArt.vue";
const props = withDefaults(defineProps<{ detail: EventDetailResponse; clockOffsetMs?: number; view?: string }>(), { clockOffsetMs: 0 });
const now = ref(Date.now());
let timer: number | undefined;
onMounted(() => { timer = window.setInterval(() => { now.value = Date.now(); }, 1000); });
onBeforeUnmount(() => { if (timer !== undefined) window.clearInterval(timer); });
const event = computed(() => props.detail.event);
const remaining = computed(() => Math.max(0, Date.parse(event.value.scheduled_start_at ?? "") - now.value - props.clockOffsetMs));
const showCountdown = computed(() => ["announced", "scheduled"].includes(event.value.status) && remaining.value > 0);
const countdown = computed(() => {
  const seconds = Math.ceil(remaining.value / 1000);
  return [{ label: "days", value: Math.floor(seconds / 86400) }, { label: "hours", value: Math.floor(seconds % 86400 / 3600) }, { label: "mins", value: Math.floor(seconds % 3600 / 60) }, { label: "secs", value: seconds % 60 }];
});
const schedule = computed(() => event.value.scheduled_start_at ? new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", hour: "numeric", minute: "2-digit", hour12: true, timeZone: "Europe/London", timeZoneName: "short" }).format(new Date(event.value.scheduled_start_at)) : "Date TBD");
const status = computed(() => ({ draft: "Draft", announced: "Announced", scheduled: "Scheduled", live: "Live", intermission: "Intermission", completed: "Completed", cancelled: "Cancelled", postponed: "Postponed" }[event.value.status]));
const competitors = computed(() => props.detail.cast.filter(member => member.parent_id === null));
</script>

<template>
  <div class="hb-page">
    <section class="hb-stage" aria-labelledby="hb-title">
      <div class="hb-content">
        <div class="hb-emblem"><HandBrainArt /></div>
        <header class="hb-heading"><span class="hb-kicker">Engine event</span><h1 id="hb-title">Hand <span>&amp;</span> Brain</h1></header>
        <div v-if="showCountdown" class="hb-countdown" aria-label="Time until event">
          <div v-for="part in countdown" :key="part.label"><strong>{{ String(part.value).padStart(2, '0') }}</strong><span>{{ part.label }}</span></div>
        </div>
        <time class="hb-schedule" :datetime="event.scheduled_start_at ?? undefined"><AppIcon name="clock" :size="15" />{{ schedule }}</time>
        <section class="hb-competitors" aria-labelledby="hb-competitors-title">
          <h2 id="hb-competitors-title">Competitors</h2>
          <ul v-if="competitors.length"><li v-for="member in competitors" :key="member.id"><strong>{{ member.display_name }}</strong><span v-if="member.profile">{{ member.profile }}</span></li></ul>
          <p v-else>TBD</p>
        </section>
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
.hb-content { display: flex; flex-direction: column; justify-content: center; align-items: center; width: min(100%, 72rem); margin-inline: auto; padding: 2rem 1.25rem; box-sizing: border-box; }
.hb-emblem { width: clamp(6rem, 17dvh, 9rem); margin-bottom: .75rem; }
.hb-heading { text-align: center; }
.hb-kicker { color: #ff7293; font-size: .62rem; font-weight: 820; letter-spacing: .18em; text-transform: uppercase; }
.hb-heading h1 { margin: .6rem 0 0; font-size: clamp(2.7rem, 7.5dvh, 5.5rem); font-weight: 850; letter-spacing: -.065em; line-height: 1; }
.hb-heading h1 span { color: #ff7293; }
.hb-countdown { display: grid; grid-template-columns: repeat(4, 1fr); width: min(100%, 36rem); margin-top: 2rem; text-align: center; }
.hb-countdown > div { display: grid; gap: .4rem; }
.hb-countdown strong { font-size: clamp(2.2rem, 6dvh, 4.2rem); font-weight: 750; font-variant-numeric: tabular-nums; letter-spacing: -.05em; line-height: 1; }
.hb-countdown span { color: #b98696; font-size: .6rem; font-weight: 800; letter-spacing: .16em; text-transform: uppercase; }
.hb-schedule { display: flex; align-items: center; justify-content: center; gap: .5rem; margin-top: 1.2rem; color: #c397a7; font-size: .75rem; text-align: center; }
.hb-competitors { width: min(100%, 36rem); margin-top: 2.3rem; padding-top: 1.2rem; border-top: 1px solid #ff729326; text-align: center; }
.hb-competitors h2 { margin: 0; color: #b98696; font-size: .6rem; font-weight: 800; letter-spacing: .15em; text-transform: uppercase; }
.hb-competitors p { margin: .7rem 0 0; font-size: 1.1rem; font-weight: 650; }
.hb-competitors ul { display: flex; flex-wrap: wrap; justify-content: center; gap: 1rem; padding: 0; list-style: none; }
.hb-competitors li { display: grid; gap: .3rem; }
.hb-competitors li span { color: #b98696; font-size: .75rem; }
.hb-status { display: flex; justify-content: space-between; gap: 1rem; padding: 1rem clamp(1rem, 3vw, 3rem); }
.hb-status > div { display: flex; align-items: center; gap: .7rem; color: #d18a9e; }
.hb-status > div > span { display: grid; gap: .2rem; }
.hb-status small { color: #ab7a8a; font-size: .55rem; font-weight: 750; letter-spacing: .06em; text-transform: uppercase; }
.hb-status strong { color: #f5c7d4; font-size: .75rem; }
@media(max-width: 450px) { .hb-heading h1 { font-size: clamp(2.5rem, 11vw, 3.5rem); }.hb-content { padding-inline: 1rem; }.hb-schedule { font-size: .67rem; }.hb-countdown { margin-top: 1.6rem; } }
</style>
