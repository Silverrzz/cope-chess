<script setup lang="ts">
import type { EventDetailResponse } from "@/types/events";
import EventAnnouncement from "../EventAnnouncement.vue";
import SecretQueenArt from "./SecretQueenArt.vue";
import { secretQueenTheme } from "./theme";

defineProps<{ detail: EventDetailResponse; clockOffsetMs?: number; view?: string }>();
</script>

<template>
  <EventAnnouncement :detail="detail" :clock-offset-ms="clockOffsetMs ?? 0" :style="secretQueenTheme">
    <template #art><SecretQueenArt /></template>
  </EventAnnouncement>
</template>
