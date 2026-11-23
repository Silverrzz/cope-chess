<script setup lang="ts">
import { computed, ref, watch } from "vue";

import { api } from "@/api/client";
import InlineFeedback from "@/components/admin/InlineFeedback.vue";
import { errorText } from "@/components/admin/format";
import { useToast } from "@/composables/useToast";
import type { EventDetailResponse } from "@/types/events";
import { timestampDate } from "@/utils/time";

const props = defineProps<{ detail: EventDetailResponse }>();
const emit = defineEmits<{ changed: [] }>();
const toast = useToast();
const localStart = ref("");
const published = ref(false);
const saving = ref(false);
const error = ref("");
const editable = computed(() => ["draft", "announced", "scheduled"].includes(props.detail.event.status));

watch(() => props.detail.event, event => {
  const start = event.scheduled_start_at ? timestampDate(event.scheduled_start_at) : null;
  const offset = start ? start.getTimezoneOffset() * 60_000 : 0;
  localStart.value = start ? new Date(start.getTime() - offset).toISOString().slice(0, 16) : "";
  published.value = Boolean(event.published_at);
}, { immediate: true });

async function save(): Promise<void> {
  if (saving.value || !editable.value) return;
  saving.value = true;
  error.value = "";
  try {
    const response = await api.put<{ message: string }>(`/api/admin/events/${props.detail.event.id}/secret-queen/announcement`, {
      body: {
        scheduled_start_at: localStart.value ? new Date(localStart.value).toISOString() : null,
        published: published.value,
      },
    });
    toast.success(response.message);
    emit("changed");
  } catch (cause) {
    error.value = errorText(cause);
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <section class="panel announcement-settings" aria-labelledby="secret-queen-announcement">
    <h2 id="secret-queen-announcement">Announcement</h2>
    <InlineFeedback :message="error" />
    <form v-if="editable" @submit.prevent="save">
      <label class="field">
        <span>Start date (your local time)</span>
        <input v-model="localStart" class="input" type="datetime-local" :disabled="saving">
      </label>
      <label class="announcement-settings__visibility">
        <input v-model="published" type="checkbox" :disabled="saving">
        <span>Publish on the Events page</span>
      </label>
      <button class="button button--primary" type="submit" :disabled="saving">{{ saving ? "Saving…" : "Save announcement" }}</button>
    </form>
    <p v-else>The announcement is locked after the event starts.</p>
  </section>
</template>

<style scoped>
.announcement-settings { display: grid; justify-items: start; gap: 1rem; padding: clamp(1rem, 2vw, 1.5rem); }
.announcement-settings h2 { margin: 0; font-size: 1.15rem; }
.announcement-settings form { display: flex; flex-wrap: wrap; align-items: end; gap: 1rem; }
.announcement-settings__visibility { display: flex; align-items: center; gap: .5rem; min-height: 2.25rem; }
</style>
