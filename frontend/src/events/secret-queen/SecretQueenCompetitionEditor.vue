<script setup lang="ts">
import { computed, ref, toRaw, watch } from "vue";

import { api } from "@/api/client";
import EngineVersionPicker from "@/components/admin/EngineVersionPicker.vue";
import InlineFeedback from "@/components/admin/InlineFeedback.vue";
import StatusBadge from "@/components/admin/StatusBadge.vue";
import { errorText } from "@/components/admin/format";
import { useConfirm } from "@/composables/useConfirm";
import { useToast } from "@/composables/useToast";
import type { EventDetailResponse } from "@/types/events";
import type { SecretQueenAdmin, SecretQueenSettings, SwissGroup } from "./types";

const props = defineProps<{ detail: EventDetailResponse }>();
const emit = defineEmits<{ changed: [] }>();
const toast = useToast();
const { confirm } = useConfirm();
const payload = computed(() => props.detail.custom as SecretQueenAdmin);
const settings = ref<SecretQueenSettings | null>(null);
const busy = ref(false);
const editError = ref("");
const editable = computed(() => ["draft", "announced", "scheduled", "postponed"].includes(props.detail.event.status));
const qualifiers = computed(() => settings.value?.groups.reduce((total, group) => total + group.qualifiers, 0) ?? 0);
const dirty = computed(() => JSON.stringify(settings.value) !== JSON.stringify(payload.value.settings));
const canStart = computed(() => Boolean(props.detail.event.published_at) && !dirty.value && [2, 4, 8].includes(qualifiers.value)
  && settings.value?.groups.every(group => group.engine_ids.length >= 2 && group.engine_ids.length >= group.qualifiers));
const engines = computed(() => payload.value.engines ?? []);
const available = computed(() => {
  const used = new Set(settings.value?.groups.flatMap(group => group.engine_ids) ?? []);
  const families = new Set(engines.value.filter(engine => used.has(engine.id)).map(engine => engine.engine_id));
  return engines.value.filter(engine => engine.active && !families.has(engine.engine_id));
});

watch(() => payload.value.settings, value => { settings.value = value ? structuredClone(toRaw(value)) : null; }, { immediate: true });

function addGroup(): void {
  if (!settings.value) return;
  let number = settings.value.groups.length + 1;
  while (settings.value.groups.some(group => group.name === `Group ${number}`)) number += 1;
  settings.value.groups.push({ name: `Group ${number}`, engine_ids: [], rounds: 3, qualifiers: 2 });
}

function selectEngine(group: SwissGroup, value: string | number): void {
  const id = Number(value);
  if (available.value.some(engine => engine.id === id)) group.engine_ids.push(id);
}

function name(id: number): string {
  const engine = engines.value.find(item => item.id === id);
  return engine ? `${engine.name} ${engine.version}`.trim() : `Engine #${id}`;
}

async function submit(action: "save" | "start"): Promise<void> {
  if (busy.value || !settings.value) return;
  if (action === "start") {
    const accepted = await confirm({ title: "Start Swiss groups?", message: "The first round will enter the worker queue and the roster will lock.", confirmLabel: "Start groups" });
    if (!accepted) return;
  }
  busy.value = true;
  editError.value = "";
  const endpoint = `/api/admin/events/${props.detail.event.id}/secret-queen`;
  try {
    const response = action === "start"
      ? await api.post<{ message: string }>(`${endpoint}/start`)
      : await api.put<{ message: string }>(`${endpoint}/competition`, { body: settings.value });
    toast.success(response.message);
    emit("changed");
  } catch (cause) {
    editError.value = errorText(cause);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="panel competition-panel" aria-labelledby="secret-queen-competition">
    <header><h2 id="secret-queen-competition">Competition</h2></header>
    <InlineFeedback :message="editError" />
    <form v-if="editable && settings" @submit.prevent="submit('save')">
      <div class="competition-fields">
        <label class="field"><span>Group base time (ms)</span><input v-model.number="settings.group_clock.initial_ms" type="number" min="1000" class="input" required></label>
        <label class="field"><span>Group increment (ms)</span><input v-model.number="settings.group_clock.increment_ms" type="number" min="0" class="input" required></label>
        <label class="field"><span>Knockout base time (ms)</span><input v-model.number="settings.knockout_clock.initial_ms" type="number" min="1000" class="input" required></label>
        <label class="field"><span>Knockout increment (ms)</span><input v-model.number="settings.knockout_clock.increment_ms" type="number" min="0" class="input" required></label>
        <label class="field"><span>Hash per UCI process (MB)</span><input v-model.number="settings.hash_mb" type="number" min="1" max="1024" class="input" required></label>
        <label class="field"><span>Concurrent games per group</span><input v-model.number="settings.group_concurrency" type="number" min="1" max="128" class="input" required></label>
      </div>
      <p class="competition-note">A published event with valid groups starts automatically at its scheduled time.</p>
      <div class="competition-toolbar"><h3>Swiss groups</h3><span>{{ qualifiers }} qualifiers · choose 2, 4 or 8</span></div>
      <section v-for="(group, index) in settings.groups" :key="index" class="competition-group">
        <div class="competition-fields">
          <label class="field"><span>Group name</span><input v-model="group.name" class="input" maxlength="80" required></label>
          <label class="field"><span>Swiss rounds</span><input v-model.number="group.rounds" class="input" type="number" min="1" max="100" required></label>
          <label class="field"><span>Qualify for bracket</span><input v-model.number="group.qualifiers" class="input" type="number" min="0" max="8" required></label>
        </div>
        <ul v-if="group.engine_ids.length" class="competition-roster">
          <li v-for="(id, seed) in group.engine_ids" :key="id">
            <span>{{ seed + 1 }}. {{ name(id) }}</span>
            <button class="button button--ghost button--small" type="button" @click="group.engine_ids.splice(seed, 1)">Remove</button>
          </li>
        </ul>
        <div class="competition-toolbar">
          <EngineVersionPicker :model-value="''" :engines="available" label="Add engine" placeholder="Choose an engine version" @update:model-value="selectEngine(group, $event)" />
          <button class="button button--secondary button--small" type="button" @click="settings.groups.splice(index, 1)">Remove group</button>
        </div>
      </section>
      <div class="competition-actions">
        <button class="button button--secondary" type="button" :disabled="busy" @click="addGroup">Add group</button>
        <button class="button button--primary" type="submit" :disabled="busy || !dirty">Save competition</button>
        <button class="button button--secondary" type="button" :disabled="busy || !canStart" @click="submit('start')">Start groups</button>
      </div>
    </form>
    <ul v-else-if="payload.contests?.length" class="competition-roster">
      <li v-for="contest in payload.contests" :key="contest.id">
        <span>{{ contest.title }}</span><StatusBadge :status="contest.status" />
        <RouterLink v-if="contest.tournament_id" class="button button--ghost button--small" :to="`/admin/tournaments/${contest.tournament_id}`">Manage games</RouterLink>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.competition-panel { display: grid; gap: 1rem; padding: clamp(1rem, 2vw, 1.5rem); }
.competition-panel h2 { font-size: 1.15rem; }
.competition-panel form, .competition-group { display: grid; gap: 1rem; }
.competition-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 12rem), 1fr)); gap: .85rem; }
.competition-group { padding: 1rem; border: 1px solid var(--color-border); border-radius: var(--radius-md); }
.competition-toolbar, .competition-actions { display: flex; flex-wrap: wrap; align-items: center; gap: .8rem; }
.competition-toolbar { justify-content: space-between; }
.competition-toolbar h3 { font-size: 1rem; }
.competition-note { margin: 0; color: var(--color-text-muted); }
.competition-roster { display: grid; gap: .35rem; list-style: none; }
.competition-roster li { display: flex; align-items: center; gap: .6rem; }
.competition-roster li > span:first-child { margin-right: auto; }
</style>
