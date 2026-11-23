<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";

import AppIcon from "@/components/ui/AppIcon.vue";
import { localDateFormatter, timestampDate } from "@/utils/time";
import type { EventSummary } from "@/types/events";
import SecretQueenArt from "./SecretQueenArt.vue";

const props = defineProps<{ summary: EventSummary; current?: boolean }>();
const event = computed(() => props.summary.record);
const dateFormat = localDateFormatter({ weekday: "long", day: "numeric", month: "long", hour: "numeric", minute: "2-digit", timeZoneName: "short" });
const timestamp = computed(() => props.current
  ? event.value.scheduled_start_at
  : event.value.finished_at ?? event.value.scheduled_start_at);
</script>

<template>
  <RouterLink class="secret-queen-hero" :to="{ name: 'event', params: { slug: event.slug } }">
    <div class="page-container secret-queen-hero__layout">
      <div>
        <h2>{{ event.title }}</h2>
        <p v-if="event.summary" class="secret-queen-hero__summary">{{ event.summary }}</p>
        <div class="secret-queen-hero__meta">
          <time v-if="timestamp" :datetime="timestamp">{{ dateFormat.format(timestampDate(timestamp)) }}</time>
          <span v-else-if="current">Date TBD</span>
          <strong>View event <AppIcon name="arrow-right" :size="17" /></strong>
        </div>
      </div>
      <SecretQueenArt class="secret-queen-hero__art" />
    </div>
  </RouterLink>
</template>

<style scoped>
.secret-queen-hero {
  display: block;
  border-block: 1px solid #342642;
  background: linear-gradient(115deg, #0b0910 36%, #180f23);
  color: #faf7ff;
  text-decoration: none;
}
.secret-queen-hero__layout {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr);
  align-items: center;
  gap: clamp(1.75rem, 4vw, 4rem);
  min-height: clamp(25rem, 55vh, 35rem);
  padding-block: clamp(2.75rem, 7vw, 6rem);
}
.secret-queen-hero__art { min-height: 100%; }
.secret-queen-hero h2 {
  font-size: clamp(3rem, 6.7vw, 7rem);
  font-weight: 820;
  letter-spacing: -.065em;
  line-height: .98;
  text-wrap: balance;
}
.secret-queen-hero__summary { margin-top: 1.25rem; color: #d3c9dc; font-size: clamp(1rem, 1.6vw, 1.2rem); }
.secret-queen-hero__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: .7rem 1.55rem;
  margin-top: 1.7rem;
  color: #c8b9d9;
  font-size: .9rem;
}
.secret-queen-hero__meta strong { display: inline-flex; align-items: center; gap: .5rem; color: #e8d6ff; }
.secret-queen-hero:hover strong { text-decoration: underline; text-underline-offset: .22em; }
.secret-queen-hero:focus-visible { outline: 2px solid #c5a3ff; outline-offset: -4px; }
@media (max-width: 38rem) {
  .secret-queen-hero__layout { grid-template-columns: minmax(0, 1fr); min-height: 0; }
  .secret-queen-hero__art { width: min(100%, 18rem); justify-self: center; }
}
</style>
