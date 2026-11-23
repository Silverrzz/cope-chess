<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";

import AppIcon from "@/components/ui/AppIcon.vue";
import { localDateFormatter, timestampDate } from "@/utils/time";
import type { EventDetailResponse } from "@/types/events";
import SecretQueenArt from "./SecretQueenArt.vue";

const props = defineProps<{ detail: EventDetailResponse }>();
const event = computed(() => props.detail.event);
const competitors = computed(() => props.detail.cast.filter(member => member.parent_id === null));
const dateFormat = localDateFormatter({
  weekday: "long", day: "numeric", month: "long", hour: "numeric", minute: "2-digit", timeZoneName: "short",
});
const startLabel = computed(() => event.value.scheduled_start_at
  ? dateFormat.format(timestampDate(event.value.scheduled_start_at))
  : "Date TBD");
</script>

<template>
  <section class="secret-queen-page" aria-labelledby="secret-queen-title">
    <div class="page-container secret-queen-page__content">
      <RouterLink class="secret-queen-page__back" :to="{ name: 'events' }"><AppIcon name="arrow-left" :size="16" /> Events</RouterLink>
      <header class="secret-queen-page__hero">
        <div>
          <h1 id="secret-queen-title">{{ event.title }}</h1>
          <p v-if="event.summary" class="secret-queen-page__summary">{{ event.summary }}</p>
          <p class="secret-queen-page__date">
            <AppIcon name="clock" :size="17" />
            <time v-if="event.scheduled_start_at" :datetime="event.scheduled_start_at">{{ startLabel }}</time>
            <span v-else>{{ startLabel }}</span>
          </p>
        </div>
        <SecretQueenArt class="secret-queen-page__art" />
      </header>
      <section class="secret-queen-page__competitors" aria-labelledby="secret-queen-competitors">
        <h2 id="secret-queen-competitors">Competitors</h2>
        <ul v-if="competitors.length">
          <li v-for="member in competitors" :key="member.id">{{ member.display_name }}</li>
        </ul>
        <p v-else>TBD</p>
      </section>
    </div>
  </section>
</template>

<style scoped>
.secret-queen-page {
  min-height: calc(100dvh - var(--header-height, 4rem));
  background: linear-gradient(128deg, #0b0910 40%, #160e20 78%, #0b0910);
  color: #faf7ff;
}
.secret-queen-page__content { padding-block: clamp(1.5rem, 4vw, 4rem) clamp(3rem, 7vw, 7rem); }
.secret-queen-page__back { display: inline-flex; align-items: center; gap: .55rem; color: #d8c9e7; font-size: .86rem; font-weight: 650; text-underline-offset: .25em; }
.secret-queen-page__hero {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr);
  align-items: center;
  gap: clamp(1.75rem, 4vw, 4rem);
  min-height: clamp(23rem, 49vh, 34rem);
  padding-block: 1.75rem 2.75rem;
}
.secret-queen-page__art { min-height: 100%; }
.secret-queen-page__hero h1 {
  font-size: clamp(3rem, 6.7vw, 7rem);
  font-weight: 820;
  letter-spacing: -.065em;
  line-height: .98;
  text-wrap: balance;
}
.secret-queen-page__summary { margin-top: 1.25rem; color: #d3c9dc; font-size: clamp(1rem, 1.6vw, 1.2rem); }
.secret-queen-page__date { display: flex; align-items: center; gap: .65rem; margin-top: 1.65rem; color: #e8d6ff; font-size: .95rem; font-weight: 650; }
.secret-queen-page__competitors { padding-block: clamp(1.5rem, 4vw, 2.5rem); border-top: 1px solid #bba2d440; }
.secret-queen-page__competitors h2 { margin-bottom: .65rem; font-size: 1.25rem; }
.secret-queen-page__competitors p { color: #c8b9d9; }
.secret-queen-page__competitors ul { display: flex; flex-wrap: wrap; gap: .65rem 1.45rem; margin: 0; padding: 0; list-style: none; }
@media (max-width: 38rem) {
  .secret-queen-page__hero { grid-template-columns: minmax(0, 1fr); min-height: 0; }
  .secret-queen-page__art { width: min(100%, 18rem); justify-self: center; }
}
</style>
