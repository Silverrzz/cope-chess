<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";
import type { EventSummary } from "@/types/events";
import HandBrainArt from "./HandBrainArt.vue";
const props = defineProps<{ summary: EventSummary; current?: boolean; nowMs?: number }>();
const record = computed(() => props.summary.record);
const date = computed(() => new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", timeZone: "Europe/London" }).format(new Date(record.value.scheduled_start_at!)));
const time = computed(() => new Intl.DateTimeFormat("en-GB", { hour: "numeric", minute: "2-digit", hour12: true, timeZone: "Europe/London", timeZoneName: "short" }).format(new Date(record.value.scheduled_start_at!)));
</script>

<template>
  <RouterLink class="hb-hero" :to="{ name: 'event', params: { slug: record.slug } }" :aria-label="`Open ${record.title}`">
    <div class="hb-copy">
      <span class="hb-kicker">Engine · {{ record.status === 'live' ? 'Live' : 'Upcoming' }}</span>
      <h2>Hand &amp;<br/>Brain</h2>
      <div class="hb-meta">
        <time :datetime="record.scheduled_start_at ?? undefined">{{ date }} · {{ time }}</time>
        <strong>View event <span aria-hidden="true">&#8599;</span></strong>
      </div>
    </div>
    <div class="hb-art"><HandBrainArt /></div>
  </RouterLink>
</template>

<style scoped>
.hb-hero { position: relative; display: grid; align-items: end; overflow: hidden; min-height: clamp(30rem, 72vh, 46rem); padding: clamp(2rem, 6vw, 6.5rem); box-sizing: border-box; color: #fff0f3; text-decoration: none; background: radial-gradient(ellipse at 85% 45%, #481024 0, transparent 65%), #14090f; }
.hb-copy { position: relative; z-index: 1; width: min(55rem, 72%); }
.hb-kicker { color: #ff7293; font-size: .72rem; font-weight: 850; letter-spacing: .17em; text-transform: uppercase; }
h2 { margin: 1rem 0 0; font-size: clamp(3.5rem, 8.5vw, 8.2rem); font-weight: 900; letter-spacing: -.075em; line-height: .84; text-transform: uppercase; }
.hb-meta { display: flex; align-items: center; flex-wrap: wrap; gap: .7rem 1.4rem; margin-top: 1.7rem; color: #b98696; font-size: .7rem; font-weight: 680; }
.hb-meta strong { color: #ff7293; text-underline-offset: .3em; }
.hb-meta strong span { display: inline-block; margin-left: .5rem; }
.hb-hero:hover .hb-meta strong { color: #ffc3d4; }
.hb-hero:focus-visible { outline: 2px solid #ff7293; outline-offset: 6px; }
.hb-art { position: absolute; top: 50%; right: clamp(2rem, 7vw, 8rem); width: clamp(18rem, 32vw, 33rem); transform: translateY(-50%); }
@media(max-width: 700px) { .hb-hero { min-height: 31rem; padding: 2rem 1.25rem; }.hb-copy { width: 100%; }.hb-art { width: 18rem; top: 30%; right: -3rem; opacity: .45; }h2 { font-size: clamp(4rem, 14vw, 6rem); } }
</style>
