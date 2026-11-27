<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import type { EventDetailResponse } from "@/types/events";
import EventCountdown from "./EventCountdown.vue";
import EventCountdownMusic from "./EventCountdownMusic.vue";

const props = withDefaults(defineProps<{
  detail: EventDetailResponse;
  clockOffsetMs?: number;
  countdownMusic?: boolean;
  pendingStart?: string;
}>(), { clockOffsetMs: 0 });
const now = ref(Date.now());
let timer: number | undefined;
onMounted(() => { timer = window.setInterval(() => { now.value = Date.now(); }, props.countdownMusic ? 100 : 1000); });
onBeforeUnmount(() => { if (timer !== undefined) window.clearInterval(timer); });
const event = computed(() => props.detail.event);
const target = computed(() => Date.parse(event.value.scheduled_start_at ?? ""));
const remaining = computed(() => Math.max(0, target.value - (now.value + props.clockOffsetMs)));
const scheduled = computed(() => ["announced", "scheduled"].includes(event.value.status));
const showCountdown = computed(() => scheduled.value && remaining.value > 0);
const waiting = computed(() => scheduled.value && remaining.value === 0 && props.pendingStart);
const pulse = computed(() => props.countdownMusic && showCountdown.value && remaining.value <= 60_000 ? Math.ceil(remaining.value / 1000) : null);
const schedule = computed(() => event.value.scheduled_start_at ? new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", hour: "numeric", minute: "2-digit", hour12: true, timeZone: "Europe/London", timeZoneName: "short" }).format(new Date(event.value.scheduled_start_at)) : "Date TBD");
const status = computed(() => ({ draft: "Draft", announced: "Announced", scheduled: "Scheduled", live: "Live", intermission: "Intermission", completed: "Completed", cancelled: "Cancelled", postponed: "Postponed" }[event.value.status]));
const competitors = computed(() => props.detail.cast.filter(member => member.parent_id === null));
</script>

<template>
  <div class="event-announcement-page">
    <section class="event-announcement-stage" aria-labelledby="event-announcement-title">
      <div v-if="$slots.backdrop" class="event-announcement-backdrop" aria-hidden="true"><slot name="backdrop" /></div>
      <div v-if="pulse !== null" :key="pulse" class="event-announcement-pulse" aria-hidden="true"></div>
      <div v-if="$slots.tools" class="event-announcement-tools"><slot name="tools" /></div>
      <div class="event-announcement-content">
        <div class="event-announcement-emblem"><slot name="art" /></div>
        <header class="event-announcement-heading"><span class="event-announcement-kicker">Engine event</span><h1 id="event-announcement-title"><slot name="title">{{ event.title }}</slot></h1></header>
        <EventCountdown v-if="showCountdown" :remaining-ms="remaining" />
        <p v-else-if="waiting" class="event-announcement-waiting" role="status">{{ waiting }}</p>
        <div class="event-announcement-schedule">
          <time :datetime="event.scheduled_start_at ?? undefined"><AppIcon name="clock" :size="15" />{{ schedule }}</time>
          <EventCountdownMusic v-if="countdownMusic && showCountdown" :key="target" :target-ms="target" :clock-offset-ms="clockOffsetMs" :remaining-ms="remaining" />
        </div>
        <section class="event-announcement-competitors" :class="{ 'event-announcement-competitors--wide': $slots.competitors }" aria-labelledby="event-announcement-competitors-title">
          <h2 id="event-announcement-competitors-title">Competitors</h2>
          <slot name="competitors">
            <ul v-if="competitors.length"><li v-for="member in competitors" :key="member.id"><strong>{{ member.display_name }}</strong><span v-if="member.profile">{{ member.profile }}</span></li></ul>
            <p v-else>TBD</p>
          </slot>
        </section>
      </div>
      <footer class="event-announcement-status">
        <div><AppIcon name="activity" :size="20" /><span><small>Status</small><strong>{{ status }}</strong></span></div>
        <div><AppIcon name="user" :size="20" /><span><small>Current spectators</small><strong>{{ (detail.spectator_count ?? 0).toLocaleString() }}</strong></span></div>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.event-announcement-page { margin-block: calc(0px - clamp(var(--space-6), 4vw, var(--space-12))) -3rem; color: var(--event-view-text, #fff0f3); background: var(--event-view-background, #14090f); }
.event-announcement-stage { display: grid; grid-template-rows: 1fr auto; min-height: calc(100dvh - var(--header-height, 4rem) - 1px); background: radial-gradient(ellipse at 50% 35%, var(--event-view-stage-glow, #51112780), transparent 65%); border-bottom: 1px solid var(--event-view-border, #ff729326); }
.event-announcement-stage:has(.event-announcement-backdrop, .event-announcement-pulse, .event-announcement-tools) { position: relative; isolation: isolate; }
.event-announcement-backdrop { position: absolute; inset: 0; z-index: -1; pointer-events: none; }
.event-announcement-tools { position: absolute; inset-block-start: 1rem; inset-inline-start: clamp(.75rem, 1.5vw, 1.75rem); }
.event-announcement-pulse { position: absolute; inset: 0; z-index: -1; pointer-events: none; background: radial-gradient(ellipse at 50% 42%, color-mix(in srgb, var(--event-view-accent) 18%, transparent), transparent 68%); animation: event-announcement-pulse .58s ease-out both; }
.event-announcement-content { display: flex; flex-direction: column; justify-content: center; align-items: center; width: min(100%, 72rem); margin-inline: auto; padding: 2rem 1.25rem; box-sizing: border-box; }
.event-announcement-emblem { width: clamp(6rem, 17dvh, 9rem); margin-bottom: .75rem; }
.event-announcement-heading { text-align: center; }
.event-announcement-kicker { color: var(--event-view-accent, #ff7293); font-size: .62rem; font-weight: 820; letter-spacing: .18em; text-transform: uppercase; }
.event-announcement-heading h1 { margin: .6rem 0 0; font-size: clamp(2.7rem, 7.5dvh, 5.5rem); font-weight: 850; letter-spacing: -.065em; line-height: 1; }
.event-announcement-heading h1 :slotted(span) { color: var(--event-view-accent, #ff7293); }
.event-announcement-schedule { display: flex; align-items: center; justify-content: center; gap: .5rem; margin-top: 1.2rem; color: var(--event-view-schedule, #c397a7); font-size: .75rem; text-align: center; }
.event-announcement-schedule { flex-wrap: wrap; }
.event-announcement-schedule time { display: inline-flex; align-items: center; gap: .5rem; }
.event-announcement-waiting { margin: 1.6rem 0 0; color: var(--event-view-highlight); font-weight: 700; }
.event-announcement-competitors { width: min(100%, 36rem); margin-top: 2.3rem; padding-top: 1.2rem; border-top: 1px solid var(--event-view-border, #ff729326); text-align: center; }
.event-announcement-competitors--wide { width: 100%; }
.event-announcement-competitors--wide h2 { margin-bottom: 1rem; }
.event-announcement-competitors h2 { margin: 0; color: var(--event-view-muted, #b98696); font-size: .6rem; font-weight: 800; letter-spacing: .15em; text-transform: uppercase; }
.event-announcement-competitors p { margin: .7rem 0 0; font-size: 1.1rem; font-weight: 650; }
.event-announcement-competitors ul { display: flex; flex-wrap: wrap; justify-content: center; gap: 1rem; padding: 0; list-style: none; }
.event-announcement-competitors li { display: grid; gap: .3rem; }
.event-announcement-competitors li span { color: var(--event-view-muted, #b98696); font-size: .75rem; }
.event-announcement-status { display: flex; justify-content: space-between; gap: 1rem; padding: 1rem clamp(1rem, 3vw, 3rem); }
.event-announcement-status > div { display: flex; align-items: center; gap: .7rem; color: var(--event-view-status-icon, #d18a9e); }
.event-announcement-status > div > span { display: grid; gap: .2rem; }
.event-announcement-status small { color: var(--event-view-status-label, #ab7a8a); font-size: .55rem; font-weight: 750; letter-spacing: .06em; text-transform: uppercase; }
.event-announcement-status strong { color: var(--event-view-status-text, #f5c7d4); font-size: .75rem; }
@keyframes event-announcement-pulse { from { opacity: 1; } to { opacity: 0; } }
@media (prefers-reduced-motion: reduce) { .event-announcement-pulse { display: none; } }
@media(max-width: 450px) { .event-announcement-heading h1 { font-size: clamp(2.5rem, 11vw, 3.5rem); }.event-announcement-content { padding-inline: 1rem; }.event-announcement-schedule { font-size: .67rem; } }
</style>
