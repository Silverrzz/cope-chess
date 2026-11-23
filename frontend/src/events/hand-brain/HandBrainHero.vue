<script setup lang="ts">
import type { EventSummary } from "@/types/events";
import EventArchiveHero from "../EventArchiveHero.vue";
import HandBrainArt from "./HandBrainArt.vue";

defineProps<{ summary: EventSummary; current?: boolean; nowMs?: number }>();
</script>

<template>
  <EventArchiveHero :summary="summary" :current="current" :now-ms="nowMs">
    <template #title>Hand &amp;<br>Brain</template>
    <template #art><HandBrainArt /></template>
  </EventArchiveHero>
</template>
