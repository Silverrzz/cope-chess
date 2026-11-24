<script setup lang="ts">
import { computed } from "vue";

import AppIcon from "@/components/ui/AppIcon.vue";
import type { EventDetailResponse } from "@/types/events";
import EventAnnouncement from "../EventAnnouncement.vue";
import SecretQueenArena from "./SecretQueenArena.vue";
import SecretQueenArt from "./SecretQueenArt.vue";
import SecretQueenInterference from "./SecretQueenInterference.vue";
import SecretQueenStandings from "./SecretQueenStandings.vue";
import type { SecretQueenCompetition } from "./types";
import { secretQueenTheme } from "./theme";

const props = defineProps<{ detail: EventDetailResponse; clockOffsetMs?: number; view?: string }>();
const competition = computed(() => props.detail.custom as SecretQueenCompetition);
const contests = computed(() => competition.value.contests ?? []);
const champion = computed(() => props.detail.awards.find(award => award.award_key === "champion"));
</script>

<template>
  <EventAnnouncement v-if="!contests.length" :detail="detail" :clock-offset-ms="clockOffsetMs ?? 0" :style="secretQueenTheme">
    <template #backdrop><SecretQueenInterference announcement /></template>
    <template #art><SecretQueenArt /></template>
  </EventAnnouncement>
  <SecretQueenArena v-else-if="view === 'arena'" :detail="detail" :contests="contests" :style="secretQueenTheme" />
  <div v-else class="secret-queen-competition" :style="secretQueenTheme">
    <header class="competition-heading">
      <SecretQueenInterference announcement />
      <div class="page-container competition-copy">
        <div class="competition-emblem"><SecretQueenArt /></div>
        <span>{{ champion ? 'Secret Queen champion' : 'Secret Queen' }}</span>
        <h1>{{ champion?.recipient_label ?? detail.stages.find(stage => stage.status === 'active')?.title ?? detail.event.title }}</h1>
        <RouterLink class="competition-action" :to="{ name: 'event-arena', params: { slug: detail.event.slug } }">
          {{ champion ? 'Replay games' : 'Enter arena' }} <AppIcon name="arrow-right" :size="18" />
        </RouterLink>
      </div>
    </header>
    <div class="page-container"><SecretQueenStandings :contests="contests" :cast="detail.cast" /></div>
  </div>
</template>

<style scoped>
.secret-queen-competition { min-height: 100dvh; padding-bottom: 4rem; color: var(--event-view-text); background: var(--event-view-background); }
.competition-heading { position: relative; isolation: isolate; overflow: hidden; margin-bottom: 2rem; border-bottom: 1px solid var(--event-view-border); }
.competition-copy { position: relative; display: grid; justify-items: center; gap: .65rem; padding-block: 2.5rem; text-align: center; }
.competition-emblem { width: 6rem; }
.competition-copy > span { color: var(--event-view-accent); font-size: .72rem; font-weight: 800; letter-spacing: .13em; text-transform: uppercase; }
.competition-copy h1 { font-size: clamp(2.5rem, 6vw, 5rem); line-height: 1; letter-spacing: -.055em; }
.competition-action { display: flex; gap: .4rem; align-items: center; margin-top: .55rem; color: var(--event-view-highlight); }
</style>
