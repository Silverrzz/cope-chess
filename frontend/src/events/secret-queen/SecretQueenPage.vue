<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";

import { localDateFormatter, timestampDate } from "@/utils/time";
import type { EventDetailResponse } from "@/types/events";
import SecretQueenSignal from "./SecretQueenSignal.vue";

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
    <SecretQueenSignal class="secret-queen-page__art" />
    <div class="secret-queen-page__content">
      <RouterLink class="secret-queen-page__back" :to="{ name: 'events' }"><span aria-hidden="true">←</span> Events</RouterLink>
      <header>
        <h1 id="secret-queen-title">{{ event.title }}</h1>
        <time v-if="event.scheduled_start_at" :datetime="event.scheduled_start_at">{{ startLabel }}</time>
        <p v-else>{{ startLabel }}</p>
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
  position: relative;
  isolation: isolate;
  min-height: calc(100dvh - var(--header-height, 4rem));
  overflow: hidden;
  background: #08060c;
  color: #f7f5fa;
}
.secret-queen-page__art { --queen-signal-inset: -8% -8% 12% 17%; z-index: -2; }
.secret-queen-page::before {
  position: absolute;
  inset: 0;
  z-index: -1;
  content: "";
  background:
    linear-gradient(0deg, #08060c 5%, #08060cce 23%, transparent 76%),
    linear-gradient(90deg, #08060c 0%, #08060cb5 20%, transparent 61%);
}
.secret-queen-page__content {
  display: grid;
  grid-template-rows: auto 1fr auto;
  width: min(100%, 83rem);
  min-height: inherit;
  margin: 0 auto;
  padding: clamp(1.25rem, 4vw, 4rem) clamp(1.25rem, 6vw, 6rem) clamp(2.5rem, 5vw, 5rem);
}
.secret-queen-page__back { justify-self: start; color: #e6d9f6; font-size: .88rem; font-weight: 650; text-underline-offset: .25em; }
.secret-queen-page__back span { margin-right: .35rem; }
.secret-queen-page__content header { align-self: end; padding-block: clamp(13rem, 31vh, 27rem) 2.4rem; }
.secret-queen-page__content h1 {
  margin: 0 0 1.45rem;
  color: inherit;
  font: 400 clamp(3.65rem, 10vw, 10rem)/.9 Impact, Haettenschweiler, "Arial Narrow", var(--font-sans);
  letter-spacing: -.01em;
  text-wrap: balance;
}
.secret-queen-page__content header :is(time, p) { margin: 0; color: #eadff4; font-size: clamp(1rem, 2vw, 1.25rem); font-weight: 650; }
.secret-queen-page__competitors { padding-top: 1.15rem; border-top: 1px solid #bba2d451; }
.secret-queen-page__competitors h2 { margin: 0 0 .65rem; color: inherit; font-size: 1.1rem; }
.secret-queen-page__competitors p { margin: 0; color: #e5d8ee; }
.secret-queen-page__competitors ul { display: flex; flex-wrap: wrap; gap: .65rem 1.45rem; margin: 0; padding: 0; list-style: none; }
@media (max-width: 42rem) {
  .secret-queen-page__art { --queen-signal-inset: -2% -30% 24% -16%; }
  .secret-queen-page__content header { padding-bottom: 1.6rem; }
}
</style>
