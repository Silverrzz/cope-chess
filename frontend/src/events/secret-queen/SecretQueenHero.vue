<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";

import { localDateFormatter, timestampDate } from "@/utils/time";
import type { EventSummary } from "@/types/events";
import SecretQueenSignal from "./SecretQueenSignal.vue";

const props = defineProps<{ summary: Pick<EventSummary, "record">; current?: boolean }>();
const event = computed(() => props.summary.record);
const dateFormat = localDateFormatter({ day: "numeric", month: "long", hour: "numeric", minute: "2-digit", timeZoneName: "short" });
const startLabel = computed(() => event.value.scheduled_start_at
  ? dateFormat.format(timestampDate(event.value.scheduled_start_at))
  : "Date TBD");
</script>

<template>
  <RouterLink class="secret-queen-hero" :to="{ name: 'event', params: { slug: event.slug } }">
    <SecretQueenSignal class="secret-queen-hero__art" />
    <div class="secret-queen-hero__content">
      <time v-if="event.scheduled_start_at" :datetime="event.scheduled_start_at">{{ startLabel }}</time>
      <span v-else-if="current">{{ startLabel }}</span>
      <h2>{{ event.title }}</h2>
      <span class="secret-queen-hero__action">View event <span aria-hidden="true">↗</span></span>
    </div>
  </RouterLink>
</template>

<style scoped>
.secret-queen-hero {
  position: relative;
  isolation: isolate;
  display: grid;
  align-items: center;
  min-height: clamp(25rem, 65vh, 42rem);
  overflow: hidden;
  padding: clamp(1.25rem, 6vw, 6rem);
  color: #f7f5fa;
  background: #08060c;
  text-decoration: none;
}
.secret-queen-hero__art { --queen-signal-inset: 0 -5% 0 30%; z-index: -2; }
.secret-queen-hero::before {
  position: absolute;
  inset: 0;
  z-index: -1;
  content: "";
  background: linear-gradient(96deg, #08060c 9%, #08060c 24%, #08060ce0 41%, transparent 75%);
}
.secret-queen-hero__content { width: fit-content; max-width: 100%; }
.secret-queen-hero__content > time,
.secret-queen-hero__content > span:first-child { color: #ded1ef; font-size: .88rem; }
.secret-queen-hero h2 {
  margin: .8rem 0 1.5rem;
  color: inherit;
  font: 400 clamp(3.25rem, 9vw, 9.5rem)/.9 Impact, Haettenschweiler, "Arial Narrow", var(--font-sans);
  letter-spacing: -.01em;
  text-wrap: balance;
}
.secret-queen-hero__action {
  display: inline-flex;
  align-items: center;
  gap: 1rem;
  padding: .62rem .88rem;
  border: 1px solid #f7f5fa;
  color: #f7f5fa;
  font-size: .84rem;
  font-weight: 700;
}
.secret-queen-hero:hover .secret-queen-hero__action { background: #f7f5fa; color: #08060c; }
.secret-queen-hero:focus-visible { outline: 2px solid #c5a3ff; outline-offset: -4px; }
@media (max-width: 42rem) {
  .secret-queen-hero { align-items: end; }
  .secret-queen-hero__art { --queen-signal-inset: -3% -38% 0 0; }
  .secret-queen-hero::before { background: linear-gradient(0deg, #08060c 0%, #08060ce0 27%, transparent 78%); }
}
</style>
