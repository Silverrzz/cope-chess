<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { api } from "@/api/client";
import AdminEmptyState from "@/components/admin/AdminEmptyState.vue";
import AdminPageHeader from "@/components/admin/AdminPageHeader.vue";
import InlineFeedback from "@/components/admin/InlineFeedback.vue";
import StatusBadge from "@/components/admin/StatusBadge.vue";
import { errorText, formatDate, humanize } from "@/components/admin/format";
import type { AdminEventListResponse, EventSummary } from "@/types/events";

const data = ref<AdminEventListResponse | null>(null);
const loading = ref(true);
const error = ref("");
const query = ref("");
const status = ref("");

const filtered = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase();
  return (data.value?.events ?? []).filter((item) => {
    if (status.value && item.record.status !== status.value) return false;
    if (!needle) return true;
    return [item.record.title, item.record.subtitle, item.record.slug, item.handler.label, item.handler.key]
      .some((value) => value.toLocaleLowerCase().includes(needle));
  });
});

const metrics = computed(() => {
  const events = data.value?.events ?? [];
  return [
    { label: "Total events", value: events.length },
    { label: "Live", value: events.filter((item) => ["live", "intermission"].includes(item.record.status)).length },
    { label: "Scheduled", value: events.filter((item) => ["announced", "scheduled", "postponed"].includes(item.record.status)).length },
    { label: "Ready modules", value: events.filter((item) => item.handler.current).length },
  ];
});

onMounted(load);

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    data.value = await api.get<AdminEventListResponse>("/api/admin/events");
  } catch (cause) {
    error.value = errorText(cause);
  } finally {
    loading.value = false;
  }
}

function scheduleLabel(item: EventSummary): string {
  if (item.record.status === "live") return "Live now";
  if (item.next_session?.scheduled_start_at) return `Next ${formatDate(item.next_session.scheduled_start_at)}`;
  if (item.record.scheduled_start_at) return `Starts ${formatDate(item.record.scheduled_start_at)}`;
  return "Schedule not set";
}
</script>

<template>
  <div class="admin-page events-admin page-stack">
    <AdminPageHeader title="Events" description="The launchpad for bespoke exhibitions. Event creation and format-specific controls are supplied by code modules.">
      <template #actions><RouterLink class="button button--secondary" to="/events">View public events</RouterLink></template>
    </AdminPageHeader>
    <InlineFeedback :message="error" />

    <div v-if="loading" class="panel loading-panel" role="status">Loading events…</div>
    <template v-else-if="data">
      <section class="event-metrics" aria-label="Event overview">
        <div v-for="metric in metrics" :key="metric.label"><strong>{{ metric.value }}</strong><span>{{ metric.label }}</span></div>
      </section>

      <section class="panel event-launchpad">
        <div class="launchpad-heading">
          <div><span>Control rooms</span><h2>Event launchpad</h2><p>{{ filtered.length }} of {{ data.events.length }} events</p></div>
          <div class="launchpad-filters">
            <label><span class="sr-only">Search events</span><input v-model="query" class="input" type="search" placeholder="Search events or modules"></label>
            <label><span class="sr-only">Filter by status</span><select v-model="status" class="input"><option value="">All statuses</option><option v-for="item in data.statuses" :key="item" :value="item">{{ humanize(item) }}</option></select></label>
          </div>
        </div>

        <div v-if="filtered.length" class="admin-event-grid">
          <RouterLink v-for="item in filtered" :key="item.record.id" class="admin-event-card" :to="`/admin/events/${item.record.id}`">
            <div class="admin-event-card__top"><StatusBadge :status="item.record.status" /><span v-if="item.record.featured">Featured</span></div>
            <div class="admin-event-card__copy"><small>{{ item.record.slug }}</small><h3>{{ item.record.title }}</h3><p>{{ item.record.summary || item.record.subtitle || "No event summary yet." }}</p></div>
            <div class="admin-event-card__schedule"><span>{{ item.next_session?.title || "Event schedule" }}</span><strong>{{ scheduleLabel(item) }}</strong></div>
            <dl><div><dt>Stages</dt><dd>{{ item.counts.stages }}</dd></div><div><dt>Sessions</dt><dd>{{ item.counts.sessions }}</dd></div><div><dt>Cast</dt><dd>{{ item.counts.cast }}</dd></div><div><dt>Contests</dt><dd>{{ item.counts.contests }}</dd></div></dl>
            <footer><span class="module-state" :class="{ 'module-state--ready': item.handler.current, 'module-state--warning': !item.handler.current }"><i></i>{{ item.handler.current ? item.handler.label : item.handler.available ? "Module version mismatch" : "Module unavailable" }}</span><span aria-hidden="true">→</span></footer>
          </RouterLink>
        </div>
        <AdminEmptyState v-else-if="data.events.length" title="No matching events" description="Adjust the search or status filter." />
        <AdminEmptyState v-else title="No events have been provisioned" description="Events are intentionally created by registered code modules. Install a module and run its provisioning entrypoint to place it here." />
      </section>

      <section class="module-registry panel">
        <div><span>Code registry</span><h2>Installed event modules</h2><p>Only version-compatible modules can open their bespoke control rooms.</p></div>
        <div v-if="data.registered_modules.length" class="module-list"><div v-for="module in data.registered_modules" :key="module.key"><span><strong>{{ module.label }}</strong><code>{{ module.key }}</code></span><StatusBadge status="active" :label="`v${module.version}`" /></div></div>
        <p v-else class="module-empty">The universal event system is ready. No bespoke event modules are registered in this build.</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.events-admin { display: grid; gap: 1rem; }.loading-panel { min-height: 16rem; padding: 2rem; color: var(--color-text-muted, #64748b); }
.event-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .7rem; }.event-metrics div { display: grid; gap: .2rem; padding: .9rem 1rem; border: 1px solid var(--color-border, #d9e0ea); border-radius: var(--radius-md, .6rem); background: var(--color-surface, #fff); }.event-metrics strong { font-size: 1.5rem; font-variant-numeric: tabular-nums; }.event-metrics span { color: var(--color-text-muted, #64748b); font-size: .68rem; font-weight: 680; }
.event-launchpad { overflow: hidden; padding: 0; }.launchpad-heading { display: flex; align-items: end; justify-content: space-between; gap: 1rem; padding: 1rem; border-bottom: 1px solid var(--color-border, #d9e0ea); }.launchpad-heading > div > span, .module-registry > div:first-child > span { color: var(--color-accent, #315fcc); font-size: .61rem; font-weight: 780; letter-spacing: .1em; text-transform: uppercase; }.launchpad-heading h2, .module-registry h2 { margin: .18rem 0 0; font-size: 1rem; }.launchpad-heading p, .module-registry p { margin: .2rem 0 0; color: var(--color-text-muted, #64748b); font-size: .7rem; }.launchpad-filters { display: flex; gap: .45rem; }.launchpad-filters input { width: min(17rem, 27vw); }.launchpad-filters select { min-width: 9rem; }
.admin-event-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 22rem), 1fr)); gap: 1px; background: var(--color-border, #d9e0ea); }.admin-event-card { display: grid; gap: 1rem; min-height: 21rem; padding: 1rem; background: var(--color-surface, #fff); color: var(--color-text, #172033); text-decoration: none; transition: background-color var(--transition-fast, 140ms), transform var(--transition-fast, 140ms); }.admin-event-card:hover { z-index: 1; background: color-mix(in srgb, var(--color-accent, #315fcc) 4%, var(--color-surface, #fff)); }.admin-event-card__top, .admin-event-card footer { display: flex; align-items: center; justify-content: space-between; gap: .7rem; }.admin-event-card__top > span { color: var(--color-accent, #315fcc); font-size: .62rem; font-weight: 750; text-transform: uppercase; }.admin-event-card__copy { align-self: center; }.admin-event-card__copy small { color: var(--color-text-muted, #64748b); font-size: .62rem; }.admin-event-card h3 { margin: .35rem 0 0; font-size: 1.3rem; letter-spacing: -.025em; }.admin-event-card__copy p { margin: .5rem 0 0; color: var(--color-text-muted, #64748b); font-size: .74rem; line-height: 1.55; }.admin-event-card__schedule { display: grid; gap: .2rem; padding: .65rem .7rem; border-left: 2px solid var(--color-accent, #315fcc); background: var(--color-surface-subtle, #f1f5f9); }.admin-event-card__schedule span { color: var(--color-text-muted, #64748b); font-size: .62rem; }.admin-event-card__schedule strong { font-size: .72rem; }.admin-event-card dl { display: grid; grid-template-columns: repeat(4, 1fr); gap: .5rem; margin: 0; }.admin-event-card dt { color: var(--color-text-muted, #64748b); font-size: .57rem; font-weight: 680; text-transform: uppercase; }.admin-event-card dd { margin: .15rem 0 0; font-size: .85rem; font-weight: 760; }.admin-event-card footer { margin-top: auto; padding-top: .7rem; border-top: 1px solid var(--color-border, #d9e0ea); }.module-state { display: inline-flex; align-items: center; gap: .4rem; color: var(--color-text-muted, #64748b); font-size: .65rem; font-weight: 680; }.module-state i { width: .42rem; height: .42rem; border-radius: 50%; background: currentColor; }.module-state--ready { color: var(--color-success, #15803d); }.module-state--warning { color: var(--color-warning, #9a6700); }
.module-registry { display: grid; grid-template-columns: minmax(15rem, .7fr) minmax(0, 1.3fr); align-items: start; gap: 2rem; padding: 1rem; }.module-list { display: grid; border: 1px solid var(--color-border, #d9e0ea); border-radius: .55rem; }.module-list > div { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: .65rem .75rem; border-bottom: 1px solid var(--color-border, #d9e0ea); }.module-list > div:last-child { border-bottom: 0; }.module-list span { display: grid; gap: .15rem; }.module-list strong { font-size: .76rem; }.module-list code { color: var(--color-text-muted, #64748b); font-size: .64rem; }.module-empty { padding: 1rem; border: 1px dashed var(--color-border-strong, #c7d0dc); border-radius: .55rem; background: var(--color-surface-subtle, #f1f5f9); }
@media (max-width: 50rem) { .event-metrics { grid-template-columns: repeat(2, 1fr); }.launchpad-heading { align-items: stretch; flex-direction: column; }.launchpad-filters input { width: 100%; }.module-registry { grid-template-columns: 1fr; } }
@media (max-width: 34rem) { .launchpad-filters { flex-direction: column; }.launchpad-filters select { width: 100%; } }
@media (prefers-reduced-motion: reduce) { .admin-event-card { transition: none; } }
</style>
