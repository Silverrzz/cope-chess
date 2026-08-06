<script setup lang="ts">
import { computed } from "vue";

import StatusPill from "@/components/public/StatusPill.vue";
import type { EventSummary } from "@/types/events";

const props = defineProps<{ item: EventSummary }>();

const accent = computed(() => {
  const value = props.item.record.theme.accent ?? props.item.record.theme.accent_color;
  return typeof value === "string" && /^#[0-9a-f]{3,8}$/i.test(value) ? value : "var(--color-accent, #315fcc)";
});

const schedule = computed(() => {
  const value = props.item.next_session?.scheduled_start_at ?? props.item.record.scheduled_start_at;
  if (!value) return "Date to be announced";
  return new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
});

const eyebrow = computed(() => {
  if (props.item.record.status === "live") return "Happening now";
  if (props.item.record.featured) return "Featured event";
  if (props.item.record.status === "completed") return "From the archive";
  return "Special event";
});
</script>

<template>
  <article class="event-card" :style="{ '--event-accent': accent }">
    <div class="event-card__glow" aria-hidden="true"></div>
    <header>
      <span class="event-card__eyebrow">{{ eyebrow }}</span>
      <StatusPill :status="item.record.status" />
    </header>
    <div class="event-card__copy">
      <p v-if="item.record.subtitle" class="event-card__subtitle">{{ item.record.subtitle }}</p>
      <h2><RouterLink :to="`/events/${item.record.slug}`">{{ item.record.title }}</RouterLink></h2>
      <p class="event-card__summary">{{ item.record.summary || "A one-off COPE Chess exhibition." }}</p>
    </div>
    <div v-if="item.next_session" class="event-card__next">
      <span>{{ item.next_session.status === "live" ? "On now" : "Up next" }}</span>
      <strong>{{ item.next_session.title }}</strong>
    </div>
    <footer>
      <time :datetime="item.next_session?.scheduled_start_at ?? item.record.scheduled_start_at ?? undefined">{{ schedule }}</time>
      <span>{{ item.counts.cast }} cast · {{ item.counts.contests }} contests</span>
      <RouterLink :to="`/events/${item.record.slug}`" :aria-label="`Explore ${item.record.title}`">Explore event <span aria-hidden="true">→</span></RouterLink>
    </footer>
  </article>
</template>

<style scoped>
.event-card {
  position: relative;
  isolation: isolate;
  display: grid;
  min-height: 24rem;
  overflow: hidden;
  padding: clamp(1.2rem, 3vw, 1.75rem);
  border: 1px solid color-mix(in srgb, var(--event-accent) 24%, var(--color-border, #d5dbe1));
  border-radius: clamp(.8rem, 2vw, 1.2rem);
  background:
    linear-gradient(145deg, color-mix(in srgb, var(--event-accent) 8%, var(--color-surface, #fff)), var(--color-surface, #fff) 52%);
  box-shadow: 0 .75rem 2.6rem color-mix(in srgb, #000 7%, transparent);
  transition: transform 180ms ease, border-color 180ms ease, box-shadow 180ms ease;
}

.event-card:hover {
  border-color: color-mix(in srgb, var(--event-accent) 58%, var(--color-border, #d5dbe1));
  box-shadow: 0 1.2rem 3.2rem color-mix(in srgb, #000 12%, transparent);
  transform: translateY(-3px);
}

.event-card__glow {
  position: absolute;
  z-index: -1;
  top: -8rem;
  right: -7rem;
  width: 18rem;
  height: 18rem;
  border-radius: 50%;
  background: color-mix(in srgb, var(--event-accent) 22%, transparent);
  filter: blur(3rem);
}

.event-card header,
.event-card footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.event-card__eyebrow {
  color: var(--event-accent);
  font-size: .67rem;
  font-weight: 800;
  letter-spacing: .13em;
  text-transform: uppercase;
}

.event-card__copy {
  align-self: center;
  padding-block: 2rem;
}

.event-card__copy p,
.event-card h2 {
  margin: 0;
}

.event-card__subtitle {
  margin-block-end: .5rem !important;
  color: var(--event-accent);
  font-size: .76rem;
  font-weight: 700;
}

.event-card h2 {
  max-width: 18ch;
  font-size: clamp(1.65rem, 4vw, 2.45rem);
  letter-spacing: -.045em;
  line-height: 1.02;
  text-wrap: balance;
}

.event-card h2 a {
  color: var(--color-text, #17202a);
  text-decoration: none;
}

.event-card h2 a::after {
  position: absolute;
  inset: 0;
  content: "";
}

.event-card__summary {
  max-width: 48ch;
  margin-block-start: .85rem !important;
  color: var(--color-text-muted, #607080);
  font-size: .9rem;
  line-height: 1.65;
}

.event-card__next {
  display: grid;
  gap: .18rem;
  align-self: end;
  margin-block-end: 1rem;
  padding: .7rem .8rem;
  border-left: 2px solid var(--event-accent);
  background: color-mix(in srgb, var(--event-accent) 7%, transparent);
}

.event-card__next span {
  color: var(--color-text-muted, #607080);
  font-size: .62rem;
  font-weight: 760;
  letter-spacing: .08em;
  text-transform: uppercase;
}

.event-card__next strong {
  font-size: .8rem;
}

.event-card footer {
  flex-wrap: wrap;
  padding-block-start: 1rem;
  border-top: 1px solid var(--color-border, #d5dbe1);
  color: var(--color-text-muted, #607080);
  font-size: .68rem;
}

.event-card footer a {
  position: relative;
  z-index: 1;
  margin-left: auto;
  color: var(--event-accent);
  font-size: .74rem;
  font-weight: 760;
  text-decoration: none;
}

@media (prefers-reduced-motion: reduce) {
  .event-card { transition: none; }
}
</style>
