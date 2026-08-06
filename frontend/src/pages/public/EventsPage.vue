<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api } from "@/api/client";
import ContentState from "@/components/public/ContentState.vue";
import EventCard from "@/components/public/EventCard.vue";
import { errorMessage } from "@/components/public/format";
import type { EventListResponse, EventSummary } from "@/types/events";

const route = useRoute();
const router = useRouter();
const data = ref<EventListResponse | null>(null);
const loading = ref(true);
const loadError = ref("");
let controller: AbortController | null = null;

const search = computed({
  get: () => typeof route.query.q === "string" ? route.query.q : "",
  set: (value: string) => updateQuery("q", value.trim() || undefined),
});
const filter = computed({
  get: () => typeof route.query.view === "string" ? route.query.view : "all",
  set: (value: string) => updateQuery("view", value === "all" ? undefined : value),
});

const filters = [
  { value: "all", label: "All" },
  { value: "live", label: "Live" },
  { value: "upcoming", label: "Upcoming" },
  { value: "archive", label: "Archive" },
];

const filtered = computed(() => {
  const needle = search.value.trim().toLocaleLowerCase();
  return (data.value?.events ?? []).filter((item) => {
    const status = item.record.status;
    const matchesView = filter.value === "all"
      || (filter.value === "live" && ["live", "intermission"].includes(status))
      || (filter.value === "upcoming" && ["announced", "scheduled", "postponed"].includes(status))
      || (filter.value === "archive" && ["completed", "cancelled"].includes(status));
    if (!matchesView) return false;
    if (!needle) return true;
    return [item.record.title, item.record.subtitle, item.record.summary]
      .some((value) => value.toLocaleLowerCase().includes(needle));
  });
});

const featured = computed<EventSummary | null>(() => {
  if (search.value || filter.value !== "all") return null;
  return filtered.value.find((item) => item.record.featured) ?? null;
});
const remaining = computed(() => featured.value ? filtered.value.filter((item) => item.record.id !== featured.value?.record.id) : filtered.value);

onMounted(load);
onBeforeUnmount(() => controller?.abort());

async function load(): Promise<void> {
  controller?.abort();
  controller = new AbortController();
  loading.value = true;
  loadError.value = "";
  try {
    data.value = await api.get<EventListResponse>("/api/events", { signal: controller.signal });
  } catch (error) {
    if ((error as { name?: string })?.name !== "AbortError") {
      loadError.value = errorMessage(error, "Events could not be loaded.");
    }
  } finally {
    loading.value = false;
  }
}

function updateQuery(key: string, value: string | undefined): void {
  const query = { ...route.query };
  if (value) query[key] = value;
  else delete query[key];
  void router.replace({ query });
}

function reset(): void {
  void router.replace({ query: {} });
}
</script>

<template>
  <div class="events-page">
    <ContentState v-if="loading" kind="loading" title="Opening the events calendar" />
    <ContentState v-else-if="loadError" kind="error" :message="loadError" action-label="Try again" @action="load" />
    <template v-else-if="data">
      <section class="events-hero">
        <div class="page-container events-hero__inner">
          <div class="events-hero__copy">
            <span>COPE exhibitions</span>
            <h1>Chess, outside the usual rules.</h1>
            <p>One-off spectacles, experimental formats, and ambitious matchups. Events are built to be memorable, not to shape the rating list.</p>
          </div>
          <dl>
            <div><dt>Live</dt><dd>{{ data.event_stats.live }}</dd></div>
            <div><dt>Coming up</dt><dd>{{ data.event_stats.upcoming }}</dd></div>
            <div><dt>In the archive</dt><dd>{{ data.event_stats.completed }}</dd></div>
          </dl>
        </div>
      </section>

      <div class="page-container events-content">
        <section class="event-controls" aria-label="Filter events">
          <div class="event-filters">
            <button v-for="item in filters" :key="item.value" type="button" :class="{ active: filter === item.value }" @click="filter = item.value">{{ item.label }}</button>
          </div>
          <label class="event-search">
            <span class="sr-only">Search events</span>
            <input v-model="search" type="search" placeholder="Search events">
          </label>
        </section>

        <section v-if="featured" class="featured-event" aria-labelledby="featured-title">
          <div class="section-heading"><span>In the spotlight</span><h2 id="featured-title">The next big thing</h2></div>
          <EventCard :item="featured" />
        </section>

        <section v-if="remaining.length" class="event-list" aria-labelledby="events-title">
          <div class="section-heading"><span>{{ filtered.length }} event{{ filtered.length === 1 ? "" : "s" }}</span><h2 id="events-title">{{ featured ? "More events" : "Events" }}</h2></div>
          <div class="event-grid"><EventCard v-for="item in remaining" :key="item.record.id" :item="item" /></div>
        </section>

        <ContentState v-if="data.events.length && !filtered.length" kind="empty" title="No events match this view" message="Try another status or clear your search." action-label="Reset filters" @action="reset" />

        <section v-if="!data.events.length" class="events-empty">
          <span aria-hidden="true">◇</span>
          <h2>The arena is being prepared.</h2>
          <p>Special events will appear here when they are announced.</p>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.events-page { margin-block-start: calc(0px - clamp(var(--space-6), 4vw, var(--space-12))); margin-block-end: -3rem; }
.events-hero { overflow: hidden; border-bottom: 1px solid var(--color-border, #d5dbe1); background: radial-gradient(circle at 82% 10%, color-mix(in srgb, var(--color-accent, #315fcc) 18%, transparent), transparent 34%), linear-gradient(145deg, var(--color-bg-subtle, #f5f7fa), var(--color-bg, #fff)); }
.events-hero__inner { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: end; gap: clamp(2rem, 8vw, 8rem); padding-block: clamp(4rem, 10vw, 8rem) clamp(2.5rem, 6vw, 5rem); }
.events-hero__copy > span, .section-heading > span { color: var(--color-accent, #315fcc); font-size: .68rem; font-weight: 800; letter-spacing: .16em; text-transform: uppercase; }
.events-hero h1 { max-width: 13ch; margin: .8rem 0 0; font-size: clamp(2.8rem, 8vw, 6.6rem); letter-spacing: -.065em; line-height: .9; text-wrap: balance; }
.events-hero p { max-width: 54ch; margin: 1.25rem 0 0; color: var(--color-text-muted, #607080); font-size: clamp(.95rem, 1.6vw, 1.12rem); line-height: 1.7; }
.events-hero dl { display: grid; gap: 1px; overflow: hidden; min-width: 10rem; margin: 0; border: 1px solid var(--color-border, #d5dbe1); border-radius: .8rem; background: var(--color-border, #d5dbe1); }
.events-hero dl div { min-width: 10rem; padding: 1rem 1.2rem; background: color-mix(in srgb, var(--color-surface, #fff) 88%, transparent); }
.events-hero dt { color: var(--color-text-muted, #607080); font-size: .62rem; font-weight: 750; letter-spacing: .08em; text-transform: uppercase; }
.events-hero dd { margin: .15rem 0 0; font-size: 1.45rem; font-weight: 800; }
.events-content { display: grid; gap: clamp(2.5rem, 6vw, 5rem); padding-block: 1.2rem 4rem; }
.event-controls { position: sticky; z-index: 10; top: calc(var(--header-height, 4rem) + .5rem); display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: .55rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: .8rem; background: color-mix(in srgb, var(--color-surface, #fff) 88%, transparent); box-shadow: 0 .5rem 1.5rem color-mix(in srgb, #000 6%, transparent); backdrop-filter: blur(16px); }
.event-filters { display: flex; gap: .25rem; overflow-x: auto; }
.event-filters button { min-height: 2.3rem; padding-inline: .85rem; border: 0; border-radius: .55rem; background: transparent; color: var(--color-text-muted, #607080); font: inherit; font-size: .75rem; font-weight: 720; cursor: pointer; }
.event-filters button:hover { color: var(--color-text, #17202a); background: var(--color-surface-hover, #edf1f5); }
.event-filters button.active { color: var(--color-accent, #315fcc); background: var(--color-accent-soft, #eef3ff); }
.event-search input { width: min(16rem, 30vw); min-height: 2.3rem; padding: .45rem .7rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: .55rem; background: var(--color-surface, #fff); color: var(--color-text, #17202a); font: inherit; font-size: .78rem; }
.event-search input:focus { border-color: var(--color-accent, #315fcc); outline: 2px solid color-mix(in srgb, var(--color-accent, #315fcc) 20%, transparent); }
.featured-event, .event-list { display: grid; gap: 1.2rem; }
.section-heading h2 { margin: .28rem 0 0; font-size: clamp(1.45rem, 3vw, 2.15rem); letter-spacing: -.035em; }
.featured-event :deep(.event-card) { min-height: 31rem; }
.featured-event :deep(.event-card h2) { font-size: clamp(2.2rem, 6vw, 4.7rem); }
.event-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 25rem), 1fr)); gap: 1rem; }
.events-empty { display: grid; min-height: 24rem; place-content: center; justify-items: center; text-align: center; }
.events-empty > span { color: var(--color-accent, #315fcc); font-size: 2.5rem; }
.events-empty h2 { margin: 1rem 0 0; font-size: 1.5rem; }
.events-empty p { margin: .5rem 0 0; color: var(--color-text-muted, #607080); }
@media (max-width: 52rem) { .events-hero__inner { grid-template-columns: 1fr; }.events-hero dl { grid-template-columns: repeat(3, 1fr); }.events-hero dl div { min-width: 0; } }
@media (max-width: 38rem) { .event-controls { align-items: stretch; flex-direction: column; }.event-search input { width: 100%; }.events-hero dl { grid-template-columns: 1fr; }.events-hero h1 { font-size: clamp(2.7rem, 16vw, 4.5rem); } }
</style>
