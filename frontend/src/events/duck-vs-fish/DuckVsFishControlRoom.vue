<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { api } from "@/api/client";
import { timeControlLabel } from "@/components/public/format";
import type { EventDetailResponse } from "@/types/events";
import type { PondPayload } from "./types";

const props = defineProps<{ detail: EventDetailResponse }>();
const emit = defineEmits<{ changed: [] }>();
const payload = computed(() => props.detail.custom as PondPayload);
const busy = ref(false);
const error = ref("");
const start = ref("");
const minutes = ref(3);
const increment = ref(1.8);
const controlLabel = computed(() => timeControlLabel(payload.value.tournament?.config?.time_control));
watch(() => payload.value.tournament?.config?.time_control, control => {
  if (control?.category === "increment") {
    minutes.value = control.initial_ms / 60_000;
    increment.value = control.increment_ms / 1000;
  }
}, { immediate: true });

async function saveTimeControl(): Promise<void> {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await api.put(`/api/admin/events/${props.detail.event.id}/duck-vs-fish/time-control`, { body: { initial_ms: Math.round(minutes.value * 60_000), increment_ms: Math.round(increment.value * 1000) } });
    emit("changed");
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "The time control could not be updated."; }
  finally { busy.value = false; }
}

async function save(action: "visibility" | "schedule" | "unschedule"): Promise<void> {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    if (action === "visibility") await api.put(`/api/admin/events/${props.detail.event.id}/duck-vs-fish/visibility`, { body: { published: !props.detail.event.published_at } });
    else if (payload.value.tournament) {
      const path = `/api/admin/tournaments/${payload.value.tournament.id}/schedule`;
      if (action === "unschedule") await api.delete(path);
      else await api.post(path, { body: { scheduled_start_at: new Date(start.value).toISOString() } });
    }
    emit("changed");
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "The event could not be updated."; }
  finally { busy.value = false; }
}
</script>

<template>
  <section class="panel pond-controls">
    <header><div><h2>Duck vs Fish</h2><p>Duck Chess · {{ payload.odds_steps?.length ? 'Odds ladder · first draw or loss ends the event' : '20 games' }} · {{ payload.tournament?.config?.engine_threads ?? 64 }} threads per engine · {{ controlLabel }} · {{ payload.opening_book }}</p></div><button class="button button--secondary" :disabled="busy" @click="save('visibility')">{{ detail.event.published_at ? 'Unpublish event' : 'Publish event' }}</button></header>
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="payload.tournament">
      <p>Match #{{ payload.tournament.id }} · {{ payload.tournament.status }} · {{ detail.event.scheduled_start_at ? new Date(detail.event.scheduled_start_at).toLocaleString() : 'Start date TBD' }}</p>
      <form v-if="payload.tournament.status === 'draft'" @submit.prevent="saveTimeControl"><label>Initial time (minutes)<input v-model.number="minutes" type="number" min="0.1" step="0.1" required :disabled="busy" /></label><label>Increment (seconds)<input v-model.number="increment" type="number" min="0" step="0.1" required :disabled="busy" /></label><button class="button button--secondary" :disabled="busy" type="submit">Save time control</button></form>
      <form v-if="['draft', 'scheduled'].includes(payload.tournament.status)" @submit.prevent="save('schedule')"><label>Start date (your local time)<input v-model="start" type="datetime-local" required :disabled="busy" /></label><button class="button button--primary" :disabled="busy || !start" type="submit">Schedule match</button><button v-if="payload.tournament.status === 'scheduled'" class="button button--secondary" type="button" :disabled="busy" @click="save('unschedule')">Return to TBD</button></form>
      <RouterLink class="button button--secondary" :to="`/admin/tournaments/${payload.tournament.id}`">Manage match ↗</RouterLink>
    </template>
  </section>
</template>

<style scoped>
.pond-controls { display: grid; gap: 1rem; padding: 1.25rem; }.pond-controls header { display: flex; justify-content: space-between; align-items: center; gap: 1rem; }.pond-controls h2 { margin: 0; font-size: 1.2rem; }.pond-controls p { margin: .5rem 0; font-size: .8rem; color: var(--color-text-muted); }.pond-controls [role="alert"] { color: var(--color-danger, #b91c1c); }.pond-controls form { display: flex; align-items: end; flex-wrap: wrap; gap: .75rem; }.pond-controls label { display: grid; gap: .4rem; font-size: .75rem; }.pond-controls input { padding: .6rem; border: 1px solid var(--color-border); border-radius: .35rem; background: var(--color-surface); color: var(--color-text); }.pond-controls > a { justify-self: start; }
@media (max-width: 44rem) { .pond-controls header { flex-direction: column; align-items: start; } }
</style>
