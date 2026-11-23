<script setup lang="ts">
import type { EventSummary } from "@/types/events";
import EventArchiveHero from "../EventArchiveHero.vue";
import SecretQueenArt from "./SecretQueenArt.vue";
import SecretQueenInterference from "./SecretQueenInterference.vue";
import { secretQueenTheme } from "./theme";

defineProps<{ summary: EventSummary; current?: boolean; nowMs?: number }>();
</script>

<template>
  <EventArchiveHero :summary="summary" :style="secretQueenTheme">
    <template v-if="current" #date-placeholder><span>Date TBD</span></template>
    <template #backdrop><SecretQueenInterference /></template>
    <template #art><SecretQueenArt /></template>
  </EventArchiveHero>
</template>
