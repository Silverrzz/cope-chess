<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import { api } from "@/api/client";
import AdminEmptyState from "@/components/admin/AdminEmptyState.vue";
import AdminPageHeader from "@/components/admin/AdminPageHeader.vue";
import InlineFeedback from "@/components/admin/InlineFeedback.vue";
import StatusBadge from "@/components/admin/StatusBadge.vue";
import { errorText, formatDate, humanize } from "@/components/admin/format";
import BaseButton from "@/components/ui/BaseButton.vue";
import BaseInput from "@/components/ui/BaseInput.vue";
import { useConfirm } from "@/composables/useConfirm";

interface DeploymentTarget {
  id: number;
  target_kind: "server" | "worker";
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
  target_commit: string | null;
  status: string;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  targets: DeploymentTarget[];
}

interface DeploymentsPayload {
  current_version: string;
  default_ref: string;
  updater: { service: string; app_version: string; last_seen: string } | null;
  jobs: DeploymentJob[];
}

const data = ref<DeploymentsPayload | null>(null);
const refName = ref("");
const loading = ref(true);
const submitting = ref(false);
const error = ref("");
const message = ref("");
const { confirm } = useConfirm();
let timer: number | undefined;

const activeJob = computed(() =>
  data.value?.jobs.find((job) => !["succeeded", "failed"].includes(job.status)) ?? null,
);
const updaterOnline = computed(() => {
  const lastSeen = data.value?.updater?.last_seen;
  if (!lastSeen) return false;
  const timestamp = Date.parse(lastSeen);
  return Number.isFinite(timestamp) && Date.now() - timestamp < 30_000;
});

function shortVersion(value: string | null): string {
  if (!value) return "—";
  return /^[0-9a-f]{40}$/.test(value) ? value.slice(0, 12) : value;
}

async function load(silent = false): Promise<void> {
  if (!silent) loading.value = true;
  try {
    const payload = await api.get<DeploymentsPayload>("/api/admin/deployments");
    data.value = payload;
    if (!refName.value) refName.value = payload.default_ref;
    error.value = "";
  } catch (cause) {
    error.value = errorText(cause);
  } finally {
    loading.value = false;
  }
}

async function deploy(): Promise<void> {
  const target = refName.value.trim() || data.value?.default_ref || "main";
  const accepted = await confirm({
    title: "Update and rebuild the platform?",
    message: `Deploy ${target} to the server and every registered worker. Active games will finish before their worker restarts.`,
    confirmLabel: "Update & rebuild",
  });
  if (!accepted) return;
  submitting.value = true;
  error.value = "";
  message.value = "";
  try {
    const response = await api.post<{ id: number; message: string }>("/api/admin/deployments", {
      body: { ref: target },
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
});
</script>

<template>
  <div class="admin-page deployments-page">
    <AdminPageHeader
      title="Updates"
      description="Pull, rebuild, migrate, restart, and reconcile the complete platform from one place."
    />
    <InlineFeedback :message="error" />
    <InlineFeedback :message="message" tone="info" />

    <section v-if="loading" class="panel loading-panel" role="status">Loading deployment state…</section>
    <template v-else-if="data">
      <section class="panel update-panel">
        <div class="version-grid">
          <div><span>Running release</span><code>{{ shortVersion(data.current_version) }}</code></div>
          <div><span>Updater</span><StatusBadge :status="updaterOnline ? 'connected' : 'offline'" /></div>
          <div><span>Last heartbeat</span><strong>{{ data.updater ? formatDate(data.updater.last_seen) : "Unavailable" }}</strong></div>
        </div>
        <form class="update-form" @submit.prevent="deploy">
          <BaseInput
            v-model="refName"
            label="Git branch, tag, or commit"
            :placeholder="data.default_ref"
            autocomplete="off"
            spellcheck="false"
            :disabled="Boolean(activeJob)"
          />
          <BaseButton
            type="submit"
            variant="primary"
            :loading="submitting"
            :disabled="Boolean(activeJob) || !updaterOnline"
          >
            Update & rebuild
          </BaseButton>
        </form>
        <p v-if="activeJob" class="active-note">
          Deployment #{{ activeJob.id }} is {{ humanize(activeJob.status).toLowerCase() }}. The control panel may reconnect while the web service restarts.
        </p>
      </section>

      <section v-if="data.jobs.length" class="deployment-list">
        <article v-for="job in data.jobs" :key="job.id" class="panel deployment-card">
          <header>
            <div>
              <span class="eyebrow">Deployment #{{ job.id }}</span>
              <h2>{{ job.requested_ref }}</h2>
              <p>{{ formatDate(job.requested_at) }} · {{ shortVersion(job.target_commit) }}</p>
            </div>
            <StatusBadge :status="job.status" />
          </header>
          <InlineFeedback v-if="job.error" :message="job.error" />
          <div class="target-list">
            <div v-for="target in job.targets" :key="target.id" class="target-row">
              <span class="target-kind">{{ target.target_kind === "server" ? "Platform" : "Worker" }}</span>
              <span class="target-name">
                <strong>{{ target.label }}</strong>
                <small v-if="target.detail">{{ target.detail }}</small>
              </span>
              <code>{{ shortVersion(target.current_commit) }}</code>
              <StatusBadge :status="target.status" />
            </div>
          </div>
        </article>
      </section>
      <AdminEmptyState v-else title="No deployments yet" description="The first update will appear here." />
    </template>
  </div>
</template>

<style scoped>
.deployments-page { display: grid; gap: 1rem; }
.loading-panel { color: var(--color-text-muted); min-height: 10rem; padding: 2rem; }
.update-panel { display: grid; gap: 1rem; padding: 1rem; }
.version-grid { display: grid; gap: .8rem; grid-template-columns: repeat(3, minmax(0, 1fr)); }
.version-grid > div { display: grid; gap: .25rem; }
.version-grid span { color: var(--color-text-muted); font-size: .68rem; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }
.version-grid code, .version-grid strong { font-size: .82rem; }
.update-form { align-items: end; display: grid; gap: .75rem; grid-template-columns: minmax(0, 1fr) auto; }
.active-note { color: var(--color-text-muted); font-size: .75rem; margin: 0; }
.deployment-list { display: grid; gap: .9rem; }
.deployment-card { overflow: hidden; padding: 0; }
.deployment-card > header { align-items: flex-start; border-bottom: 1px solid var(--color-border); display: flex; gap: 1rem; justify-content: space-between; padding: .9rem 1rem; }
.deployment-card h2 { font-size: .95rem; margin: .15rem 0; }
.deployment-card header p { color: var(--color-text-muted); font-size: .7rem; margin: 0; }
.deployment-card > .inline-feedback { margin: .8rem; }
.eyebrow { color: var(--color-text-muted); font-size: .62rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }
.target-list { display: grid; }
.target-row { align-items: center; border-bottom: 1px solid var(--color-border); display: grid; gap: .75rem; grid-template-columns: 5rem minmax(0, 1fr) minmax(5rem, auto) auto; padding: .7rem 1rem; }
.target-row:last-child { border-bottom: 0; }
.target-kind { color: var(--color-text-muted); font-size: .68rem; text-transform: uppercase; }
.target-name { display: grid; min-width: 0; }
.target-name strong { font-size: .78rem; }
.target-name small { color: var(--color-text-muted); font-size: .68rem; margin-top: .15rem; }
.target-row code { font-size: .7rem; }
@media (max-width: 48rem) {
  .version-grid { grid-template-columns: 1fr; }
  .target-row { grid-template-columns: minmax(0, 1fr) auto; }
  .target-kind, .target-row code { display: none; }
}
@media (max-width: 32rem) {
  .update-form { grid-template-columns: 1fr; }
}
</style>
