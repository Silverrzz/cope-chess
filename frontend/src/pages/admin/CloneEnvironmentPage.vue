<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { api } from "@/api/client";
import AdminPageHeader from "@/components/admin/AdminPageHeader.vue";
import { errorText, formatDate } from "@/components/admin/format";
import InlineFeedback from "@/components/admin/InlineFeedback.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import BaseButton from "@/components/ui/BaseButton.vue";
import BaseInput from "@/components/ui/BaseInput.vue";

interface DatasetOption {
  key: string;
  label: string;
  description: string;
  dependencies: string[];
  default: boolean;
  available: boolean;
  warning: string;
}

interface DatasetGroup {
  label: string;
  datasets: DatasetOption[];
}

interface Preset {
  key: string;
  label: string;
  description: string;
  datasets: string[];
}

interface CloneStep {
  dataset_name: string;
  status: string;
  total_rows: number;
  completed_rows: number;
  total_bytes: number;
  completed_bytes: number;
  inserted_rows: number;
  skipped_rows: number;
  error: string;
}

interface CloneEvent {
  id: number;
  level: string;
  phase: string;
  dataset_name: string | null;
  message: string;
  detail: Record<string, unknown>;
  occurred_at: string;
}

interface CloneJob {
  id: number;
  source_url: string;
  source_instance_id: string;
  selection: { datasets: string[] };
  status: string;
  phase: string;
  total_datasets: number;
  completed_datasets: number;
  total_rows: number;
  completed_rows: number;
  total_bytes: number;
  completed_bytes: number;
  artifacts_total: number;
  artifacts_completed: number;
  artifacts_skipped: number;
  conflicts: number;
  warnings: number;
  error: string;
  created_at: string;
  started_at: string | null;
  updated_at: string;
  finished_at: string | null;
  steps: CloneStep[];
  events: CloneEvent[];
}

interface CloneContext {
  protocol_version: number;
  schema_version: number;
  instance_id: string;
  inventory: Record<string, number>;
  groups: DatasetGroup[];
  presets: Preset[];
  recent_jobs: CloneJob[];
}

interface PreflightResult extends CloneContext {
  source_url: string;
  app_version: string;
  compatible: boolean;
  destination_inventory: Record<string, number>;
}

const context = ref<CloneContext | null>(null);
const source = ref("");
const adminToken = ref("");
const explicitSelection = ref<string[]>([]);
const preflight = ref<PreflightResult | null>(null);
const activePreset = ref("engine_development");
const job = ref<CloneJob | null>(null);
const loading = ref(true);
const checking = ref(false);
const starting = ref(false);
const actioning = ref(false);
const error = ref("");
const streamState = ref("idle");
const followLog = ref(true);
const logFilter = ref("all");
const optionFilter = ref("");
const logRef = ref<HTMLElement | null>(null);
const now = ref(Date.now());
let eventSource: EventSource | null = null;
let clock: number | null = null;

const optionsByKey = computed(() => {
  const result = new Map<string, DatasetOption>();
  for (const group of context.value?.groups ?? []) {
    for (const option of group.datasets) result.set(option.key, option);
  }
  return result;
});

const expandedSelection = computed(() => {
  const selected = new Set(explicitSelection.value);
  const pending = [...selected];
  while (pending.length) {
    const key = pending.pop()!;
    for (const dependency of optionsByKey.value.get(key)?.dependencies ?? []) {
      if (!selected.has(dependency)) {
        selected.add(dependency);
        pending.push(dependency);
      }
    }
  }
  return selected;
});

const dependencyOnly = computed(() => {
  const explicit = new Set(explicitSelection.value);
  return new Set([...expandedSelection.value].filter((key) => !explicit.has(key)));
});

const filteredGroups = computed(() => {
  const needle = optionFilter.value.trim().toLowerCase();
  if (!needle) return context.value?.groups ?? [];
  return (context.value?.groups ?? [])
    .map((group) => ({
      ...group,
      datasets: group.datasets.filter((item) => `${item.label} ${item.description}`.toLowerCase().includes(needle)),
    }))
    .filter((group) => group.datasets.length);
});

const terminal = computed(() => Boolean(job.value && ["completed", "failed", "cancelled"].includes(job.value.status)));
const active = computed(() => Boolean(job.value && !terminal.value));
const cancellable = computed(() => Boolean(active.value && job.value?.status !== "cleaning_source"));

const progress = computed(() => {
  if (!job.value) return 0;
  if (job.value.status === "completed") return 100;
  if (job.value.status === "failed" || job.value.status === "cancelled") {
    return Math.max(0, Math.min(99, phaseProgress(job.value)));
  }
  return phaseProgress(job.value);
});

const phaseLabels: Record<string, string> = {
  queued: "Queued",
  waiting_source: "Building source snapshot",
  transferring: "Transferring and validating",
  importing: "Importing datasets",
  verifying: "Final verification",
  cleaning_source: "Cleaning source",
  completed: "Complete",
  failed: "Failed",
  cancelled: "Cancelled",
};

const elapsedMs = computed(() => {
  if (!job.value?.started_at) return 0;
  const end = job.value.finished_at ? Date.parse(job.value.finished_at) : now.value;
  return Math.max(0, end - Date.parse(job.value.started_at));
});

const bytesPerSecond = computed(() => {
  if (!job.value || !elapsedMs.value || !job.value.completed_bytes) return 0;
  return job.value.completed_bytes / (elapsedMs.value / 1000);
});

const remainingMs = computed(() => {
  if (!job.value || progress.value < 2 || terminal.value) return 0;
  return Math.max(0, elapsedMs.value * (100 / progress.value - 1));
});

const estimatedFinish = computed(() => remainingMs.value ? new Date(now.value + remainingMs.value) : null);
const selectedRows = computed(() => [...expandedSelection.value].reduce((total, key) => total + Number(preflight.value?.inventory[key] ?? 0), 0));
const selectedArtifactBytes = computed(() => expandedSelection.value.has("artifact_files") ? Number(preflight.value?.inventory.artifact_bytes ?? 0) : 0);
const visibleEvents = computed(() => (job.value?.events ?? []).filter((item) => logFilter.value === "all" || item.level === logFilter.value));

function phaseProgress(value: CloneJob): number {
  if (value.phase === "queued") return 1;
  if (value.phase === "waiting_source") return 5;
  if (value.phase === "transferring") {
    const ratio = value.total_bytes ? value.completed_bytes / value.total_bytes : 0;
    return Math.min(69, 10 + ratio * 59);
  }
  if (value.phase === "importing") {
    const ratio = value.total_rows ? value.completed_rows / value.total_rows : 0;
    return Math.min(95, 70 + ratio * 25);
  }
  if (value.phase === "verifying") return 97;
  if (value.phase === "cleaning_source") return 99;
  if (value.phase === "completed") return 100;
  return 0;
}

function applyPreset(key: string): void {
  const preset = context.value?.presets.find((item) => item.key === key);
  if (!preset) return;
  activePreset.value = key;
  explicitSelection.value = [...preset.datasets];
}

function toggleOption(key: string): void {
  activePreset.value = "custom";
  explicitSelection.value = explicitSelection.value.includes(key)
    ? explicitSelection.value.filter((item) => item !== key)
    : [...explicitSelection.value, key];
}

function toggleGroup(group: DatasetGroup): void {
  activePreset.value = "custom";
  const keys = group.datasets.filter((item) => item.available).map((item) => item.key);
  const allSelected = keys.every((key) => explicitSelection.value.includes(key));
  explicitSelection.value = allSelected
    ? explicitSelection.value.filter((key) => !keys.includes(key))
    : [...new Set([...explicitSelection.value, ...keys])];
}

function groupSelected(group: DatasetGroup): boolean {
  const values = group.datasets.filter((item) => item.available);
  return values.length > 0 && values.every((item) => expandedSelection.value.has(item.key));
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    context.value = await api.get<CloneContext>("/api/admin/tools/environment-clone");
    if (!explicitSelection.value.length) applyPreset("engine_development");
    const requested = Number(new URLSearchParams(window.location.search).get("job"));
    if (Number.isInteger(requested) && requested > 0) await openJob(requested);
  } catch (cause) {
    error.value = errorText(cause);
  } finally {
    loading.value = false;
  }
}

async function inspect(): Promise<void> {
  checking.value = true;
  error.value = "";
  preflight.value = null;
  try {
    preflight.value = await api.post<PreflightResult>("/api/admin/tools/environment-clone/preflight", {
      body: { source: source.value.trim(), admin_token: adminToken.value },
    });
  } catch (cause) {
    error.value = errorText(cause);
  } finally {
    checking.value = false;
  }
}

async function start(): Promise<void> {
  if (!preflight.value?.compatible || !expandedSelection.value.size) return;
  starting.value = true;
  error.value = "";
  try {
    const result = await api.post<{ job: CloneJob }>("/api/admin/tools/environment-clone", {
      body: {
        source: source.value.trim(),
        admin_token: adminToken.value,
        datasets: [...expandedSelection.value],
      },
    });
    adminToken.value = "";
    job.value = result.job;
    history.replaceState(null, "", `${location.pathname}?job=${result.job.id}`);
    connectStream(result.job.id);
  } catch (cause) {
    error.value = errorText(cause);
  } finally {
    starting.value = false;
  }
}

async function openJob(id: number): Promise<void> {
  error.value = "";
  try {
    const result = await api.get<{ job: CloneJob }>(`/api/admin/tools/environment-clone/jobs/${id}`);
    job.value = result.job;
    history.replaceState(null, "", `${location.pathname}?job=${id}`);
    if (!["completed", "failed", "cancelled"].includes(result.job.status)) connectStream(id);
  } catch (cause) {
    error.value = errorText(cause);
  }
}

function connectStream(id: number): void {
  eventSource?.close();
  streamState.value = "connecting";
  eventSource = new EventSource(`/api/admin/tools/environment-clone/jobs/${id}/events`, { withCredentials: true });
  eventSource.onopen = () => (streamState.value = "live");
  eventSource.onerror = () => (streamState.value = terminal.value ? "closed" : "reconnecting");
  eventSource.addEventListener("clone.snapshot", (event) => {
    job.value = JSON.parse((event as MessageEvent<string>).data) as CloneJob;
    if (terminal.value) {
      eventSource?.close();
      streamState.value = "closed";
    }
  });
}

async function cancel(): Promise<void> {
  if (!job.value) return;
  actioning.value = true;
  try {
    await api.post(`/api/admin/tools/environment-clone/jobs/${job.value.id}/cancel`);
    await openJob(job.value.id);
  } catch (cause) {
    error.value = errorText(cause);
  } finally {
    actioning.value = false;
  }
}

async function resume(): Promise<void> {
  if (!job.value) return;
  actioning.value = true;
  try {
    await api.post(`/api/admin/tools/environment-clone/jobs/${job.value.id}/resume`);
    await openJob(job.value.id);
  } catch (cause) {
    error.value = errorText(cause);
  } finally {
    actioning.value = false;
  }
}

function newClone(): void {
  eventSource?.close();
  job.value = null;
  preflight.value = null;
  source.value = "";
  adminToken.value = "";
  streamState.value = "idle";
  history.replaceState(null, "", location.pathname);
}

function formatBytes(value: number): string {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function formatDuration(value: number): string {
  const seconds = Math.max(0, Math.round(value / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  if (hours) return `${hours}h ${minutes.toString().padStart(2, "0")}m ${remainder.toString().padStart(2, "0")}s`;
  if (minutes) return `${minutes}m ${remainder.toString().padStart(2, "0")}s`;
  return `${remainder}s`;
}

function datasetLabel(key: string): string {
  return optionsByKey.value.get(key)?.label ?? key.replaceAll("_", " ");
}

watch([source, adminToken], () => {
  preflight.value = null;
});

watch(
  () => job.value?.events.length,
  async () => {
    if (!followLog.value) return;
    await nextTick();
    if (logRef.value) logRef.value.scrollTop = logRef.value.scrollHeight;
  },
);

onMounted(() => {
  clock = window.setInterval(() => (now.value = Date.now()), 1000);
  load();
});

onBeforeUnmount(() => {
  eventSource?.close();
  if (clock !== null) window.clearInterval(clock);
});
</script>

<template>
  <div class="admin-page page-stack clone-page">
    <AdminPageHeader title="Clone environment" description="Pull a verified, dependency-complete selection from another live Cope installation.">
      <template #actions>
        <BaseButton variant="ghost" to="/admin/tools"><template #icon><AppIcon name="arrow-left" :size="16" /></template>All tools</BaseButton>
        <BaseButton v-if="job" variant="secondary" @click="newClone"><template #icon><AppIcon name="plus" :size="16" /></template>New clone</BaseButton>
      </template>
    </AdminPageHeader>
    <InlineFeedback :message="error || job?.error || ''" />

    <template v-if="!job">
      <section class="source-panel panel">
        <div class="step-heading"><span>1</span><div><small>Source</small><h2>Connect to a Cope host</h2><p>The destination server performs the connection. The admin token is exchanged once and never saved in the clone job.</p></div></div>
        <div class="source-form">
          <BaseInput v-model="source" label="Source domain" placeholder="cope-chess.live" icon="server" :disabled="checking || starting" required />
          <BaseInput v-model="adminToken" label="Source admin token" type="password" placeholder="Paste the live host token" icon="key" autocomplete="off" :disabled="checking || starting" required />
          <BaseButton variant="primary" size="large" :loading="checking" :disabled="!source.trim() || !adminToken" @click="inspect">
            <template #icon><AppIcon name="activity" :size="17" /></template>Test connection
          </BaseButton>
        </div>
        <div v-if="preflight" class="connection-result" :class="{ 'connection-result--bad': !preflight.compatible }">
          <span class="connection-result__icon"><AppIcon :name="preflight.compatible ? 'check-circle' : 'alert-circle'" :size="22" /></span>
          <div><strong>{{ preflight.compatible ? "Source authenticated and compatible" : "Source authenticated but incompatible" }}</strong><small>{{ preflight.source_url }} · Cope {{ preflight.app_version }} · clone protocol {{ preflight.protocol_version }} · schema {{ preflight.schema_version }}</small></div>
          <code>{{ preflight.instance_id }}</code>
        </div>
      </section>

      <section class="selection-panel panel" :class="{ 'selection-panel--locked': !preflight }">
        <div class="step-heading"><span>2</span><div><small>Scope</small><h2>Choose exactly what to copy</h2><p>Prerequisites are added automatically and shown as dependencies. Stored access tokens, session credentials, and active execution state are removed.</p></div></div>
        <div class="preset-grid">
          <button v-for="preset in context?.presets" :key="preset.key" type="button" :class="{ active: activePreset === preset.key }" :disabled="!preflight" @click="applyPreset(preset.key)">
            <AppIcon :name="preset.key === 'engine_development' ? 'engine' : preset.key === 'safe_full' ? 'archive' : 'copy'" :size="19" />
            <span><strong>{{ preset.label }}</strong><small>{{ preset.description }}</small></span>
            <AppIcon v-if="activePreset === preset.key" name="check-circle" :size="17" />
          </button>
        </div>
        <label class="option-search"><AppIcon name="search" :size="16" /><input v-model="optionFilter" type="search" placeholder="Filter more than 50 granular data options…" :disabled="!preflight" /></label>
        <div class="dataset-groups">
          <section v-for="group in filteredGroups" :key="group.label" class="dataset-group">
            <header><button type="button" :disabled="!preflight" @click="toggleGroup(group)"><span :class="{ checked: groupSelected(group) }"><AppIcon v-if="groupSelected(group)" name="check" :size="13" /></span>{{ group.label }}</button><small>{{ group.datasets.filter((item) => expandedSelection.has(item.key)).length }}/{{ group.datasets.length }} selected</small></header>
            <div class="dataset-options">
              <label v-for="option in group.datasets" :key="option.key" :class="{ selected: expandedSelection.has(option.key), dependency: dependencyOnly.has(option.key), unavailable: !option.available }">
                <input type="checkbox" :checked="expandedSelection.has(option.key)" :disabled="!preflight || !option.available || dependencyOnly.has(option.key)" @change="toggleOption(option.key)" />
                <span><strong>{{ option.label }} <em v-if="dependencyOnly.has(option.key)">Dependency</em></strong><small>{{ option.description }}</small><small v-if="option.warning" class="option-warning">{{ option.warning }}</small></span>
                <b>{{ Number(preflight?.inventory[option.key] ?? 0).toLocaleString() }}</b>
              </label>
            </div>
          </section>
        </div>
      </section>

      <section class="review-panel panel" :class="{ 'review-panel--locked': !preflight }">
        <div class="step-heading"><span>3</span><div><small>Review</small><h2>Transfer plan</h2><p>The clone is additive. Existing primary keys are reused, mismatched constraints stop the affected dataset, and destination data is never deleted.</p></div></div>
        <div class="review-metrics">
          <div><small>Datasets</small><strong>{{ expandedSelection.size }}</strong><span>{{ explicitSelection.length }} chosen · {{ dependencyOnly.size }} dependencies</span></div>
          <div><small>Source rows</small><strong>{{ selectedRows.toLocaleString() }}</strong><span>Before snapshot compression</span></div>
          <div><small>Artifact transfer</small><strong>{{ formatBytes(selectedArtifactBytes) }}</strong><span>Existing verified files are reused</span></div>
          <div><small>Destination</small><strong>{{ Object.values(preflight?.destination_inventory ?? {}).reduce((sum, value) => sum + Number(value), 0).toLocaleString() }}</strong><span>Current portable records</span></div>
        </div>
        <div class="safety-strip"><AppIcon name="info" :size="18" /><span><strong>Safe live-state policy</strong>Running competitions are neutralized, workers and benchmarkers arrive offline, pending commands are terminalized, and known stored credentials are stripped.</span></div>
        <div class="start-row"><span v-if="!preflight">Test the source connection to unlock selection and review.</span><span v-else-if="!preflight.compatible">Upgrade both hosts to the same Cope schema and clone protocol.</span><span v-else>Ready to create a consistent source snapshot and begin the resumable transfer.</span><BaseButton variant="primary" size="large" :loading="starting" :disabled="!preflight?.compatible || !expandedSelection.size || !adminToken" @click="start"><template #icon><AppIcon name="download" :size="18" /></template>Start environment clone</BaseButton></div>
      </section>

      <section v-if="context?.recent_jobs.length" class="recent-panel panel">
        <header><div><small>History</small><h2>Recent environment clones</h2></div><span>{{ context.recent_jobs.length }} runs</span></header>
        <button v-for="item in context.recent_jobs" :key="item.id" type="button" @click="openJob(item.id)"><span class="recent-state" :class="`recent-state--${item.status}`"><AppIcon :name="item.status === 'completed' ? 'check' : item.status === 'failed' ? 'alert-circle' : 'refresh'" :size="15" /></span><span><strong>#{{ item.id }} · {{ item.source_url }}</strong><small>{{ formatDate(item.created_at) }}</small></span><span>{{ item.completed_datasets }}/{{ item.total_datasets }}</span><b>{{ phaseLabels[item.status] ?? item.status }}</b><AppIcon name="chevron-right" :size="16" /></button>
      </section>
    </template>

    <template v-else>
      <section class="run-hero panel" :class="`run-hero--${job.status}`">
        <div class="run-hero__top">
          <div><span class="eyebrow">Clone job #{{ job.id }}</span><h2>{{ job.source_url }}</h2><p>{{ phaseLabels[job.phase] ?? job.phase }} · started {{ job.started_at ? formatDate(job.started_at) : "waiting" }}</p></div>
          <span class="run-status"><i />{{ phaseLabels[job.status] ?? job.status }}</span>
        </div>
        <div class="overall-progress">
          <div><span>{{ phaseLabels[job.phase] ?? job.phase }}</span><strong>{{ progress.toFixed(1) }}%</strong></div>
          <div class="progress-track" role="progressbar" :aria-valuenow="progress" aria-valuemin="0" aria-valuemax="100"><span :style="{ width: `${progress}%` }" /></div>
          <div class="progress-foot"><span>{{ job.completed_datasets }}/{{ job.total_datasets }} datasets · {{ job.completed_rows.toLocaleString() }}/{{ job.total_rows.toLocaleString() }} rows</span><span>{{ formatBytes(job.completed_bytes) }}/{{ formatBytes(job.total_bytes) }}</span></div>
        </div>
        <div class="run-actions"><span class="stream-state" :class="`stream-state--${streamState}`"><i />{{ streamState === "live" ? "Live updates" : streamState }}</span><BaseButton v-if="cancellable" variant="danger" :loading="actioning" @click="cancel"><template #icon><AppIcon name="stop" :size="16" /></template>Cancel safely</BaseButton><BaseButton v-else-if="job.status === 'failed' || job.status === 'cancelled'" variant="primary" :loading="actioning" @click="resume"><template #icon><AppIcon name="refresh" :size="16" /></template>Resume clone</BaseButton></div>
      </section>

      <section class="telemetry-grid">
        <article class="panel"><AppIcon name="clock" :size="19" /><span><small>Elapsed</small><strong>{{ formatDuration(elapsedMs) }}</strong><em>{{ estimatedFinish ? `Finish near ${estimatedFinish.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : terminal ? "Finished" : "Calculating estimate" }}</em></span></article>
        <article class="panel"><AppIcon name="download" :size="19" /><span><small>Average transfer</small><strong>{{ bytesPerSecond ? `${formatBytes(bytesPerSecond)}/s` : "Calculating" }}</strong><em>{{ remainingMs ? `${formatDuration(remainingMs)} remaining` : phaseLabels[job.phase] }}</em></span></article>
        <article class="panel"><AppIcon name="archive" :size="19" /><span><small>Artifacts</small><strong>{{ job.artifacts_completed }}/{{ job.artifacts_total }}</strong><em>{{ job.artifacts_skipped }} verified and reused</em></span></article>
        <article class="panel"><AppIcon name="alert-circle" :size="19" /><span><small>Review signals</small><strong>{{ job.conflicts }} conflicts</strong><em>{{ job.warnings }} warnings</em></span></article>
      </section>

      <div class="run-layout">
        <main class="run-main">
          <section class="steps-panel panel">
            <header><div><span class="eyebrow">Pipeline</span><h2>Dataset progress</h2></div><span>{{ job.steps.length }} materialized datasets</span></header>
            <div class="step-list">
              <article v-for="step in job.steps" :key="step.dataset_name" :class="`step-row step-row--${step.status}`">
                <span class="step-state"><AppIcon :name="step.status === 'completed' ? 'check' : step.status === 'failed' ? 'alert-circle' : step.status === 'importing' || step.status === 'transferring' ? 'refresh' : 'clock'" :size="15" /></span>
                <span class="step-copy"><strong>{{ datasetLabel(step.dataset_name) }}</strong><small>{{ step.status }} · {{ step.completed_rows.toLocaleString() }}/{{ step.total_rows.toLocaleString() }} rows</small></span>
                <span class="step-transfer"><b>{{ formatBytes(step.completed_bytes) }}</b><small>of {{ formatBytes(step.total_bytes) }}</small></span>
                <span class="step-results"><b>{{ step.inserted_rows.toLocaleString() }} new</b><small>{{ step.skipped_rows.toLocaleString() }} reused</small></span>
                <div class="mini-track"><span :style="{ width: `${step.status === 'completed' ? 100 : step.total_bytes ? Math.min(100, step.completed_bytes / step.total_bytes * 100) : 0}%` }" /></div>
              </article>
              <p v-if="!job.steps.length" class="steps-empty">The source is building its consistent snapshot. Dataset totals will appear as soon as the manifest is ready.</p>
            </div>
          </section>
        </main>

        <aside class="run-aside">
          <section class="log-panel panel">
            <header><div><span class="eyebrow">Live log</span><h2>{{ visibleEvents.length }} entries</h2></div><label><input v-model="followLog" type="checkbox" />Follow</label></header>
            <div class="log-filters"><button v-for="level in ['all', 'info', 'success', 'warning', 'error']" :key="level" type="button" :class="{ active: logFilter === level }" @click="logFilter = level">{{ level }}</button></div>
            <div ref="logRef" class="live-log" role="log" aria-live="polite" aria-relevant="additions text">
              <article v-for="event in visibleEvents" :key="event.id" :class="`log-entry log-entry--${event.level}`"><time>{{ new Date(event.occurred_at).toLocaleTimeString() }}</time><span>{{ event.level }}</span><p><b v-if="event.dataset_name">{{ datasetLabel(event.dataset_name) }} · </b>{{ event.message }}</p></article>
              <p v-if="!visibleEvents.length" class="log-empty">Waiting for clone runner output…</p>
            </div>
          </section>
          <section class="details-panel panel"><span class="eyebrow">Run details</span><dl><div><dt>Source instance</dt><dd><code>{{ job.source_instance_id }}</code></dd></div><div><dt>Selected scope</dt><dd>{{ job.selection.datasets.length }} options</dd></div><div><dt>Created</dt><dd>{{ formatDate(job.created_at) }}</dd></div><div><dt>Updated</dt><dd>{{ formatDate(job.updated_at) }}</dd></div></dl></section>
        </aside>
      </div>
    </template>
  </div>
</template>

<style scoped>
.clone-page{gap:1rem}.step-heading{display:flex;gap:.85rem}.step-heading>span{align-items:center;background:var(--color-accent-soft);border:1px solid color-mix(in srgb,var(--color-accent) 25%,var(--color-border));border-radius:50%;color:var(--color-accent);display:flex;flex:0 0 auto;font-size:.72rem;font-weight:800;height:2rem;justify-content:center;width:2rem}.step-heading small,.eyebrow,.recent-panel header small{color:var(--color-accent);font-size:.6rem;font-weight:760;letter-spacing:.1em;text-transform:uppercase}.step-heading h2,.recent-panel h2,.steps-panel h2,.log-panel h2{font-size:1rem;margin:.14rem 0 0}.step-heading p{color:var(--color-text-muted);font-size:.72rem;line-height:1.5;margin:.3rem 0 0;max-width:72ch}.source-panel,.selection-panel,.review-panel{padding:1rem}.source-form{align-items:end;display:grid;gap:.75rem;grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto;margin-top:1rem}.connection-result{align-items:center;background:color-mix(in srgb,var(--color-success) 8%,var(--color-surface-sunken));border:1px solid color-mix(in srgb,var(--color-success) 26%,var(--color-border));border-radius:.65rem;color:var(--color-success);display:grid;gap:.65rem;grid-template-columns:auto minmax(0,1fr) auto;margin-top:.85rem;padding:.7rem}.connection-result--bad{background:color-mix(in srgb,var(--color-danger) 7%,var(--color-surface-sunken));border-color:color-mix(in srgb,var(--color-danger) 25%,var(--color-border));color:var(--color-danger)}.connection-result__icon{display:flex}.connection-result div{display:grid;gap:.14rem}.connection-result strong{font-size:.73rem}.connection-result small{color:var(--color-text-muted);font-size:.63rem}.connection-result code{color:var(--color-text-muted);font-size:.58rem}.selection-panel--locked,.review-panel--locked{opacity:.62}.preset-grid{display:grid;gap:.5rem;grid-template-columns:repeat(5,minmax(0,1fr));margin-top:1rem}.preset-grid button{align-items:start;background:var(--color-surface-sunken);border:1px solid var(--color-border);border-radius:.6rem;color:var(--color-text-muted);display:grid;gap:.45rem;grid-template-columns:auto minmax(0,1fr) auto;min-height:6.5rem;padding:.65rem;text-align:left}.preset-grid button:not(:disabled){cursor:pointer}.preset-grid button.active{background:var(--color-accent-soft);border-color:color-mix(in srgb,var(--color-accent) 40%,var(--color-border));color:var(--color-accent)}.preset-grid button span{display:grid;gap:.24rem}.preset-grid strong{color:var(--color-text);font-size:.7rem}.preset-grid small{font-size:.6rem;line-height:1.45}.option-search{align-items:center;background:var(--color-surface-sunken);border:1px solid var(--color-border);border-radius:.55rem;color:var(--color-text-muted);display:flex;gap:.5rem;margin-top:.8rem;padding:0 .65rem}.option-search input{background:transparent;border:0;color:var(--color-text);font-size:.7rem;min-height:2.4rem;outline:0;width:100%}.dataset-groups{display:grid;gap:.65rem;margin-top:.65rem}.dataset-group{border:1px solid var(--color-border);border-radius:.65rem;overflow:hidden}.dataset-group>header{align-items:center;background:var(--color-surface-sunken);display:flex;justify-content:space-between;padding:.55rem .7rem}.dataset-group>header button{align-items:center;background:transparent;border:0;color:var(--color-text);cursor:pointer;display:flex;font-size:.7rem;font-weight:730;gap:.45rem;padding:0}.dataset-group>header button>span{align-items:center;border:1px solid var(--color-border-strong);border-radius:.25rem;display:flex;height:1rem;justify-content:center;width:1rem}.dataset-group>header button>span.checked{background:var(--color-accent);border-color:var(--color-accent);color:var(--color-on-primary)}.dataset-group>header small{color:var(--color-text-muted);font-size:.6rem}.dataset-options{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}.dataset-options label{align-items:start;display:grid;gap:.55rem;grid-template-columns:auto minmax(0,1fr) auto;padding:.65rem}.dataset-options label:nth-child(even){border-left:1px solid var(--color-border)}.dataset-options label:nth-child(n+3){border-top:1px solid var(--color-border)}.dataset-options label.selected{background:color-mix(in srgb,var(--color-accent) 3%,transparent)}.dataset-options label.dependency{background:var(--color-surface-sunken)}.dataset-options label>input{margin-top:.12rem}.dataset-options label>span{display:grid;gap:.18rem}.dataset-options label strong{font-size:.68rem}.dataset-options label strong em{background:var(--color-accent-soft);border-radius:999px;color:var(--color-accent);font-size:.5rem;font-style:normal;margin-left:.3rem;padding:.15rem .3rem;text-transform:uppercase}.dataset-options label small{color:var(--color-text-muted);font-size:.59rem;line-height:1.4}.dataset-options label b{color:var(--color-text-faint);font-size:.58rem}.option-warning{color:var(--color-warning)!important}.review-metrics{display:grid;gap:1px;grid-template-columns:repeat(4,1fr);margin-top:1rem;overflow:hidden}.review-metrics>div{background:var(--color-surface-sunken);display:grid;gap:.2rem;padding:.75rem}.review-metrics small{color:var(--color-text-muted);font-size:.57rem;text-transform:uppercase}.review-metrics strong{font-size:1.05rem}.review-metrics span{color:var(--color-text-muted);font-size:.58rem}.safety-strip{align-items:start;background:color-mix(in srgb,var(--color-warning) 7%,var(--color-surface-sunken));border-left:3px solid var(--color-warning);display:flex;gap:.55rem;margin-top:.8rem;padding:.65rem;color:var(--color-warning)}.safety-strip span{color:var(--color-text-muted);display:grid;font-size:.62rem;gap:.15rem}.safety-strip strong{color:var(--color-text);font-size:.68rem}.start-row{align-items:center;border-top:1px solid var(--color-border);display:flex;justify-content:space-between;margin-top:.8rem;padding-top:.8rem}.start-row>span{color:var(--color-text-muted);font-size:.65rem}.recent-panel{overflow:hidden;padding:0}.recent-panel>header{align-items:center;border-bottom:1px solid var(--color-border);display:flex;justify-content:space-between;padding:.7rem .85rem}.recent-panel>header span{color:var(--color-text-muted);font-size:.62rem}.recent-panel>button{align-items:center;background:transparent;border:0;color:inherit;cursor:pointer;display:grid;gap:.65rem;grid-template-columns:auto minmax(0,1fr) auto auto auto;padding:.65rem .85rem;text-align:left;width:100%}.recent-panel>button+button{border-top:1px solid var(--color-border)}.recent-panel>button:hover{background:var(--color-surface-hover)}.recent-panel>button>span:nth-child(2){display:grid;gap:.12rem}.recent-panel>button strong{font-size:.68rem}.recent-panel>button small,.recent-panel>button>span:nth-child(3){color:var(--color-text-muted);font-size:.59rem}.recent-panel>button b{font-size:.58rem;text-transform:uppercase}.recent-state{align-items:center;background:var(--color-accent-soft);border-radius:.45rem;color:var(--color-accent);display:flex;height:1.8rem;justify-content:center;width:1.8rem}.recent-state--completed{background:color-mix(in srgb,var(--color-success) 10%,transparent);color:var(--color-success)}.recent-state--failed{background:color-mix(in srgb,var(--color-danger) 10%,transparent);color:var(--color-danger)}.run-hero{background:linear-gradient(120deg,color-mix(in srgb,var(--color-accent) 8%,var(--color-surface-raised)),var(--color-surface-raised));padding:1rem}.run-hero__top,.run-actions,.steps-panel>header,.log-panel>header{align-items:center;display:flex;justify-content:space-between}.run-hero h2{font-size:1.15rem;margin:.25rem 0 0}.run-hero p{color:var(--color-text-muted);font-size:.65rem;margin:.2rem 0 0}.run-status{align-items:center;background:var(--color-accent-soft);border-radius:999px;color:var(--color-accent);display:flex;font-size:.62rem;font-weight:730;gap:.4rem;padding:.35rem .55rem;text-transform:uppercase}.run-status i,.stream-state i{background:currentColor;border-radius:50%;height:.38rem;width:.38rem}.run-hero--completed .run-status{background:color-mix(in srgb,var(--color-success) 10%,transparent);color:var(--color-success)}.run-hero--failed .run-status,.run-hero--cancelled .run-status{background:color-mix(in srgb,var(--color-danger) 10%,transparent);color:var(--color-danger)}.overall-progress{margin-top:1rem}.overall-progress>div:first-child,.progress-foot{display:flex;justify-content:space-between}.overall-progress>div:first-child{font-size:.68rem}.progress-track{background:var(--color-surface-sunken);border-radius:999px;height:.65rem;margin:.4rem 0;overflow:hidden}.progress-track span,.mini-track span{background:linear-gradient(90deg,var(--color-accent),color-mix(in srgb,var(--color-accent) 60%,var(--color-success)));display:block;height:100%;transition:width .4s ease}.progress-foot{color:var(--color-text-muted);font-size:.6rem}.run-actions{border-top:1px solid var(--color-border);margin-top:.85rem;padding-top:.75rem}.stream-state{align-items:center;color:var(--color-text-muted);display:flex;font-size:.6rem;gap:.4rem;text-transform:capitalize}.stream-state--live{color:var(--color-success)}.telemetry-grid{display:grid;gap:.65rem;grid-template-columns:repeat(4,1fr)}.telemetry-grid article{align-items:start;display:flex;gap:.6rem;padding:.75rem}.telemetry-grid article>svg{color:var(--color-accent)}.telemetry-grid span{display:grid;gap:.16rem}.telemetry-grid small{color:var(--color-text-muted);font-size:.56rem;text-transform:uppercase}.telemetry-grid strong{font-size:.85rem}.telemetry-grid em{color:var(--color-text-muted);font-size:.57rem;font-style:normal}.run-layout{display:grid;gap:.75rem;grid-template-columns:minmax(0,1.55fr) minmax(20rem,.8fr)}.run-main,.run-aside{display:grid;gap:.75rem}.steps-panel,.log-panel,.details-panel{overflow:hidden;padding:0}.steps-panel>header,.log-panel>header{border-bottom:1px solid var(--color-border);padding:.7rem .8rem}.steps-panel>header>span{color:var(--color-text-muted);font-size:.58rem}.step-list{display:grid}.step-row{align-items:center;display:grid;gap:.55rem;grid-template-columns:auto minmax(0,1fr) auto auto;padding:.6rem .75rem;position:relative}.step-row+.step-row{border-top:1px solid var(--color-border)}.step-state{align-items:center;background:var(--color-surface-sunken);border-radius:.4rem;color:var(--color-text-faint);display:flex;height:1.75rem;justify-content:center;width:1.75rem}.step-row--completed .step-state{background:color-mix(in srgb,var(--color-success) 10%,transparent);color:var(--color-success)}.step-row--transferring .step-state,.step-row--importing .step-state{background:var(--color-accent-soft);color:var(--color-accent)}.step-row--transferring .step-state svg,.step-row--importing .step-state svg{animation:spin 1s linear infinite}.step-copy,.step-transfer,.step-results{display:grid;gap:.12rem}.step-copy strong{font-size:.67rem}.step-copy small,.step-transfer small,.step-results small{color:var(--color-text-muted);font-size:.55rem}.step-transfer b,.step-results b{font-size:.59rem;text-align:right}.mini-track{background:var(--color-surface-sunken);bottom:0;height:2px;left:0;position:absolute;right:0}.steps-empty,.log-empty{color:var(--color-text-muted);font-size:.65rem;padding:1.5rem;text-align:center}.log-panel>header label{align-items:center;color:var(--color-text-muted);display:flex;font-size:.6rem;gap:.35rem}.log-filters{display:flex;gap:.3rem;padding:.5rem .65rem}.log-filters button{background:var(--color-surface-sunken);border:1px solid transparent;border-radius:999px;color:var(--color-text-muted);cursor:pointer;font-size:.52rem;padding:.2rem .38rem;text-transform:uppercase}.log-filters button.active{background:var(--color-accent-soft);border-color:color-mix(in srgb,var(--color-accent) 25%,transparent);color:var(--color-accent)}.live-log{background:#0d1421;color:#cbd5e1;font-family:var(--font-mono);height:28rem;overflow:auto;padding:.5rem}.log-entry{display:grid;font-size:.56rem;gap:.4rem;grid-template-columns:auto auto minmax(0,1fr);line-height:1.5;padding:.22rem}.log-entry time{color:#64748b}.log-entry>span{color:#60a5fa;text-transform:uppercase}.log-entry--success>span{color:#4ade80}.log-entry--warning>span{color:#facc15}.log-entry--error>span{color:#f87171}.log-entry p{margin:0;overflow-wrap:anywhere}.log-entry b{color:#94a3b8}.details-panel{padding:.75rem}.details-panel dl{display:grid;gap:.55rem;margin:.65rem 0 0}.details-panel dl div{display:grid;gap:.16rem}.details-panel dt{color:var(--color-text-muted);font-size:.54rem;text-transform:uppercase}.details-panel dd{font-size:.62rem;margin:0;overflow-wrap:anywhere}.details-panel code{font-size:.56rem}@keyframes spin{to{transform:rotate(360deg)}}@media(max-width:72rem){.preset-grid{grid-template-columns:repeat(3,1fr)}.telemetry-grid{grid-template-columns:repeat(2,1fr)}.run-layout{grid-template-columns:1fr}.live-log{height:22rem}}@media(max-width:52rem){.source-form,.dataset-options{grid-template-columns:1fr}.dataset-options label:nth-child(even){border-left:0}.dataset-options label:nth-child(n+2){border-top:1px solid var(--color-border)}.preset-grid{grid-template-columns:1fr}.review-metrics{grid-template-columns:repeat(2,1fr)}.start-row{align-items:stretch;flex-direction:column;gap:.7rem}.connection-result{grid-template-columns:auto 1fr}.connection-result code{grid-column:1/-1}.step-transfer{display:none}}@media(max-width:34rem){.telemetry-grid,.review-metrics{grid-template-columns:1fr}.step-row{grid-template-columns:auto minmax(0,1fr) auto}.step-results{display:none}.recent-panel>button{grid-template-columns:auto minmax(0,1fr) auto}.recent-panel>button>span:nth-child(3),.recent-panel>button>b{display:none}}
</style>
