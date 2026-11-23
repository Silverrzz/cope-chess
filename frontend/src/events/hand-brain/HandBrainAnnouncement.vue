<script setup lang="ts">
import type { EventDetailResponse } from "@/types/events";
import EventAnnouncement from "../EventAnnouncement.vue";
import HandBrainArt from "./HandBrainArt.vue";

defineProps<{ detail: EventDetailResponse; clockOffsetMs?: number; view?: string }>();
</script>

<template>
  <EventAnnouncement :detail="detail" :clock-offset-ms="clockOffsetMs ?? 0">
    <template #title>Hand <span>&amp;</span> Brain</template>
    <template #art><HandBrainArt /></template>
  </EventAnnouncement>
</template>
