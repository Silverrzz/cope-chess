<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";

import { api } from "@/api/client";
import EngineVersionPicker from "@/components/admin/EngineVersionPicker.vue";
import ChessBoard from "@/components/chess/ChessBoard.vue";
import { errorText, formatDate } from "@/components/admin/format";
import StatusBadge from "@/components/admin/StatusBadge.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import { useConfirm } from "@/composables/useConfirm";
import { useToast } from "@/composables/useToast";
import type { EventDetailResponse } from "@/types/events";

import PuzzleBatchModal from "./PuzzleBatchModal.vue";
import type { GauntletPayload, GauntletPuzzle } from "./types";

const props = defineProps<{ detail: EventDetailResponse }>();
const emit = defineEmits<{ changed: [] }>();
const toast = useToast();
const { confirm } = useConfirm();
const payload = computed(() => props.detail.custom as GauntletPayload);
const tab = ref<"puzzles" | "field" | "run">("puzzles");
const pending = ref("");
const error = ref("");
const bulkText = ref("");
const bulkError = ref("");
const bulkOpen = ref(false);
const selectedPuzzleIds = ref<number[]>([]);
const selectedEngine = ref(0);
const editingId = ref<number | null>(null);
const quick = reactive({ fen: "", solutions: "", title: "" });
const edit = reactive({ fen: "", solutions: "", title: "" });
const settings = reactive({
  startSeconds: 30,
  decrementSeconds: 2,
  minimumSeconds: 5,
  threads: 1,
  hashMb: 256,
  scheduledStart: "",
});
const visibility = reactive({ published: false });

const locked = computed(() => !!payload.value.tournament);
const availableEngines = computed(() => {
  const entered = new Set(payload.value.entries.map((entry) => Number(entry.engine_id)));
  return (payload.value.engine_options ?? []).filter((engine) => !entered.has(engine.id));
});
const readiness = computed(() => [
  { label: "Puzzle set", ready: payload.value.puzzles.length > 0, value: `${payload.value.puzzles.length} loaded` },
  { label: "Engine field", ready: payload.value.entries.length >= 2, value: `${payload.value.entries.length} entered` },
  { label: "Visibility", ready: visibility.published, value: visibility.published ? "Public" : "Private" },
]);
const previewFen = computed(() => editingId.value ? edit.fen : quick.fen || payload.value.puzzles.at(-1)?.fen || "startpos");
const timeRange = computed(() => payload.value.puzzles.map((puzzle) => puzzle.time_limit_ms));
const allPuzzlesSelected = computed(() => payload.value.puzzles.length > 0 && selectedPuzzleIds.value.length === payload.value.puzzles.length);

watch(
  () => props.detail.event.revision,
  syncState,
  { immediate: true },
);

function syncState(): void {
  const value = payload.value.settings;
  settings.startSeconds = value.start_time_ms / 1000;
  settings.decrementSeconds = value.decrement_ms / 1000;
  settings.minimumSeconds = value.minimum_time_ms / 1000;
  settings.threads = value.threads;
  settings.hashMb = value.hash_mb;
  settings.scheduledStart = toLocalDateTime(value.scheduled_start_at);
  visibility.published = !!props.detail.event.published_at;
  const availablePuzzleIds = new Set(payload.value.puzzles.map((puzzle) => puzzle.id));
  selectedPuzzleIds.value = locked.value ? [] : selectedPuzzleIds.value.filter((id) => availablePuzzleIds.has(id));
  if (!selectedEngine.value) selectedEngine.value = availableEngines.value[0]?.id ?? 0;
}

async function run(label: string, operation: () => Promise<unknown>): Promise<boolean> {
  if (pending.value) return false;
  pending.value = label;
  error.value = "";
  try {
    const result = await operation() as { message?: string };
    if (result?.message) toast.success(result.message);
    emit("changed");
    return true;
  } catch (cause) {
    error.value = errorText(cause);
    toast.error(cause);
    return false;
  } finally {
    pending.value = "";
  }
}

function solutionList(value: string): string[] {
  return value.split(/[,/]+/).map((item) => item.trim()).filter(Boolean);
}

function addPuzzle(): void {
  void run("quick-add", async () => {
    const result = await api.post(`/api/admin/events/${props.detail.event.id}/puzzle-gauntlet/puzzles`, {
      body: { fen: quick.fen, solutions: solutionList(quick.solutions), title: quick.title },
    });
    Object.assign(quick, { fen: "", solutions: "", title: "" });
    return result;
  });
}

async function addBulk(): Promise<void> {
  let puzzles: Array<{ fen: string; solutions: string[]; title: string }>;
  try {
    puzzles = bulkText.value.split(/\r?\n/).map((line, index) => ({ line: line.trim(), number: index + 1 })).filter(({ line }) => line).map(({ line, number }) => {
      const separator = line.indexOf("|");
      if (separator < 0 || line.indexOf("|", separator + 1) >= 0) throw new Error(`Line ${number} must use fen|solution.`);
      const fen = line.slice(0, separator).trim();
      const solutions = solutionList(line.slice(separator + 1));
      if (!fen) throw new Error(`Line ${number} is missing a FEN.`);
      if (!solutions.length) throw new Error(`Line ${number} is missing a solution.`);
      return { fen, solutions, title: "" };
    });
    if (!puzzles.length) throw new Error("Paste at least one puzzle.");
  } catch (cause) {
    bulkError.value = errorText(cause);
    return;
  }
  bulkError.value = "";
  const completed = await run("bulk-add", () => api.post(`/api/admin/events/${props.detail.event.id}/puzzle-gauntlet/puzzles/bulk`, { body: { puzzles } }));
  if (completed) {
    bulkText.value = "";
    bulkOpen.value = false;
  } else {
    bulkError.value = error.value;
  }
}

function beginEdit(puzzle: GauntletPuzzle): void {
  editingId.value = puzzle.id;
  Object.assign(edit, { fen: puzzle.fen, solutions: puzzle.solutions?.join(", ") ?? "", title: puzzle.title });
}

function savePuzzle(): void {
  if (!editingId.value) return;
  const id = editingId.value;
  void run(`edit-${id}`, async () => {
    const result = await api.put(`/api/admin/events/${props.detail.event.id}/puzzle-gauntlet/puzzles/${id}`, {
      body: { fen: edit.fen, solutions: solutionList(edit.solutions), title: edit.title },
    });
    editingId.value = null;
    return result;
  });
}

async function removePuzzle(puzzle: GauntletPuzzle): Promise<void> {
  const accepted = await confirm({ title: "Remove puzzle?", message: `Remove puzzle ${puzzle.position + 1} from this gauntlet?`, confirmLabel: "Remove", tone: "danger" });
  if (!accepted) return;
  void run(`delete-${puzzle.id}`, () => api.delete(`/api/admin/events/${props.detail.event.id}/puzzle-gauntlet/puzzles/${puzzle.id}`));
}

function toggleAllPuzzles(): void {
  selectedPuzzleIds.value = allPuzzlesSelected.value ? [] : payload.value.puzzles.map((puzzle) => puzzle.id);
}

async function removeSelectedPuzzles(): Promise<void> {
  const ids = [...selectedPuzzleIds.value];
  if (!ids.length) return;
  const accepted = await confirm({
    title: `Remove ${ids.length} puzzles?`,
    message: `Remove the selected ${ids.length} ${ids.length === 1 ? "puzzle" : "puzzles"} from this gauntlet?`,
    confirmLabel: "Remove selected",
    tone: "danger",
  });
  if (!accepted) return;
  const completed = await run("bulk-delete", () => api.delete(`/api/admin/events/${props.detail.event.id}/puzzle-gauntlet/puzzles`, { body: { puzzle_ids: ids } }));
  if (!completed) return;
  if (editingId.value !== null && ids.includes(editingId.value)) editingId.value = null;
  selectedPuzzleIds.value = [];
}

function movePuzzle(index: number, offset: number): void {
  const ids = payload.value.puzzles.map((puzzle) => puzzle.id);
  const target = index + offset;
  if (target < 0 || target >= ids.length) return;
  [ids[index], ids[target]] = [ids[target]!, ids[index]!];
  void run("order", () => api.put(`/api/admin/events/${props.detail.event.id}/puzzle-gauntlet/puzzle-order`, { body: { puzzle_ids: ids } }));
}

function addEngine(): void {
  if (!selectedEngine.value) return;
  void run("add-engine", async () => {
    const result = await api.post(`/api/admin/events/${props.detail.event.id}/puzzle-gauntlet/entries`, { body: { engine_id: selectedEngine.value } });
    selectedEngine.value = 0;
    return result;
  });
}

function removeEngine(id: number): void {
  void run(`remove-engine-${id}`, () => api.delete(`/api/admin/events/${props.detail.event.id}/puzzle-gauntlet/entries/${id}`));
}

function saveSettings(): void {
  void run("settings", () => api.put(`/api/admin/events/${props.detail.event.id}/puzzle-gauntlet/settings`, {
    body: {
      start_time_ms: Math.round(settings.startSeconds * 1000),
      decrement_ms: Math.round(settings.decrementSeconds * 1000),
      minimum_time_ms: Math.round(settings.minimumSeconds * 1000),
      threads: settings.threads,
      hash_mb: settings.hashMb,
      scheduled_start_at: settings.scheduledStart ? new Date(settings.scheduledStart).toISOString() : null,
    },
  }));
}

function saveVisibility(): void {
  void run("visibility", () => api.put(`/api/admin/events/${props.detail.event.id}/puzzle-gauntlet/visibility`, { body: visibility }));
}

function startGauntlet(): void {
  void run("start", () => api.post(`/api/admin/events/${props.detail.event.id}/puzzle-gauntlet/start`));
}

async function applyAction(action: "pause" | "resume" | "abort"): Promise<void> {
  if (action === "abort") {
    const accepted = await confirm({ title: "Abort gauntlet?", message: "Active searches will stop and no winner will be declared.", confirmLabel: "Abort", tone: "danger" });
    if (!accepted) return;
  }
  void run(action, () => api.post(`/api/admin/events/${props.detail.event.id}/puzzle-gauntlet/action`, { body: { action } }));
}

function timeLabel(value: number): string {
  return value >= 60000 ? `${Math.round(value / 6000) / 10}m` : `${Math.round(value / 100) / 10}s`;
}

function barHeight(value: number): string {
  const max = Math.max(...timeRange.value, 1);
  return `${Math.max(12, value / max * 100)}%`;
}

function toLocalDateTime(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return shifted.toISOString().slice(0, 16);
}
</script>

<template>
  <div class="gauntlet-control">
    <section class="command-deck panel">
      <div class="command-brand"><span class="puzzle-glyph"><i></i></span><div><small>Event module</small><strong>Puzzle Gauntlet</strong></div></div>
      <nav><button type="button" :class="{ active: tab === 'puzzles' }" @click="tab = 'puzzles'"><AppIcon name="book-open" :size="14" />Builder <span>{{ payload.puzzles.length }}</span></button><button type="button" :class="{ active: tab === 'field' }" @click="tab = 'field'"><AppIcon name="engine" :size="14" />Engine field <span>{{ payload.entries.length }}</span></button><button type="button" :class="{ active: tab === 'run' }" @click="tab = 'run'"><AppIcon name="radio" :size="14" />Run of show</button></nav>
      <form class="publish-control" @submit.prevent="saveVisibility"><label><input v-model="visibility.published" type="checkbox"><span>{{ visibility.published ? "Public" : "Private" }}</span></label><button class="button button--secondary button--small" type="submit" :disabled="!!pending">{{ pending === "visibility" ? "Saving…" : "Save" }}</button></form>
    </section>

    <p v-if="error" class="control-error" role="alert"><AppIcon name="alert-circle" :size="15" />{{ error }}</p>

    <template v-if="tab === 'puzzles'">
      <section class="builder-grid">
        <form class="panel quick-builder" @submit.prevent="addPuzzle">
          <header class="quick-builder__header"><div><span>Rapid entry</span><h2>Add the next puzzle</h2><p>Paste a FEN, enter the accepted move, hit Enter. Focus stays in the flow.</p></div><button class="button button--secondary button--small" type="button" :disabled="locked" @click="bulkError = ''; bulkOpen = true"><AppIcon name="upload" :size="14" />Add a batch</button></header>
          <label><span>FEN</span><textarea v-model="quick.fen" rows="3" placeholder="8/8/… w - - 0 1" :disabled="locked" autofocus></textarea></label>
          <div class="solution-row"><label><span>Solution</span><input v-model="quick.solutions" class="input" placeholder="e2e4 or e2e4, d2d4" :disabled="locked" @keydown.enter.prevent="addPuzzle"></label><label><span>Optional label</span><input v-model="quick.title" class="input" placeholder="Back-rank shot" :disabled="locked" @keydown.enter.prevent="addPuzzle"></label></div>
          <button class="button button--primary" type="submit" :disabled="locked || pending === 'quick-add' || !quick.fen || !quick.solutions"><AppIcon name="plus" :size="15" />{{ pending === "quick-add" ? "Adding…" : "Add & continue" }}</button>
        </form>

        <section class="panel board-preview"><header><span>Position preview</span><strong>{{ editingId ? "Editing puzzle" : quick.fen ? "New puzzle" : "Latest puzzle" }}</strong></header><div><ChessBoard :fen="previewFen" :controls="false" :coordinates="true" label="Puzzle builder preview" /></div><footer><span>Accepted move</span><strong>{{ editingId ? edit.solutions || "-" : quick.solutions || payload.puzzles.at(-1)?.solutions?.join(" / ") || "-" }}</strong></footer></section>
      </section>

      <section class="panel puzzle-queue">
        <header>
          <div><span>Challenge sequence</span><h2>{{ payload.puzzles.length }} {{ payload.puzzles.length === 1 ? "puzzle" : "puzzles" }} ready</h2></div>
          <div v-if="payload.puzzles.length" class="queue-header-actions">
            <button class="button button--ghost button--small" type="button" :disabled="locked || pending === 'bulk-delete'" @click="toggleAllPuzzles">{{ allPuzzlesSelected ? "Clear selection" : "Select all" }}</button>
            <button class="button button--danger button--small" type="button" :disabled="locked || !selectedPuzzleIds.length || pending === 'bulk-delete'" @click="removeSelectedPuzzles"><AppIcon name="trash" :size="14" />{{ pending === "bulk-delete" ? "Removing…" : `Remove selected (${selectedPuzzleIds.length})` }}</button>
          </div>
        </header>
        <div v-if="payload.puzzles.length" class="queue-list">
          <article v-for="(puzzle, index) in payload.puzzles" :key="puzzle.id" :class="{ editing: editingId === puzzle.id }">
            <label class="queue-position">
              <input v-model="selectedPuzzleIds" type="checkbox" :value="puzzle.id" :disabled="locked || pending === 'bulk-delete'" :aria-label="`Select puzzle ${index + 1}`">
              <span class="queue-index">{{ String(index + 1).padStart(2, "0") }}</span>
            </label>
            <template v-if="editingId !== puzzle.id">
              <div class="queue-copy"><strong>{{ puzzle.title || `Puzzle ${index + 1}` }}</strong><code>{{ puzzle.fen }}</code></div>
              <div class="solution-chips"><span v-for="move in puzzle.solutions" :key="move">{{ move }}</span></div>
              <strong class="queue-time">{{ timeLabel(puzzle.time_limit_ms) }}</strong>
              <div class="queue-actions"><button type="button" title="Move up" :disabled="locked || index === 0" @click="movePuzzle(index, -1)"><AppIcon name="chevron-left" :size="14" /></button><button type="button" title="Move down" :disabled="locked || index === payload.puzzles.length - 1" @click="movePuzzle(index, 1)"><AppIcon name="chevron-right" :size="14" /></button><button type="button" title="Edit puzzle" :disabled="locked" @click="beginEdit(puzzle)"><AppIcon name="edit" :size="14" /></button><button type="button" title="Remove puzzle" :disabled="locked" @click="removePuzzle(puzzle)"><AppIcon name="trash" :size="14" /></button></div>
            </template>
            <form v-else class="queue-edit" @submit.prevent="savePuzzle"><input v-model="edit.title" class="input" placeholder="Puzzle label"><input v-model="edit.fen" class="input queue-edit__fen" placeholder="FEN"><input v-model="edit.solutions" class="input" placeholder="Solutions"><button class="button button--primary button--small" type="submit">Save</button><button class="button button--ghost button--small" type="button" @click="editingId = null">Cancel</button></form>
          </article>
        </div>
        <div v-else class="queue-empty"><span class="puzzle-glyph"><i></i></span><strong>Your gauntlet is empty</strong><p>The first puzzle you add appears here instantly.</p></div>
      </section>
    </template>

    <template v-else-if="tab === 'field'">
      <section class="field-layout">
        <div class="panel field-card">
          <header><div><span>Contender roster</span><h2>Add engines one at a time</h2><p>Each engine receives an independent search on every active puzzle.</p></div><strong>{{ payload.entries.length }}</strong></header>
          <form class="engine-adder" @submit.prevent="addEngine"><label><span>Available engine version</span><EngineVersionPicker v-model="selectedEngine" :engines="availableEngines" placeholder="Choose an engine" :disabled="locked" /></label><button class="button button--primary" type="submit" :disabled="locked || !selectedEngine || pending === 'add-engine'"><AppIcon name="plus" :size="15" />Enter engine</button></form>
          <div class="roster-list"><article v-for="(entry, index) in payload.entries" :key="entry.id" :class="{ 'roster-entry--knocked-out': entry.status !== 'active' }"><span class="roster-seed">{{ String(index + 1).padStart(2, "0") }}</span><i>{{ entry.name.slice(0, 2).toUpperCase() }}</i><div><strong>{{ entry.name }}</strong><small>{{ entry.version }} · {{ entry.author }}</small></div><span class="roster-status" :class="entry.status">{{ entry.winner ? "winner" : entry.status }}</span><button type="button" :disabled="locked" title="Remove engine" @click="removeEngine(entry.id)"><AppIcon name="close" :size="14" /></button></article><p v-if="!payload.entries.length">No engines entered yet.</p></div>
        </div>

        <aside class="panel timing-card"><header><span>Pressure curve</span><h2>Time per puzzle</h2></header><div class="time-chart"><i v-for="(value, index) in timeRange" :key="index" :style="{ height: barHeight(value) }"><span>{{ index + 1 }}</span></i><p v-if="!timeRange.length">Add puzzles to preview the curve.</p></div><dl><div><dt>Opening clock</dt><dd>{{ settings.startSeconds }}s</dd></div><div><dt>Drop per round</dt><dd>−{{ settings.decrementSeconds }}s</dd></div><div><dt>Floor</dt><dd>{{ settings.minimumSeconds }}s</dd></div></dl></aside>
      </section>
    </template>

    <template v-else>
      <section class="run-layout">
        <form class="panel run-settings" @submit.prevent="saveSettings">
          <header><span>Run configuration</span><h2>Clock, resources & launch</h2><p>Save once, then arm now or at the scheduled time.</p></header>
          <div class="settings-grid">
            <label><span>First puzzle</span><div><input v-model.number="settings.startSeconds" class="input" type="number" min="0.1" step="0.1" :disabled="locked"><small>seconds</small></div></label>
            <label><span>Round decrease</span><div><input v-model.number="settings.decrementSeconds" class="input" type="number" min="0" step="0.1" :disabled="locked"><small>seconds</small></div></label>
            <label><span>Minimum clock</span><div><input v-model.number="settings.minimumSeconds" class="input" type="number" min="0.1" step="0.1" :disabled="locked"><small>seconds</small></div></label>
            <label><span>Threads / engine</span><input v-model.number="settings.threads" class="input" type="number" min="1" :disabled="locked"></label>
            <label><span>Hash / engine</span><div><input v-model.number="settings.hashMb" class="input" type="number" min="1" :disabled="locked"><small>MB</small></div></label>
            <label class="settings-wide"><span>Optional scheduled start</span><input v-model="settings.scheduledStart" class="input" type="datetime-local" :disabled="locked"></label>
          </div>
          <button class="button button--secondary" type="submit" :disabled="locked || pending === 'settings'"><AppIcon name="check" :size="15" />{{ pending === "settings" ? "Saving…" : "Save configuration" }}</button>
        </form>

        <aside class="panel launch-card"><header><span>Launch checklist</span><h2>{{ locked ? "Gauntlet runtime" : "Ready to arm?" }}</h2></header><div class="readiness"><div v-for="item in readiness" :key="item.label" :class="{ ready: item.ready }"><AppIcon :name="item.ready ? 'check-circle' : 'alert-circle'" :size="16" /><span><strong>{{ item.label }}</strong><small>{{ item.value }}</small></span></div></div><div v-if="payload.worker" class="gauntlet-worker"><AppIcon name="server" :size="14" /><span><strong>{{ payload.worker.label }}</strong> is {{ payload.tournament?.status === 'scheduled' ? payload.worker.prepared ? 'fully prepared and waiting for the scheduled start' : 'reserved and preparing every engine' : 'dedicated to this gauntlet' }}</span><StatusBadge :status="payload.worker.prepared ? 'ready' : payload.worker.status" /></div><dl><div><dt>Status</dt><dd>{{ detail.event.status }}</dd></div><div><dt>Starts</dt><dd>{{ formatDate(detail.event.scheduled_start_at) }}</dd></div><div><dt>Runtime</dt><dd>{{ payload.tournament ? `#${payload.tournament.id} · ${payload.tournament.status}` : "Not armed" }}</dd></div></dl><button v-if="!locked" class="button button--primary launch-button" type="button" :disabled="pending === 'start' || !readiness[0]?.ready || !readiness[1]?.ready" @click="startGauntlet"><AppIcon name="play" :size="16" />{{ pending === "start" ? "Arming…" : settings.scheduledStart ? "Arm scheduled gauntlet" : "Start Puzzle Gauntlet" }}</button><div v-else class="runtime-actions"><button v-if="payload.tournament?.status === 'running'" class="button button--secondary" type="button" @click="applyAction('pause')"><AppIcon name="pause" :size="15" />Pause</button><button v-if="payload.tournament?.status === 'paused'" class="button button--primary" type="button" @click="applyAction('resume')"><AppIcon name="play" :size="15" />Resume</button><button v-if="['scheduled', 'running', 'paused'].includes(payload.tournament?.status || '')" class="button button--danger" type="button" @click="applyAction('abort')"><AppIcon name="stop" :size="15" />Abort</button></div></aside>
      </section>

      <section v-if="payload.rounds.length" class="panel round-log"><header><span>Round log</span><h2>Gauntlet decisions</h2></header><div><article v-for="round in [...payload.rounds].reverse()" :key="round.puzzle_id"><span>{{ String(round.position + 1).padStart(2, "0") }}</span><div><strong>{{ round.title || `Puzzle ${round.position + 1}` }}</strong><small>{{ round.void ? "Everyone missed · field saved" : `${round.eliminated_ids.length} eliminated` }}</small></div><code>{{ round.solutions.join(" / ") }}</code></article></div></section>
    </template>

    <PuzzleBatchModal v-model="bulkText" :open="bulkOpen" :pending="pending === 'bulk-add'" :error="bulkError" @close="bulkOpen = false" @submit="addBulk" />
  </div>
</template>

<style scoped>
.gauntlet-control { display: grid; gap: 1rem; }.command-deck { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 1.2rem; padding: .55rem .7rem; overflow: hidden; }.command-brand { display: flex; align-items: center; gap: .6rem; padding-right: 1rem; border-right: 1px solid var(--color-border); }.command-brand > div { display: grid; gap: .08rem; }.command-brand small, .quick-builder header span, .puzzle-queue > header span, .field-card > header span, .timing-card header span, .run-settings header span, .launch-card header span, .round-log header span { color: #7c5ce0; font-size: .54rem; font-weight: 800; letter-spacing: .09em; text-transform: uppercase; }.command-brand strong { font-size: .72rem; }.puzzle-glyph { position: relative; display: block; width: 2rem; aspect-ratio: 1; border-radius: .42rem; background: linear-gradient(135deg, #8b5cf6, #5d3bbb); }.puzzle-glyph::before, .puzzle-glyph::after { position: absolute; border-radius: 50%; background: var(--color-surface, #fff); content: ""; }.puzzle-glyph::before { top: -.22rem; left: .7rem; width: .62rem; height: .62rem; }.puzzle-glyph::after { top: .7rem; right: -.22rem; width: .62rem; height: .62rem; }.command-deck nav { display: flex; align-items: center; gap: .2rem; }.command-deck nav button { display: flex; align-items: center; gap: .38rem; min-height: 2rem; padding: 0 .65rem; border: 0; border-radius: .42rem; background: transparent; color: var(--color-text-muted); cursor: pointer; font-size: .63rem; font-weight: 680; }.command-deck nav button.active { background: color-mix(in srgb, #8b5cf6 9%, var(--color-surface-subtle)); color: #6943cf; }.command-deck nav button span { display: grid; min-width: 1.1rem; height: 1.1rem; place-items: center; border-radius: 999px; background: color-mix(in srgb, currentColor 9%, transparent); font-size: .48rem; }.publish-control, .publish-control label { display: flex; align-items: center; gap: .45rem; }.publish-control label { font-size: .61rem; font-weight: 700; }.control-error { display: flex; align-items: center; gap: .45rem; margin: 0; padding: .65rem .8rem; border: 1px solid color-mix(in srgb, var(--color-danger) 25%, var(--color-border)); border-radius: .55rem; background: color-mix(in srgb, var(--color-danger) 6%, var(--color-surface)); color: var(--color-danger); font-size: .66rem; }
.builder-grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(18rem, .75fr); gap: 1rem; }.quick-builder { display: grid; gap: .8rem; padding: 1rem; }.quick-builder__header { display: flex; align-items: start; justify-content: space-between; gap: 1rem; }.quick-builder__header > div { min-width: 0; }.quick-builder__header > button { flex: 0 0 auto; }.quick-builder header h2, .puzzle-queue > header h2, .field-card > header h2, .timing-card h2, .run-settings h2, .launch-card h2, .round-log h2 { margin: .15rem 0 0; font-size: 1rem; }.quick-builder header p, .field-card > header p, .run-settings header p { margin: .25rem 0 0; color: var(--color-text-muted); font-size: .64rem; }.quick-builder label, .engine-adder label, .settings-grid label { display: grid; gap: .3rem; }.quick-builder label > span, .engine-adder label > span, .settings-grid label > span { color: var(--color-text-muted); font-size: .56rem; font-weight: 750; }.quick-builder textarea { width: 100%; resize: vertical; border: 1px solid var(--color-border); border-radius: .48rem; background: var(--color-surface); color: var(--color-text); font: .65rem/1.5 ui-monospace, monospace; padding: .6rem; }.solution-row { display: grid; grid-template-columns: 1fr 1fr; gap: .65rem; }.quick-builder > button { justify-self: start; }.board-preview { display: grid; grid-template-rows: auto 1fr auto; overflow: hidden; padding: 0; }.board-preview > header, .board-preview > footer { display: flex; align-items: center; justify-content: space-between; padding: .65rem .8rem; }.board-preview > header { border-bottom: 1px solid var(--color-border); }.board-preview > header span, .board-preview > footer span { color: var(--color-text-muted); font-size: .55rem; text-transform: uppercase; }.board-preview > header strong, .board-preview > footer strong { font-size: .62rem; }.board-preview > div { display: grid; align-items: center; padding: .7rem; background: color-mix(in srgb, #8b5cf6 5%, var(--color-surface-subtle)); }.board-preview > footer { border-top: 1px solid var(--color-border); }.board-preview > footer strong { color: #6943cf; font-family: ui-monospace, monospace; }
.puzzle-queue { overflow: hidden; padding: 0; }.puzzle-queue > header { display: flex; align-items: end; justify-content: space-between; padding: .8rem 1rem; border-bottom: 1px solid var(--color-border); }.puzzle-queue > header p { margin: 0; color: var(--color-text-muted); font-size: .59rem; }.queue-list { display: grid; }.queue-list article { display: grid; grid-template-columns: 2.3rem minmax(12rem, 1fr) minmax(7rem, auto) 3.5rem auto; align-items: center; gap: .65rem; min-width: 0; padding: .55rem .75rem; }.queue-list article + article { border-top: 1px solid var(--color-border); }.queue-index { color: #8b5cf6; font: 700 .6rem ui-monospace, monospace; }.queue-copy { display: grid; min-width: 0; gap: .12rem; }.queue-copy strong { font-size: .66rem; }.queue-copy code { overflow: hidden; color: var(--color-text-muted); font-size: .53rem; text-overflow: ellipsis; white-space: nowrap; }.solution-chips { display: flex; flex-wrap: wrap; gap: .2rem; }.solution-chips span { padding: .2rem .35rem; border-radius: .3rem; background: color-mix(in srgb, #8b5cf6 9%, var(--color-surface-subtle)); color: #6943cf; font: 700 .52rem ui-monospace, monospace; }.queue-time { color: var(--color-text-muted); font: .6rem ui-monospace, monospace; }.queue-actions { display: flex; gap: .18rem; }.queue-actions button, .roster-list article > button { display: grid; width: 1.65rem; aspect-ratio: 1; place-items: center; border: 1px solid var(--color-border); border-radius: .35rem; background: var(--color-surface); color: var(--color-text-muted); cursor: pointer; }.queue-actions button:disabled, .roster-list article > button:disabled { opacity: .35; cursor: default; }.queue-actions button:first-child svg { transform: rotate(90deg); }.queue-actions button:nth-child(2) svg { transform: rotate(90deg); }.queue-edit { display: grid; grid-column: 2 / -1; grid-template-columns: .5fr 1.6fr .7fr auto auto; gap: .4rem; }.queue-empty { display: grid; min-height: 10rem; place-content: center; justify-items: center; color: var(--color-text-muted); text-align: center; }.queue-empty .puzzle-glyph { margin-bottom: .6rem; opacity: .55; }.queue-empty strong { color: var(--color-text); font-size: .74rem; }.queue-empty p { margin: .2rem 0 0; font-size: .6rem; }
.field-layout, .run-layout { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(18rem, .7fr); gap: 1rem; align-items: start; }.field-card, .timing-card, .run-settings, .launch-card { overflow: hidden; padding: 0; }.field-card > header { display: flex; justify-content: space-between; padding: .9rem 1rem; border-bottom: 1px solid var(--color-border); }.field-card > header > strong { color: #8b5cf6; font-size: 1.7rem; }.engine-adder { display: grid; grid-template-columns: 1fr auto; align-items: end; gap: .55rem; padding: .75rem 1rem; background: var(--color-surface-subtle); }.roster-list { display: grid; }.roster-list article { display: grid; grid-template-columns: 2rem 2.2rem minmax(0, 1fr) auto auto; align-items: center; gap: .6rem; padding: .62rem 1rem; border-left: 3px solid var(--engine); }.roster-list article + article { border-top: 1px solid var(--color-border); }.roster-seed { color: var(--color-text-muted); font: .56rem ui-monospace, monospace; }.roster-list article > i { display: grid; width: 2.1rem; aspect-ratio: 1; place-items: center; border-radius: .45rem; background: color-mix(in srgb, var(--engine) 13%, var(--color-surface-subtle)); color: var(--engine); font-size: .55rem; font-style: normal; font-weight: 900; }.roster-list article > div { display: grid; min-width: 0; }.roster-list article strong { font-size: .67rem; }.roster-list article small { color: var(--color-text-muted); font-size: .55rem; }.roster-status { padding: .2rem .38rem; border-radius: 999px; background: color-mix(in srgb, var(--color-success) 10%, transparent); color: var(--color-success); font-size: .49rem; font-weight: 800; text-transform: uppercase; }.roster-status.eliminated { background: color-mix(in srgb, var(--color-danger) 8%, transparent); color: var(--color-danger); }.roster-list > p { padding: 1rem; color: var(--color-text-muted); font-size: .65rem; }.timing-card header, .run-settings > header, .launch-card > header, .round-log > header { padding: .85rem 1rem; border-bottom: 1px solid var(--color-border); }.time-chart { display: flex; align-items: end; gap: .22rem; height: 13rem; padding: 1rem 1rem .6rem; background: linear-gradient(var(--color-border) 1px, transparent 1px) 0 0 / 100% 25%; }.time-chart i { position: relative; flex: 1; min-width: .25rem; max-width: 1.3rem; border-radius: .25rem .25rem 0 0; background: linear-gradient(#8b5cf6, #22d3ee); }.time-chart i span { position: absolute; bottom: -1rem; left: 50%; color: var(--color-text-muted); font-size: .43rem; transform: translateX(-50%); }.time-chart p { align-self: center; color: var(--color-text-muted); font-size: .6rem; }.timing-card dl, .launch-card dl { display: grid; margin: 0; }.timing-card dl div, .launch-card dl div { display: flex; justify-content: space-between; padding: .55rem 1rem; border-top: 1px solid var(--color-border); }.timing-card dt, .launch-card dt { color: var(--color-text-muted); font-size: .56rem; }.timing-card dd, .launch-card dd { margin: 0; font-size: .61rem; font-weight: 700; }
.run-settings > header p { max-width: none; }.settings-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: .7rem; padding: 1rem; }.settings-grid label > div { position: relative; }.settings-grid label > div .input { width: 100%; padding-right: 3.2rem; }.settings-grid label > div small { position: absolute; top: 50%; right: .6rem; color: var(--color-text-muted); font-size: .5rem; transform: translateY(-50%); }.settings-wide { grid-column: 1 / -1; }.run-settings > button { margin: 0 1rem 1rem; }.readiness { display: grid; padding: .45rem 1rem; }.readiness > div { display: flex; align-items: center; gap: .55rem; padding: .48rem 0; color: var(--color-warning); }.readiness > div.ready { color: var(--color-success); }.readiness span { display: grid; }.readiness strong { color: var(--color-text); font-size: .62rem; }.readiness small { color: var(--color-text-muted); font-size: .52rem; }.gauntlet-worker { display: flex; align-items: center; gap: .45rem; margin: 0 .75rem .45rem; padding: .48rem .55rem; border: 1px solid color-mix(in srgb, var(--color-accent) 22%, var(--color-border)); border-radius: .45rem; background: color-mix(in srgb, var(--color-accent) 6%, var(--color-surface)); color: var(--color-text-muted); font-size: .6rem; }.gauntlet-worker > span { flex: 1; }.gauntlet-worker strong { color: var(--color-text); }.launch-button { width: calc(100% - 2rem); margin: .85rem 1rem 1rem; }.runtime-actions { display: flex; flex-wrap: wrap; gap: .45rem; padding: .85rem 1rem 1rem; }.runtime-actions .button { flex: 1; }.round-log { overflow: hidden; padding: 0; }.round-log > div { display: grid; }.round-log article { display: grid; grid-template-columns: 2rem minmax(0, 1fr) auto; align-items: center; gap: .6rem; padding: .6rem 1rem; }.round-log article + article { border-top: 1px solid var(--color-border); }.round-log article > span { color: #8b5cf6; font: .6rem ui-monospace, monospace; }.round-log article > div { display: grid; }.round-log article strong { font-size: .64rem; }.round-log article small { color: var(--color-text-muted); font-size: .54rem; }.round-log code { color: var(--color-success); font-size: .56rem; }
.roster-list article { --engine: var(--color-primary, #2d63bf); }
.roster-list article.roster-entry--knocked-out { --engine: #7b8495; }
@media (max-width: 70rem) { .builder-grid, .field-layout, .run-layout { grid-template-columns: 1fr; }.board-preview { grid-template-columns: auto minmax(0, 1fr) auto; grid-template-rows: auto; }.board-preview > header { border-right: 1px solid var(--color-border); border-bottom: 0; writing-mode: vertical-rl; }.board-preview > footer { border-top: 0; border-left: 1px solid var(--color-border); }.timing-card { display: grid; grid-template-columns: auto 1fr auto; }.timing-card header { border-right: 1px solid var(--color-border); border-bottom: 0; }.timing-card dl { min-width: 13rem; } }
@media (max-width: 50rem) { .command-deck { grid-template-columns: 1fr auto; }.command-brand { display: none; }.command-deck nav { overflow: auto; }.queue-list article { grid-template-columns: 2rem minmax(0, 1fr) auto; }.solution-chips { grid-column: 2; }.queue-time { grid-column: 3; grid-row: 1; }.queue-actions { grid-column: 3; grid-row: 2; }.queue-edit { grid-column: 2 / -1; grid-template-columns: 1fr auto auto; }.queue-edit__fen { grid-column: 1 / -1; }.settings-grid { grid-template-columns: repeat(2, 1fr); }.settings-wide { grid-column: 1 / -1; } }
@media (max-width: 36rem) { .command-deck { grid-template-columns: 1fr; }.publish-control { justify-content: space-between; }.solution-row, .engine-adder, .settings-grid { grid-template-columns: 1fr; }.settings-wide { grid-column: auto; }.board-preview { display: grid; grid-template-columns: 1fr; }.board-preview > header { border-right: 0; border-bottom: 1px solid var(--color-border); writing-mode: initial; }.board-preview > footer { border-top: 1px solid var(--color-border); border-left: 0; }.queue-list article { grid-template-columns: 1.5rem minmax(0, 1fr); }.solution-chips, .queue-time, .queue-actions { grid-column: 2; grid-row: auto; }.queue-edit { grid-column: 2; grid-template-columns: 1fr; }.timing-card { grid-template-columns: 1fr; }.timing-card header { border-right: 0; border-bottom: 1px solid var(--color-border); }.roster-list article { grid-template-columns: 1.5rem 2rem minmax(0, 1fr) auto; }.roster-status { grid-column: 3; }.round-log article { grid-template-columns: 1.5rem 1fr; }.round-log code { grid-column: 2; } }
.queue-header-actions { display: flex; flex-wrap: wrap; justify-content: end; gap: .4rem; }
.queue-position { display: flex; align-items: center; gap: .4rem; cursor: pointer; }
.queue-position input { width: 1rem; height: 1rem; margin: 0; accent-color: #8b5cf6; }
.queue-position input:disabled { cursor: default; opacity: .45; }
.queue-list article { grid-template-columns: 3.7rem minmax(12rem, 1fr) minmax(7rem, auto) 3.5rem auto; }
@media (max-width: 50rem) { .queue-list article { grid-template-columns: 3.4rem minmax(0, 1fr) auto; } }
@media (max-width: 36rem) { .puzzle-queue > header { align-items: start; gap: .6rem; }.queue-header-actions { justify-content: start; }.queue-list article { grid-template-columns: 3.2rem minmax(0, 1fr); } }
</style>
