<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";

import AppIcon from "@/components/ui/AppIcon.vue";
import type { EventSummary } from "@/types/events";

const props = defineProps<{ summary: EventSummary; current?: boolean; nowMs: number }>();
const event = computed(() => props.summary.record);
const target = computed(() => props.current
  ? { name: "event", params: { slug: event.value.slug } }
  : { name: "event-arena", params: { slug: event.value.slug } });
</script>

<template>
  <RouterLink class="generic-event" :to="target">
    <span>{{ current ? "Upcoming" : "Completed" }}</span>
    <h2>{{ event.title }}</h2>
    <strong>{{ current ? "View event" : "View arena" }} <AppIcon name="arrow-right" :size="18" /></strong>
  </RouterLink>
</template>

<style scoped>
.generic-event { display: grid; min-height: clamp(28rem, 66vh, 42rem); align-content: end; padding: clamp(2rem, 7vw, 7rem); background: linear-gradient(135deg, color-mix(in srgb, var(--color-accent) 30%, #08101b), #08101b 65%); color: white; text-decoration: none; }
.generic-event > span { color: #9dbfff; font-size: .72rem; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; }
.generic-event h2 { max-width: 13ch; margin: .7rem 0 0; font-size: clamp(3rem, 8vw, 7.5rem); letter-spacing: -.07em; line-height: .86; }
.generic-event strong { display: inline-flex; align-items: center; gap: .4rem; margin-top: 1rem; font-size: .75rem; }
</style>
