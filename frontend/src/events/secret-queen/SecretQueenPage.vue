<script setup lang="ts">
import { computed, watch } from "vue";
import { useRouter } from "vue-router";

import AppIcon from "@/components/ui/AppIcon.vue";
import type { EventDetailResponse } from "@/types/events";
import EventAnnouncement from "../EventAnnouncement.vue";
import SecretQueenArena from "./SecretQueenArena.vue";
import SecretQueenArt from "./SecretQueenArt.vue";
import SecretQueenInfo from "./SecretQueenInfo.vue";
import SecretQueenInterference from "./SecretQueenInterference.vue";
import SecretQueenStandings from "./SecretQueenStandings.vue";
import type { SecretQueenCompetition } from "./types";
import { secretQueenTheme } from "./theme";

const props = defineProps<{ detail: EventDetailResponse; clockOffsetMs?: number; view?: string }>();
const router = useRouter();
const competition = computed(() => props.detail.custom as SecretQueenCompetition);
const contests = computed(() => competition.value.contests ?? []);
const plannedContests = computed(() => competition.value.planned_contests ?? []);
const champion = computed(() => props.detail.awards.find(award => award.award_key === "champion"));
const arenaRoute = computed(() => ({ name: "event-arena", params: { slug: props.detail.event.slug } }));
const arenaOpen = computed(() => props.detail.arena_available !== false
  && contests.value.some(contest => contest.games.length)
  && ["live", "intermission"].includes(props.detail.event.status));
const pendingStart = computed(() => competition.value.launch_ready ? "Opening the arena…" : "Waiting for competition setup");

watch(arenaOpen, (open) => {
  if (open && props.view === "event") void router.replace(arenaRoute.value);
});

function openContest(id: number): void {
  const selected = contests.value.find(contest => contest.id === id);
  const game = selected?.games.find(item => item.status === "live")
    ?? selected?.games.find(item => item.status === "finished")
    ?? selected?.games[0];
  if (game) void router.push({ ...arenaRoute.value, query: { game_id: String(game.id) } });
}
</script>

<template>
  <EventAnnouncement v-if="!contests.length" :detail="detail" :clock-offset-ms="clockOffsetMs ?? 0" :countdown-music="view !== 'arena'" :pending-start="pendingStart" :style="secretQueenTheme">
    <template #backdrop><SecretQueenInterference announcement /></template>
    <template #art><SecretQueenArt /></template>
    <template #tools><SecretQueenInfo /></template>
    <template v-if="plannedContests.length" #competitors>
      <SecretQueenStandings :contests="plannedContests" :cast="detail.cast" preview />
    </template>
  </EventAnnouncement>
  <SecretQueenArena v-else-if="view === 'arena'" :detail="detail" :contests="contests" :clock-offset-ms="clockOffsetMs ?? 0" />
  <div v-else class="secret-queen-competition" :style="secretQueenTheme">
    <header class="competition-heading">
      <SecretQueenInterference announcement />
      <SecretQueenInfo class="competition-info" />
      <div class="page-container competition-copy">
        <div class="competition-emblem"><SecretQueenArt /></div>
        <span>{{ champion ? 'Secret Queen champion' : 'Secret Queen' }}</span>
        <h1>{{ champion?.recipient_label ?? detail.stages.find(stage => stage.status === 'active')?.title ?? detail.event.title }}</h1>
        <RouterLink v-if="arenaOpen || champion" class="competition-action" :to="arenaRoute">
          {{ champion ? 'Replay games' : 'Enter arena' }} <AppIcon name="arrow-right" :size="18" />
        </RouterLink>
      </div>
    </header>
    <div class="page-container"><SecretQueenStandings :contests="contests" :cast="detail.cast" @select="openContest" /></div>
  </div>
</template>

<style scoped>
.secret-queen-competition { min-height: 100dvh; padding-bottom: 4rem; color: var(--event-view-text); background: var(--event-view-background); }
.competition-heading { position: relative; isolation: isolate; overflow: hidden; margin-bottom: 2rem; border-bottom: 1px solid var(--event-view-border); }
.competition-info { position: absolute; z-index: 1; inset-block-start: 1rem; inset-inline-start: clamp(.75rem, 1.5vw, 1.75rem); }
.competition-copy { position: relative; display: grid; justify-items: center; gap: .65rem; padding-block: 2.5rem; text-align: center; }
.competition-emblem { width: 6rem; }
.competition-copy > span { color: var(--event-view-accent); font-size: .72rem; font-weight: 800; letter-spacing: .13em; text-transform: uppercase; }
.competition-copy h1 { font-size: clamp(2.5rem, 6vw, 5rem); line-height: 1; letter-spacing: -.055em; }
.competition-action { display: flex; gap: .4rem; align-items: center; margin-top: .55rem; color: var(--event-view-highlight); }
</style>
