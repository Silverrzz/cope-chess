<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { api } from "@/api/client";
import AdminPageHeader from "@/components/admin/AdminPageHeader.vue";
import InlineFeedback from "@/components/admin/InlineFeedback.vue";
import StatusBadge from "@/components/admin/StatusBadge.vue";
import { errorText, formatDate, humanize } from "@/components/admin/format";
import { useConfirm } from "@/composables/useConfirm";
import { useToast } from "@/composables/useToast";
import { adminEventComponent } from "@/events/registry";
import type { EventDetailResponse } from "@/types/events";

const props = defineProps<{ id: string }>();
const router = useRouter();
const toast = useToast();
const { confirm } = useConfirm();
const data = ref<EventDetailResponse | null>(null);
const loading = ref(true);
const deleting = ref(false);
const error = ref("");
const customComponent = computed(() => data.value ? adminEventComponent(data.value.handler.key) : null);

onMounted(load);

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    data.value = await api.get<EventDetailResponse>(`/api/admin/events/${encodeURIComponent(props.id)}`);
  } catch (cause) {
    error.value = errorText(cause);
  } finally {
    loading.value = false;
  }
}

async function remove(): Promise<void> {
  if (!data.value || deleting.value) return;
  const accepted = await confirm({
    title: "Delete event?",
    message: `Delete “${data.value.event.title}” and all of its event content? Unstarted relay tournaments will also be deleted. Completed tournament history is retained. This cannot be undone.`,
    confirmLabel: "Delete event",
    tone: "danger",
  });
  if (!accepted) return;
  deleting.value = true;
  error.value = "";
  try {
    const response = await api.delete<{ message: string }>(`/api/admin/events/${encodeURIComponent(props.id)}`);
    toast.success(response.message);
    await router.push("/admin/events");
  } catch (cause) {
    toast.error(cause);
    error.value = errorText(cause);
  } finally {
    deleting.value = false;
  }
}
</script>

<template>
  <div class="admin-page control-room page-stack">
    <InlineFeedback :message="error" />
    <div v-if="loading" class="panel loading-panel" role="status">Opening control room…</div>
    <template v-else-if="data">
      <AdminPageHeader :title="data.event.title" :description="data.event.subtitle || 'Bespoke event control room'">
        <template #actions><StatusBadge :status="data.event.status" /><RouterLink v-if="data.event.published_at" class="button button--secondary" :to="`/events/${data.event.slug}`">View public event</RouterLink><RouterLink class="button button--ghost" to="/admin/events">Back to events</RouterLink><button class="button button--danger" type="button" :disabled="deleting" @click="remove">{{ deleting ? "Deleting…" : "Delete event" }}</button></template>
      </AdminPageHeader>

      <section class="control-strip panel">
        <div><span>Event</span><strong>#{{ data.event.id }} · {{ data.event.slug }}</strong></div>
        <div><span>Starts</span><strong>{{ formatDate(data.event.scheduled_start_at) }}</strong></div>
        <div><span>Revision</span><strong>{{ data.event.revision }}</strong></div>
        <div><span>Visibility</span><strong>{{ data.event.published_at ? "Published" : "Private" }}</strong></div>
      </section>

      <component :is="customComponent" v-if="customComponent && data.handler.current" :detail="data" @changed="load" />

      <section v-else class="panel module-gate">
        <div class="module-gate__signal" :class="{ 'module-gate__signal--warning': !data.handler.current }"><span></span></div>
        <div class="module-gate__copy">
          <span>Control room adapter</span>
          <h2>{{ data.handler.available ? "The event module needs attention" : "This event’s control room is not installed" }}</h2>
          <p v-if="!data.handler.available">The universal event record is healthy, but its bespoke admin interface belongs to the <code>{{ data.handler.key }}</code> code module. Register that module to activate operational controls.</p>
          <p v-else-if="!data.handler.current">This event requires module version {{ data.handler.required_version }}, while version {{ data.handler.installed_version }} is installed. Align the versions before operating it.</p>
          <p v-else>The backend module is ready, but this build does not provide its admin component.</p>
        </div>
        <dl>
          <div><dt>Handler</dt><dd><code>{{ data.handler.key }}</code></dd></div>
          <div><dt>Required</dt><dd>v{{ data.handler.required_version }}</dd></div>
          <div><dt>Installed</dt><dd>{{ data.handler.installed_version ? `v${data.handler.installed_version}` : "Not installed" }}</dd></div>
          <div><dt>State</dt><dd><StatusBadge :status="data.handler.current ? 'active' : 'deferred'" :label="data.handler.current ? 'Compatible' : 'Unavailable'" /></dd></div>
        </dl>
      </section>

      <div class="resource-grid">
        <section class="panel resource-card"><header><span>Structure</span><h2>Event resources</h2></header><dl><div v-for="(value, key) in data.counts" :key="key"><dt>{{ humanize(key) }}</dt><dd>{{ value }}</dd></div></dl></section>
        <section class="panel resource-card"><header><span>Universal state</span><h2>Lifecycle</h2></header><dl><div><dt>Status</dt><dd><StatusBadge :status="data.event.status" /></dd></div><div><dt>Published</dt><dd>{{ formatDate(data.event.published_at) }}</dd></div><div><dt>Started</dt><dd>{{ formatDate(data.event.started_at) }}</dd></div><div><dt>Finished</dt><dd>{{ formatDate(data.event.finished_at) }}</dd></div></dl></section>
        <section class="panel resource-card resource-card--wide"><header><span>Shared data</span><h2>Provisioned content</h2></header><div class="content-overview"><div><strong>{{ data.stages.length }}</strong><span>Stages</span></div><div><strong>{{ data.sessions.length }}</strong><span>Sessions</span></div><div><strong>{{ data.cast.length }}</strong><span>Cast members</span></div><div><strong>{{ data.contests.length }}</strong><span>Contests</span></div><div><strong>{{ data.updates.length }}</strong><span>Updates</span></div><div><strong>{{ data.awards.length }}</strong><span>Awards</span></div></div></section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.control-room { display: grid; gap: 1rem; }.loading-panel { min-height: 16rem; padding: 2rem; color: var(--color-text-muted, #64748b); }.control-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); overflow: hidden; padding: 0; }.control-strip > div { display: grid; gap: .2rem; padding: .75rem .9rem; border-right: 1px solid var(--color-border, #d9e0ea); }.control-strip > div:last-child { border-right: 0; }.control-strip span { color: var(--color-text-muted, #64748b); font-size: .58rem; font-weight: 720; letter-spacing: .06em; text-transform: uppercase; }.control-strip strong { overflow: hidden; font-size: .72rem; text-overflow: ellipsis; white-space: nowrap; }
.module-gate { display: grid; grid-template-columns: auto minmax(0, 1fr) minmax(15rem, .65fr); align-items: center; gap: 1.25rem; padding: 1.2rem; }.module-gate__signal { display: grid; width: 3.2rem; height: 3.2rem; place-items: center; border-radius: 1rem; background: color-mix(in srgb, var(--color-success, #15803d) 10%, transparent); color: var(--color-success, #15803d); }.module-gate__signal--warning { background: color-mix(in srgb, var(--color-warning, #9a6700) 10%, transparent); color: var(--color-warning, #9a6700); }.module-gate__signal span { width: .75rem; height: .75rem; border-radius: 50%; background: currentColor; box-shadow: 0 0 0 .45rem color-mix(in srgb, currentColor 15%, transparent); }.module-gate__copy > span, .resource-card header > span { color: var(--color-accent, #315fcc); font-size: .6rem; font-weight: 780; letter-spacing: .1em; text-transform: uppercase; }.module-gate h2 { margin: .2rem 0 0; font-size: 1.1rem; }.module-gate p { max-width: 65ch; margin: .45rem 0 0; color: var(--color-text-muted, #64748b); font-size: .76rem; line-height: 1.55; }.module-gate code { font-size: .7rem; }.module-gate > dl { display: grid; grid-template-columns: 1fr 1fr; gap: .65rem; margin: 0; padding: .8rem; border: 1px solid var(--color-border, #d9e0ea); border-radius: .55rem; background: var(--color-surface-subtle, #f1f5f9); }.module-gate dt, .resource-card dt { color: var(--color-text-muted, #64748b); font-size: .58rem; font-weight: 680; text-transform: uppercase; }.module-gate dd, .resource-card dd { margin: .18rem 0 0; font-size: .71rem; font-weight: 680; }
.resource-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }.resource-card { overflow: hidden; padding: 0; }.resource-card header { padding: .8rem .9rem; border-bottom: 1px solid var(--color-border, #d9e0ea); }.resource-card h2 { margin: .18rem 0 0; font-size: .9rem; }.resource-card dl { display: grid; grid-template-columns: repeat(2, 1fr); gap: 1px; margin: 0; background: var(--color-border, #d9e0ea); }.resource-card dl div { padding: .75rem .9rem; background: var(--color-surface, #fff); }.resource-card--wide { grid-column: 1 / -1; }.content-overview { display: grid; grid-template-columns: repeat(6, 1fr); gap: 1px; background: var(--color-border, #d9e0ea); }.content-overview div { display: grid; gap: .18rem; padding: 1rem; background: var(--color-surface, #fff); }.content-overview strong { font-size: 1.3rem; }.content-overview span { color: var(--color-text-muted, #64748b); font-size: .64rem; }
@media (max-width: 60rem) { .module-gate { grid-template-columns: auto 1fr; }.module-gate > dl { grid-column: 1 / -1; }.content-overview { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 42rem) { .control-strip { grid-template-columns: repeat(2, 1fr); }.control-strip > div:nth-child(2) { border-right: 0; }.control-strip > div:nth-child(-n+2) { border-bottom: 1px solid var(--color-border, #d9e0ea); }.module-gate { grid-template-columns: 1fr; }.module-gate > dl { grid-column: auto; }.resource-grid { grid-template-columns: 1fr; }.resource-card--wide { grid-column: auto; } }
</style>
