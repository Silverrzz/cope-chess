<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import { api } from "@/api/client";
import ContentState from "@/components/public/ContentState.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import GenericArchiveHero from "@/events/GenericArchiveHero.vue";
import { archiveEventComponent } from "@/events/registry";
import type { EventSummary, PublicEventListResponse } from "@/types/events";

type FilterKey = "all" | "engine-relay" | "engine-relay-finale" | "puzzle-gauntlet";

const response = ref<PublicEventListResponse | null>(null);
const loading = ref(true);
const loadError = ref("");
const query = ref("");
const filter = ref<FilterKey>("all");
const nowMs = ref(Date.now());
const clockOffsetMs = ref(0);
let timer: number | undefined;

const filters: Array<{ key: FilterKey; label: string }> = [
  { key: "all", label: "All" },
  { key: "engine-relay", label: "Engine relay" },
  { key: "engine-relay-finale", label: "Finales" },
  { key: "puzzle-gauntlet", label: "Puzzle gauntlet" },
];
const pastEvents = computed(() => (response.value?.events ?? []).filter((event) => ["completed", "cancelled"].includes(event.record.status)));
const visibleEvents = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase();
  return pastEvents.value.filter((event) => {
    if (filter.value !== "all" && event.record.handler_key !== filter.value) return false;
    if (!needle) return true;
    return [event.record.title, event.record.subtitle, event.record.summary, event.handler.label]
      .some((value) => value.toLocaleLowerCase().includes(needle));
  });
});

onMounted(() => {
  void load();
  timer = window.setInterval(() => { nowMs.value = Date.now() + clockOffsetMs.value; }, 1000);
});

onBeforeUnmount(() => {
  if (timer !== undefined) window.clearInterval(timer);
});

async function load(): Promise<void> {
  loading.value = true;
  loadError.value = "";
  try {
    response.value = await api.get<PublicEventListResponse>("/api/events");
    const serverTime = Date.parse(response.value.server_time);
    clockOffsetMs.value = Number.isFinite(serverTime) ? serverTime - Date.now() : 0;
    nowMs.value = Date.now() + clockOffsetMs.value;
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : "The event archive could not be loaded.";
  } finally {
    loading.value = false;
  }
}

function eventComponent(event: EventSummary) {
  return archiveEventComponent(event.handler.key) ?? GenericArchiveHero;
}
</script>

<template>
  <div class="events-page">
    <div v-if="loading" class="page-container state-wrap"><ContentState kind="loading" title="Opening the event archive" /></div>
    <div v-else-if="loadError" class="page-container state-wrap"><ContentState kind="error" :message="loadError" action-label="Try again" @action="load" /></div>
    <template v-else-if="response">
      <header class="events-intro page-container">
        <div><span>COPE broadcasts</span><h1>Events</h1></div>
        <p>One-off formats, engine spectacles, and every move preserved after the broadcast ends.</p>
      </header>

      <section class="upcoming-section" aria-labelledby="upcoming-events-title">
        <div class="section-heading page-container"><span>On the horizon</span><h2 id="upcoming-events-title">Upcoming event</h2></div>
        <component v-if="response.current" :is="eventComponent(response.current)" :summary="response.current" :current="true" :now-ms="nowMs" />
        <div v-else class="no-upcoming page-container"><AppIcon name="clock" :size="22" /><div><strong>No event is scheduled yet</strong><span>The next COPE special will appear here as soon as it is announced.</span></div></div>
      </section>

      <section class="archive-section" aria-labelledby="event-archive-title">
        <div class="archive-header page-container">
          <div class="section-heading"><span>Past broadcasts</span><h2 id="event-archive-title">Event archive</h2></div>
          <div class="archive-tools">
            <label class="archive-search"><AppIcon name="search" :size="17" /><span class="sr-only">Search past events</span><input v-model="query" type="search" placeholder="Search events" /></label>
            <div class="archive-filters" aria-label="Filter events">
              <button v-for="item in filters" :key="item.key" type="button" :class="{ active: filter === item.key }" :aria-pressed="filter === item.key" @click="filter = item.key">{{ item.label }}</button>
            </div>
          </div>
        </div>

        <div v-if="visibleEvents.length" class="archive-list">
          <component v-for="event in visibleEvents" :is="eventComponent(event)" :key="event.record.id" :summary="event" :now-ms="nowMs" />
        </div>
        <div v-else class="archive-empty page-container"><AppIcon name="archive" :size="22" /><div><strong>{{ pastEvents.length ? "No events match these filters" : "The archive is empty" }}</strong><span>{{ pastEvents.length ? "Try another event type or search term." : "Finished events will be preserved here." }}</span></div></div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.events-page { margin-block: calc(0px - clamp(var(--space-6), 4vw, var(--space-12))) -3rem; }
.state-wrap { padding-block: 3rem; }
.events-intro { display: flex; align-items: end; justify-content: space-between; gap: 2rem; padding-block: clamp(3.5rem, 8vw, 7rem) clamp(2.5rem, 6vw, 5rem); }
.events-intro > div { display: grid; gap: .25rem; }
.events-intro span, .section-heading > span { color: var(--color-accent); font-size: .67rem; font-weight: 820; letter-spacing: .15em; text-transform: uppercase; }
.events-intro h1 { margin: 0; font-size: clamp(3.8rem, 9vw, 8rem); letter-spacing: -.075em; line-height: .86; }
.events-intro p { max-width: 34rem; margin: 0 0 .6rem; color: var(--color-text-muted); font-size: clamp(.88rem, 1.6vw, 1.05rem); line-height: 1.65; }
.upcoming-section, .archive-section { display: grid; }
.section-heading { display: grid; gap: .25rem; }
.section-heading h2 { margin: 0; font-size: clamp(1.6rem, 3vw, 2.25rem); letter-spacing: -.035em; }
.upcoming-section > .section-heading { padding-block: 1.15rem; }
.no-upcoming, .archive-empty { display: flex; align-items: center; gap: .8rem; min-height: 6rem; margin-bottom: 2rem; border: 1px dashed var(--color-border-strong); border-radius: var(--radius-lg); color: var(--color-text-muted); }
.no-upcoming > div, .archive-empty > div { display: grid; gap: .2rem; }
.no-upcoming strong, .archive-empty strong { color: var(--color-text); font-size: .82rem; }
.no-upcoming span, .archive-empty span { font-size: .7rem; }
.archive-section { padding-top: clamp(3rem, 7vw, 6rem); }
.archive-header { display: grid; gap: 1.25rem; padding-bottom: 1.4rem; }
.archive-tools { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.archive-search { display: flex; width: min(22rem, 100%); min-height: 2.35rem; align-items: center; gap: .45rem; border: 1px solid var(--color-border-strong); border-radius: var(--radius-round); background: var(--color-surface-raised); padding: 0 .75rem; color: var(--color-text-muted); }
.archive-search:focus-within { border-color: var(--color-focus); box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-focus) 18%, transparent); }
.archive-search input { width: 100%; min-width: 0; border: 0; outline: 0; background: transparent; color: var(--color-text); font: inherit; font-size: .76rem; }
.archive-filters { display: flex; flex-wrap: wrap; justify-content: end; gap: .35rem; }
.archive-filters button { min-height: 2.2rem; border: 1px solid var(--color-border); border-radius: var(--radius-round); background: var(--color-surface); padding: 0 .75rem; color: var(--color-text-muted); cursor: pointer; font: inherit; font-size: .65rem; font-weight: 720; }
.archive-filters button:hover { border-color: var(--color-border-strong); color: var(--color-text); }
.archive-filters button.active { border-color: var(--color-accent); background: var(--color-accent-soft); color: var(--color-accent); }
.archive-list { display: grid; }
.archive-empty { margin-block: 1rem 5rem; }
@media (max-width: 44rem) { .events-intro { align-items: start; flex-direction: column; }.archive-tools { align-items: stretch; flex-direction: column; }.archive-filters { justify-content: start; }.archive-filters button { flex: 1 1 auto; }.archive-search { width: 100%; } }
</style>
