<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import { ApiError, api } from "@/api/client";
import AdminEmptyState from "@/components/admin/AdminEmptyState.vue";
import AdminPageHeader from "@/components/admin/AdminPageHeader.vue";
import InlineFeedback from "@/components/admin/InlineFeedback.vue";
import StatusBadge from "@/components/admin/StatusBadge.vue";
import { errorText, formatDate, humanize } from "@/components/admin/format";
import AppIcon from "@/components/ui/AppIcon.vue";
import BaseButton from "@/components/ui/BaseButton.vue";
import BaseInput from "@/components/ui/BaseInput.vue";
import { useConfirm } from "@/composables/useConfirm";
import type { IconName } from "@/types/icons";

type UpdateMethodId = "web" | "platform" | "dockerfiles";

interface UpdateMethod {
  id: UpdateMethodId;
  label: string;
}

interface DeploymentTarget {
  id: number;
  target_kind: "server" | "worker" | "benchmarker";
  target_id: number | null;
  label: string;
  current_commit: string | null;
  target_commit: string | null;
  status: string;
  detail: string;
  updated_at: string;
}

interface DeploymentJob {
  id: number;
  requested_ref: string;
  scope: "platform" | "web";
  target_commit: string | null;
  status: string;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  targets: DeploymentTarget[];
}

interface DockerfilePullJob {
  id: number;
  requested_ref: string;
  target_commit: string | null;
  status: string;
  files_updated: number;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

interface DeploymentsPayload {
  current_version: string;
  default_ref: string;
  updater: { service: string; app_version: string; last_seen: string } | null;
  methods: UpdateMethod[];
  dockerfile_pull: DockerfilePullJob | null;
  jobs: DeploymentJob[];
}

const fallbackMethods: UpdateMethod[] = [
  {
    id: "web",
    label: "Web application",
  },
  {
    id: "platform",
    label: "Full platform",
  },
  {
    id: "dockerfiles",
    label: "Engine definitions",
  },
];

const methodIcons: Record<UpdateMethodId, IconName> = {
  web: "server",
  platform: "refresh",
  dockerfiles: "engine",
};

const methodActions: Record<UpdateMethodId, string> = {
  web: "Deploy web update",
  platform: "Deploy full update",
  dockerfiles: "Refresh engine definitions",
};

const data = ref<DeploymentsPayload | null>(null);
const refName = ref("");
const selectedMethodId = ref<UpdateMethodId>("web");
const loading = ref(true);
const submitting = ref(false);
const error = ref("");
const message = ref("");
const reconnecting = ref(false);
const connectionRestored = ref(false);
const { confirm } = useConfirm();
let timer: number | undefined;
let restoredTimer: number | undefined;

const methods = computed(() => data.value?.methods?.length ? data.value.methods : fallbackMethods);
const selectedMethod = computed(() =>
  methods.value.find((method) => method.id === selectedMethodId.value) ?? methods.value[0],
);
const activeJob = computed(() =>
  data.value?.jobs.find((job) => !["succeeded", "failed"].includes(job.status)) ?? null,
);
const activeDockerfilePull = computed(() => {
  const job = data.value?.dockerfile_pull;
  return job && !["succeeded", "failed"].includes(job.status) ? job : null;
});
const activeOperation = computed(() => activeJob.value || activeDockerfilePull.value);
const updateBusy = computed(() => Boolean(activeOperation.value));
const updaterOnline = computed(() => {
  const lastSeen = data.value?.updater?.last_seen;
  if (!lastSeen) return false;
  const timestamp = Date.parse(lastSeen);
  return Number.isFinite(timestamp) && Date.now() - timestamp < 30_000;
});
const updaterDifferentRelease = computed(() => Boolean(
  data.value?.updater && data.value.updater.app_version !== data.value.current_version,
));

function shortVersion(value: string | null): string {
  if (!value) return "—";
  return /^[0-9a-f]{40}$/.test(value) ? value.slice(0, 12) : value;
}

function restartInterruption(cause: unknown): boolean {
  return cause instanceof ApiError && (cause.status === 0 || cause.status >= 500);
}

function markConnectionRestored(): void {
  connectionRestored.value = true;
  if (restoredTimer !== undefined) window.clearTimeout(restoredTimer);
  restoredTimer = window.setTimeout(() => {
    connectionRestored.value = false;
    restoredTimer = undefined;
  }, 5000);
}

function deploymentMethod(job: DeploymentJob): UpdateMethodId {
  return job.scope === "web" ? "web" : "platform";
}

function deploymentLabel(job: DeploymentJob): string {
  return job.scope === "web" ? "Web application" : "Full platform";
}

function completedTargets(job: DeploymentJob): number {
  return job.targets.filter((target) => ["succeeded", "deferred"].includes(target.status)).length;
}

function targetKind(target: DeploymentTarget): string {
  if (target.target_kind === "server") return target.label;
  return target.target_kind === "benchmarker" ? "Benchmarker" : "Worker";
}

async function load(silent = false): Promise<void> {
  if (!silent) loading.value = true;
  try {
    const payload = await api.get<DeploymentsPayload>("/api/admin/deployments");
    const recovered = reconnecting.value;
    data.value = payload;
    if (!refName.value) refName.value = payload.default_ref;
    error.value = "";
    reconnecting.value = false;
    if (recovered) markConnectionRestored();
  } catch (cause) {
    if (data.value && activeJob.value && restartInterruption(cause)) {
      reconnecting.value = true;
      connectionRestored.value = false;
      error.value = "";
    } else {
      error.value = errorText(cause);
    }
  } finally {
    loading.value = false;
  }
}

async function queueUpdate(): Promise<void> {
  const method = selectedMethod.value;
  if (!method) return;
  const target = refName.value.trim() || data.value?.default_ref || "main";
  const confirmation = {
    web: {
      title: "Deploy the web application?",
      message: `Build ${target} and restart only the website and API. Active games and worker connections will continue uninterrupted. Releases requiring a database migration will be rejected.`,
    },
    platform: {
      title: "Deploy the full platform?",
      message: `Build ${target}, migrate the database, and update every platform service and client. Active games and benchmarks will finish before their clients restart.`,
    },
    dockerfiles: {
      title: "Refresh engine definitions?",
      message: `Replace the engine Dockerfiles with data/engines from ${target}. Running services and games will continue.`,
    },
  }[method.id];
  const accepted = await confirm({
    ...confirmation,
    confirmLabel: methodActions[method.id],
  });
  if (!accepted) return;
  submitting.value = true;
  error.value = "";
  message.value = "";
  try {
    const response = await api.post<{ id: number; message: string }>("/api/admin/updates", {
      body: { method: method.id, ref: target },
    });
    message.value = response.message;
    await load(true);
  } catch (cause) {
    error.value = errorText(cause);
  } finally {
    submitting.value = false;
  }
}

onMounted(async () => {
  await load();
  timer = window.setInterval(() => load(true), 2000);
});

onBeforeUnmount(() => {
  if (timer !== undefined) window.clearInterval(timer);
  if (restoredTimer !== undefined) window.clearTimeout(restoredTimer);
});
</script>

<template>
  <div class="admin-page deployments-page">
    <AdminPageHeader title="Updates" />

    <section v-if="reconnecting" class="connection-notice connection-notice--reconnecting" role="status" aria-live="polite">
      <span class="connection-spinner" aria-hidden="true" />
      <span><strong>Reconnecting</strong><small>The web service is restarting.</small></span>
    </section>
    <section v-else-if="connectionRestored" class="connection-notice connection-notice--restored" role="status">
      <AppIcon name="check-circle" :size="18" />
      <span><strong>Connected</strong><small>Update status is current.</small></span>
    </section>

    <InlineFeedback :message="error" />
    <InlineFeedback :message="message" tone="info" />

    <section v-if="loading" class="panel loading-panel" role="status">Loading updates…</section>
    <template v-else-if="data">
      <section class="release-overview">
        <article class="release-primary panel">
          <span class="release-icon"><AppIcon name="tag" :size="20" /></span>
          <div>
            <span class="eyebrow">Current release</span>
            <strong>{{ shortVersion(data.current_version) }}</strong>
          </div>
          <StatusBadge status="active" label="Live" />
        </article>
        <article class="coordinator-card panel">
          <div class="coordinator-heading">
            <span class="release-icon release-icon--muted"><AppIcon name="activity" :size="19" /></span>
            <div>
              <span class="eyebrow">Update coordinator</span>
              <strong>{{ updaterOnline ? "Online" : "Unavailable" }}</strong>
            </div>
            <StatusBadge :status="updaterOnline ? 'connected' : 'offline'" />
          </div>
          <dl>
            <div><dt>Release</dt><dd><code>{{ shortVersion(data.updater?.app_version ?? null) }}</code></dd></div>
            <div><dt>Last heartbeat</dt><dd>{{ data.updater ? formatDate(data.updater.last_seen) : "Not reported" }}</dd></div>
          </dl>
          <p v-if="updaterDifferentRelease" class="coordinator-note">Updater release: <code>{{ shortVersion(data.updater?.app_version ?? null) }}</code></p>
        </article>
      </section>

      <section class="panel update-composer">
        <header class="section-heading">
          <div>
            <h2>Start an update</h2>
          </div>
          <span v-if="activeOperation" class="busy-lock"><AppIcon name="clock" :size="15" /> Another update is active</span>
        </header>

        <div class="method-grid" role="radiogroup" aria-label="Update method">
          <button
            v-for="method in methods"
            :key="method.id"
            type="button"
            class="method-card"
            :class="{ 'method-card--selected': selectedMethodId === method.id }"
            role="radio"
            :aria-checked="selectedMethodId === method.id"
            @click="selectedMethodId = method.id"
          >
            <span class="method-card__icon"><AppIcon :name="methodIcons[method.id]" :size="21" /></span>
            <span class="method-card__copy">
              <strong>{{ method.label }}</strong>
            </span>
            <span class="method-card__check"><AppIcon name="check" :size="14" /></span>
          </button>
        </div>

        <form class="update-config" @submit.prevent="queueUpdate">
          <BaseInput
            v-model="refName"
            label="Git branch, tag, or commit"
            :placeholder="data.default_ref"
            autocomplete="off"
            spellcheck="false"
            :disabled="updateBusy"
          />
          <BaseButton
            type="submit"
            variant="primary"
            size="large"
            :loading="submitting"
            :disabled="updateBusy"
          >
            <template #icon><AppIcon :name="methodIcons[selectedMethodId]" :size="18" /></template>
            {{ methodActions[selectedMethodId] }}
          </BaseButton>
        </form>
      </section>

      <section v-if="activeJob" class="panel active-rollout">
        <div class="active-rollout__pulse"><span /></div>
        <div class="active-rollout__main">
          <span class="eyebrow">Update in progress</span>
          <h2>{{ deploymentLabel(activeJob) }} <small>#{{ activeJob.id }}</small></h2>
          <p>{{ activeJob.requested_ref }} is currently {{ humanize(activeJob.status).toLowerCase() }}.</p>
        </div>
        <div class="active-rollout__progress">
          <strong>{{ completedTargets(activeJob) }} / {{ activeJob.targets.length }}</strong>
          <small>targets complete</small>
        </div>
        <StatusBadge :status="activeJob.status" />
      </section>

      <section v-else-if="activeDockerfilePull" class="panel active-rollout">
        <div class="active-rollout__pulse"><span /></div>
        <div class="active-rollout__main">
          <span class="eyebrow">Update in progress</span>
          <h2>Engine definitions <small>#{{ activeDockerfilePull.id }}</small></h2>
          <p>{{ activeDockerfilePull.requested_ref }} is currently {{ humanize(activeDockerfilePull.status).toLowerCase() }}.</p>
        </div>
        <StatusBadge :status="activeDockerfilePull.status" />
      </section>

      <section class="history-section">
        <header class="section-heading history-heading">
          <div>
            <span class="eyebrow">Release activity</span>
            <h2>Recent updates</h2>
          </div>
          <span>{{ data.jobs.length }} deployment {{ data.jobs.length === 1 ? "update" : "updates" }}</span>
        </header>

        <div v-if="data.jobs.length || data.dockerfile_pull" class="history-list">
          <article v-if="data.dockerfile_pull" class="panel history-card">
            <span class="history-card__icon"><AppIcon name="engine" :size="18" /></span>
            <div class="history-card__identity">
              <span>Engine definitions</span>
              <strong>{{ data.dockerfile_pull.requested_ref }}</strong>
            </div>
            <div class="history-card__meta">
              <span>#{{ data.dockerfile_pull.id }} · {{ formatDate(data.dockerfile_pull.requested_at) }}</span>
              <small v-if="data.dockerfile_pull.status === 'succeeded'">{{ data.dockerfile_pull.files_updated }} files refreshed</small>
              <small v-else-if="data.dockerfile_pull.error">{{ data.dockerfile_pull.error }}</small>
              <small v-else>{{ shortVersion(data.dockerfile_pull.target_commit) }}</small>
            </div>
            <StatusBadge :status="data.dockerfile_pull.status" />
          </article>

          <article v-for="job in data.jobs" :key="job.id" class="panel history-card history-card--deployment">
            <span class="history-card__icon"><AppIcon :name="methodIcons[deploymentMethod(job)]" :size="18" /></span>
            <div class="history-card__identity">
              <span>{{ deploymentLabel(job) }}</span>
              <strong>{{ job.requested_ref }}</strong>
            </div>
            <div class="history-card__meta">
              <span>#{{ job.id }} · {{ formatDate(job.requested_at) }}</span>
              <small>{{ shortVersion(job.target_commit) }} · {{ completedTargets(job) }}/{{ job.targets.length }} targets</small>
            </div>
            <StatusBadge :status="job.status" />
            <div v-if="job.error" class="history-card__error"><AppIcon name="alert-circle" :size="15" />{{ job.error }}</div>
            <div v-if="job.targets.length > 1 || job.targets.some((target) => target.detail)" class="target-list">
              <div v-for="target in job.targets" :key="target.id" class="target-row">
                <span>{{ targetKind(target) }}</span>
                <span><strong>{{ target.label }}</strong><small v-if="target.detail">{{ target.detail }}</small></span>
                <code>{{ shortVersion(target.current_commit) }}</code>
                <StatusBadge :status="target.status" />
              </div>
            </div>
          </article>
        </div>
        <AdminEmptyState v-else title="No updates yet" />
      </section>
    </template>
  </div>
</template>

<style scoped>
.deployments-page { display: grid; gap: 1rem; }
.loading-panel { color: var(--color-text-muted); min-height: 12rem; padding: 2rem; }
.eyebrow { color: var(--color-text-muted); font-size: .64rem; font-weight: 750; letter-spacing: .09em; text-transform: uppercase; }
.connection-notice { align-items: center; border: 1px solid; border-radius: var(--radius-lg); display: flex; gap: .75rem; padding: .8rem 1rem; }
.connection-notice > span:last-child { display: grid; gap: .12rem; }
.connection-notice strong { font-size: .82rem; }
.connection-notice small { color: var(--color-text-muted); font-size: .72rem; }
.connection-notice--reconnecting { background: var(--color-info-soft); border-color: color-mix(in srgb, var(--color-info) 30%, transparent); color: var(--color-info); }
.connection-notice--restored { background: var(--color-success-soft); border-color: color-mix(in srgb, var(--color-success) 30%, transparent); color: var(--color-success); }
.connection-spinner { animation: connection-spin .8s linear infinite; border: 2px solid color-mix(in srgb, currentColor 22%, transparent); border-radius: 50%; border-top-color: currentColor; height: 1rem; width: 1rem; }
@keyframes connection-spin { to { transform: rotate(360deg); } }
.release-overview { display: grid; gap: 1rem; grid-template-columns: minmax(0, 1.15fr) minmax(20rem, .85fr); }
.release-primary { align-items: center; display: grid; gap: 1rem; grid-template-columns: auto minmax(0, 1fr) auto; min-height: 6.25rem; overflow: hidden; padding: 1rem; position: relative; }
.release-primary > * { position: relative; z-index: 1; }
.release-primary > div { display: grid; gap: .22rem; }
.release-primary strong { font-family: var(--font-mono); font-size: clamp(1.15rem, 2vw, 1.55rem); }
.release-primary small { color: var(--color-text-muted); font-size: .72rem; }
.release-icon { align-items: center; background: var(--color-accent-soft); border-radius: .8rem; color: var(--color-accent); display: inline-flex; height: 2.75rem; justify-content: center; width: 2.75rem; }
.release-icon--muted { background: var(--color-surface-sunken); color: var(--color-text-secondary); height: 2.5rem; width: 2.5rem; }
.coordinator-card { display: grid; gap: .85rem; padding: 1rem; }
.coordinator-heading { align-items: center; display: grid; gap: .75rem; grid-template-columns: auto minmax(0, 1fr) auto; }
.coordinator-heading > div { display: grid; gap: .12rem; }
.coordinator-heading strong { font-size: .9rem; }
.coordinator-card dl { display: grid; gap: .5rem; grid-template-columns: 1fr 1fr; margin: 0; }
.coordinator-card dl > div { background: var(--color-surface-sunken); border-radius: var(--radius-md); display: grid; gap: .2rem; padding: .55rem .65rem; }
.coordinator-card dt { color: var(--color-text-muted); font-size: .62rem; text-transform: uppercase; }
.coordinator-card dd { font-size: .7rem; margin: 0; }
.coordinator-note { align-items: center; color: var(--color-text-muted); display: flex; font-size: .68rem; gap: .4rem; margin: 0; }
.update-composer { display: grid; gap: 1rem; padding: 1rem; }
.section-heading { align-items: flex-start; display: flex; gap: 1rem; justify-content: space-between; }
.section-heading > div { display: grid; gap: .25rem; }
.section-heading h2 { font-size: 1rem; margin: 0; }
.section-heading p { color: var(--color-text-muted); font-size: .74rem; margin: 0; }
.busy-lock { align-items: center; background: var(--color-warning-soft); border-radius: 999px; color: var(--color-warning); display: flex; font-size: .68rem; font-weight: 700; gap: .35rem; padding: .4rem .6rem; }
.method-grid { display: grid; gap: .75rem; grid-template-columns: repeat(3, minmax(0, 1fr)); }
.method-card { align-items: center; background: var(--color-surface-sunken); border: 1px solid transparent; border-radius: var(--radius-md); color: var(--color-text); cursor: pointer; display: grid; gap: .7rem; grid-template-columns: auto minmax(0, 1fr) auto; min-height: 4.25rem; padding: .75rem; position: relative; text-align: left; transition: border-color var(--transition-fast), box-shadow var(--transition-fast), transform var(--transition-fast); }
.method-card:hover { border-color: var(--color-border-strong); transform: translateY(-1px); }
.method-card--selected { background: color-mix(in srgb, var(--color-accent-soft) 58%, var(--color-surface)); border-color: var(--color-accent); box-shadow: 0 0 0 1px color-mix(in srgb, var(--color-accent) 15%, transparent); }
.method-card__icon { align-items: center; background: var(--color-surface-raised); border: 1px solid var(--color-border); border-radius: .55rem; color: var(--color-text-secondary); display: flex; height: 2.25rem; justify-content: center; width: 2.25rem; }
.method-card--selected .method-card__icon { background: var(--color-accent); border-color: var(--color-accent); color: var(--color-on-accent); }
.method-card__copy { display: grid; }
.method-card__copy strong { font-size: .82rem; }
.method-card__copy small { color: var(--color-text-muted); font-size: .69rem; line-height: 1.45; }
.method-card__check { align-items: center; background: var(--color-accent); border-radius: 50%; color: var(--color-on-accent); display: flex; height: 1.4rem; justify-content: center; opacity: 0; transform: scale(.75); transition: opacity var(--transition-fast), transform var(--transition-fast); width: 1.4rem; }
.method-card--selected .method-card__check { opacity: 1; transform: scale(1); }
.update-config { align-items: end; border-top: 1px solid var(--color-border); display: grid; gap: 1rem; grid-template-columns: minmax(14rem, 1fr) auto; padding-top: 1.1rem; }
.active-rollout { align-items: center; border-color: color-mix(in srgb, var(--color-accent) 30%, var(--color-border)); display: grid; gap: 1rem; grid-template-columns: auto minmax(0, 1fr) auto auto; padding: 1rem 1.1rem; }
.active-rollout__pulse { align-items: center; background: var(--color-accent-soft); border-radius: 50%; display: flex; height: 2.6rem; justify-content: center; width: 2.6rem; }
.active-rollout__pulse span { animation: status-pulse 1.5s ease-in-out infinite; background: var(--color-accent); border-radius: 50%; height: .65rem; width: .65rem; }
@keyframes status-pulse { 50% { box-shadow: 0 0 0 .45rem color-mix(in srgb, var(--color-accent) 12%, transparent); transform: scale(.86); } }
.active-rollout__main { display: grid; gap: .18rem; }
.active-rollout__main h2 { font-size: .88rem; margin: 0; }
.active-rollout__main h2 small { color: var(--color-text-muted); font-size: .68rem; font-weight: 500; }
.active-rollout__main p { color: var(--color-text-muted); font-size: .7rem; margin: 0; }
.active-rollout__progress { display: grid; text-align: right; }
.active-rollout__progress strong { font-size: .85rem; }
.active-rollout__progress small { color: var(--color-text-muted); font-size: .62rem; }
.history-section { display: grid; gap: .75rem; padding-top: .35rem; }
.history-heading { align-items: end; padding: 0 .1rem; }
.history-heading > span { color: var(--color-text-muted); font-size: .67rem; }
.history-list { display: grid; gap: .6rem; }
.history-card { align-items: center; display: grid; gap: .85rem; grid-template-columns: auto minmax(10rem, .7fr) minmax(12rem, 1fr) auto; overflow: hidden; padding: .8rem 1rem; }
.history-card__icon { align-items: center; background: var(--color-surface-sunken); border-radius: .6rem; color: var(--color-text-secondary); display: flex; height: 2.25rem; justify-content: center; width: 2.25rem; }
.history-card__identity, .history-card__meta { display: grid; gap: .15rem; min-width: 0; }
.history-card__identity span { color: var(--color-text-muted); font-size: .62rem; text-transform: uppercase; }
.history-card__identity strong { font-size: .76rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.history-card__meta span, .history-card__meta small { color: var(--color-text-muted); font-size: .66rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.history-card__error { align-items: center; background: var(--color-danger-soft); border-radius: var(--radius-sm); color: var(--color-danger); display: flex; font-size: .68rem; gap: .4rem; grid-column: 2 / -1; padding: .5rem .6rem; }
.target-list { border-top: 1px solid var(--color-border); display: grid; grid-column: 1 / -1; margin: .1rem -1rem -.8rem; }
.target-row { align-items: center; border-bottom: 1px solid var(--color-border); display: grid; gap: .75rem; grid-template-columns: 7rem minmax(0, 1fr) minmax(5rem, auto) auto; padding: .58rem 1rem; }
.target-row:last-child { border-bottom: 0; }
.target-row > span:first-child { color: var(--color-text-muted); font-size: .62rem; text-transform: uppercase; }
.target-row > span:nth-child(2) { display: grid; gap: .1rem; }
.target-row strong { font-size: .7rem; }
.target-row small { color: var(--color-text-muted); font-size: .62rem; }
.target-row code { font-size: .65rem; }
@media (max-width: 960px) {
  .release-overview { grid-template-columns: 1fr; }
  .method-grid { grid-template-columns: 1fr; }
  .method-card { min-height: auto; }
  .update-config { align-items: stretch; grid-template-columns: 1fr; }
}
@media (max-width: 680px) {
  .release-primary { grid-template-columns: auto minmax(0, 1fr); }
  .release-primary > .status-badge { grid-column: 2; justify-self: start; }
  .coordinator-card dl { grid-template-columns: 1fr; }
  .active-rollout { grid-template-columns: auto minmax(0, 1fr); }
  .active-rollout__progress, .active-rollout > .status-badge { grid-column: 2; justify-self: start; text-align: left; }
  .history-card { grid-template-columns: auto minmax(0, 1fr) auto; }
  .history-card__meta { grid-column: 2 / -1; }
  .target-row { grid-template-columns: 1fr auto; }
  .target-row > span:first-child, .target-row code { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  .active-rollout__pulse span, .connection-spinner { animation: none; }
}
</style>
