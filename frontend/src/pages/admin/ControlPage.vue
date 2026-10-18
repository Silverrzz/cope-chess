<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"
import { api } from "@/api/client"
import VariantPicker from "@/components/admin/VariantPicker.vue"
import AdminPageHeader from "@/components/admin/AdminPageHeader.vue"
import { errorText } from "@/components/admin/format"

interface Choice { id: number; name: string; variant?: string }
interface Submission { id: number; name: string; version: string; submitted_at: string | null }
interface TestingPlan { id: number; engine_name: string; engine_version: string; preset_key: string; status: string; reason: string; tournament_id: number | null; tournament_status: string | null }
interface Preset { key: string; name: string; revision: number; category: string; variant: string }
interface ControlContext {
  actor: string
  scopes: string[]
  queue: { revision: number; acknowledged: Submission[]; waiting_for_test: TestingPlan[] }
  presets: Preset[]
  git_hosts: Choice[]
  rating_lists: Choice[]
  opening_suites: Choice[]
}
interface OperationSummary { id: string; title: string; status: string; error: string; created_at: string }
interface Operation extends OperationSummary {
  steps: { key: string; action: string; status: string; attempts: number; error: string; result: unknown }[]
  events: { id: number; kind: string; detail: unknown; created_at: string }[]
}
interface DraftEngine {
  queue_id: number
  selected: boolean
  git_host_id: number
  repository_full_name: string
  source_ref: string
  recipe: { path: string; content: string }
  dfrc: string
  variant_capabilities: Record<string, string>
  capability_evidence: string
  top20: string
  strength_evidence: string
}
interface Token { id: string; label: string; scopes: string[]; expires_at: string; revoked_at: string | null }

const context = ref<ControlContext | null>(null)
const operations = ref<OperationSummary[]>([])
const selected = ref<Operation | null>(null)
const drafts = ref<DraftEngine[]>([])
const tokens = ref<Token[]>([])
const error = ref("")
const busy = ref(false)
const repoBase = ref("")
const repoBranch = ref("")
const presetKeys = ref<string[]>([])
const referenceList = ref<number | null>(null)
const requestKey = ref(crypto.randomUUID())
const tokenLabel = ref("")
const newToken = ref("")
const origin = window.location.origin
const preset = ref({ key: "", name: "", rating_list_id: 0, category: "bullet", variant: "standard", seconds: 60, increment: 1, threads: 1, hash: 64, opponents: 9, cycles: 1, concurrency: 8, opening_suite_id: 0 })
watch(() => preset.value.variant, () => { preset.value.rating_list_id = 0; preset.value.opening_suite_id = 0 })
const owner = computed(() => context.value?.scopes.includes("*") ?? false)
let timer: number | undefined
let refreshing = false
let active = true

function pretty(value: unknown): string { return JSON.stringify(value, null, 2) }

async function refresh(): Promise<void> {
  if (refreshing) return
  refreshing = true
  try {
    const [nextContext, nextOperations] = await Promise.all([
      api.get<ControlContext>("/api/admin/control/context"),
      api.get<OperationSummary[]>("/api/admin/control/operations"),
    ])
    if (!active) return
    context.value = nextContext
    operations.value = nextOperations
    if (!drafts.value.length) drafts.value = nextContext.queue.acknowledged.map((item) => ({
      queue_id: item.id, selected: false, git_host_id: nextContext.git_hosts[0]?.id ?? 0,
      repository_full_name: "", source_ref: "", recipe: { path: `${item.name}-${item.version}`.toLowerCase().replace(/[^a-z0-9._-]/g, "-") + ".Dockerfile", content: "" },
      dfrc: "unknown", variant_capabilities: { duck: "unknown", duck960: "unknown" }, capability_evidence: "", top20: "unknown", strength_evidence: "",
    }))
    if (selected.value) selected.value = await api.get<Operation>(`/api/admin/control/operations/${selected.value.id}`)
  } catch (cause) { if (active) error.value = errorText(cause) }
  finally { refreshing = false }
}

async function act(work: () => Promise<void>): Promise<void> {
  busy.value = true
  error.value = ""
  try { await work() } catch (cause) { error.value = errorText(cause) }
  finally { busy.value = false }
}

async function loadRepository(): Promise<void> {
  await act(async () => {
    const result = await api.get<{ commit: string; branch: string }>("/api/admin/control/repository")
    repoBase.value = result.commit
    repoBranch.value = result.branch
  })
}

async function submit(preview: boolean): Promise<void> {
  await act(async () => {
    const engines = drafts.value.filter((item) => item.selected).map(({ selected: _selected, top20, ...item }) => ({
      ...item, top20: top20 === "unknown" ? null : top20 === "yes",
    }))
    selected.value = await api.post<Operation>("/api/admin/control/intake", { body: {
      idempotency_key: requestKey.value, preview,
      intake: { title: "Engine intake", queue_revision: context.value?.queue.revision, repository_base_commit: repoBase.value,
        engines, preset_keys: presetKeys.value, top20_rating_list_id: referenceList.value },
    } })
    requestKey.value = crypto.randomUUID()
    await refresh()
  })
}

async function selectOperation(id: string): Promise<void> {
  await act(async () => { selected.value = await api.get<Operation>(`/api/admin/control/operations/${id}`) })
}

async function command(value: string): Promise<void> {
  if (!selected.value) return
  const id = selected.value.id
  await act(async () => {
    selected.value = await api.post<Operation>(`/api/admin/control/operations/${id}/commands`, { body: { command: value } })
    await refresh()
  })
}

async function savePreset(): Promise<void> {
  await act(async () => {
    const value = preset.value
    await api.post("/api/admin/control/presets", { body: {
      key: value.key, name: value.name, rating_list_id: value.rating_list_id, category: value.category, variant: value.variant, opponent_count: value.opponents,
      config: { format: "gauntlet", format_options: { hero_engine_id: 1, cycles: value.cycles }, participants: [1, 2],
        variant: value.variant, time_control: { category: "increment", initial_ms: Math.round(value.seconds * 1000), increment_ms: Math.round(value.increment * 1000) },
        engine_threads: value.category === "classical" ? 8 : value.threads, engine_hash_mb: value.hash, concurrency: value.concurrency,
        opening_suite_id: value.opening_suite_id || null, adjudication: {}, rated: true },
    } })
    await refresh()
  })
}

async function loadTokens(): Promise<void> {
  tokens.value = await api.get<Token[]>("/api/admin/control/tokens")
}

async function mintToken(): Promise<void> {
  await act(async () => {
    const result = await api.post<{ token: string }>("/api/admin/control/tokens", { body: { label: tokenLabel.value, scopes: ["*"], ttl_days: 30 } })
    newToken.value = result.token
    tokenLabel.value = ""
    await loadTokens()
  })
}

async function revokeToken(id: string): Promise<void> {
  await act(async () => { await api.delete(`/api/admin/control/tokens/${id}`); await loadTokens() })
}

onMounted(async () => {
  await refresh()
  if (owner.value) await act(loadTokens)
  if (active) timer = window.setInterval(() => { void refresh() }, 5000)
})
onBeforeUnmount(() => { active = false; window.clearInterval(timer) })
</script>

<template>
  <div class="admin-page page-stack control-page">
    <AdminPageHeader title="Cope Control" description="Prepare engine submissions and follow durable operations across the platform.">
      <template #actions><button class="button button--ghost" :disabled="busy" @click="refresh">Refresh</button></template>
    </AdminPageHeader>
    <p v-if="error" role="alert" class="control-error">{{ error }}</p>

    <section class="control-panel">
      <h2>Operations</h2>
      <p v-if="!operations.length">No operations yet.</p>
      <div v-else class="control-scroll"><table>
        <thead><tr><th>Operation</th><th>Status</th><th>Created</th></tr></thead>
        <tbody><tr v-for="operation in operations" :key="operation.id">
          <td><button class="control-link" @click="selectOperation(operation.id)">{{ operation.title }}</button></td>
          <td>{{ operation.status }}</td><td>{{ new Date(operation.created_at).toLocaleString() }}</td>
        </tr></tbody>
      </table></div>
      <div v-if="selected" class="page-stack">
        <h3>{{ selected.title }} · {{ selected.status }}</h3>
        <p v-if="selected.error" class="control-error">{{ selected.error }}</p>
        <div v-if="owner" class="control-actions">
          <button v-if="selected.status === 'planned'" class="button button--primary" :disabled="busy" @click="command('execute')">Execute plan</button>
          <button v-if="['failed', 'waiting'].includes(selected.status)" class="button button--primary" :disabled="busy" @click="command('resume')">Resume</button>
          <button v-if="!['succeeded', 'cancelled'].includes(selected.status)" class="button button--ghost" :disabled="busy" @click="command('cancel')">Cancel future work</button>
        </div>
        <ol class="control-steps"><li v-for="step in selected.steps" :key="step.key">
          <strong>{{ step.key }}</strong> · {{ step.status }} · {{ step.attempts }} attempts
          <p v-if="step.error" class="control-error">{{ step.error }}</p>
          <details v-if="step.result"><summary>Results</summary><pre>{{ pretty(step.result) }}</pre></details>
        </li></ol>
        <details><summary>Audit events</summary><ol><li v-for="event in selected.events" :key="event.id">{{ event.kind }} · {{ new Date(event.created_at).toLocaleString() }}<pre>{{ pretty(event.detail) }}</pre></li></ol></details>
      </div>
    </section>

    <section class="control-panel">
      <h2>Testing plans</h2>
      <p>Standard tournaments run before DFRC. Shorter time controls and older submissions take precedence within each group.</p>
      <div class="control-scroll"><table>
        <thead><tr><th>Engine</th><th>Preset</th><th>Status</th><th>Details</th></tr></thead>
        <tbody><tr v-for="plan in context?.queue.waiting_for_test" :key="plan.id">
          <td>{{ plan.engine_name }} {{ plan.engine_version }}</td><td>{{ plan.preset_key }}</td><td>{{ plan.tournament_status ?? plan.status }}</td>
          <td><RouterLink v-if="plan.tournament_id" :to="`/admin/tournaments/${plan.tournament_id}`">Tournament {{ plan.tournament_id }}</RouterLink><span v-else>{{ plan.reason }}</span></td>
        </tr></tbody>
      </table></div>
    </section>

    <section v-if="owner" class="control-panel page-stack">
      <h2>Engine intake</h2>
      <p>Supply prepared Dockerfiles and exact source commits. Preview saves a plan; execute publishes recipes and starts preparation. Builds and unavailable opponents remain visible as waiting work.</p>
      <div class="control-actions"><button class="button button--ghost" :disabled="busy" @click="loadRepository">Load repository revision</button><span>{{ repoBranch }} {{ repoBase }}</span></div>
      <fieldset><legend>Tournament presets</legend><label v-for="item in context?.presets" :key="item.key" class="control-check"><input v-model="presetKeys" type="checkbox" :value="item.key">{{ item.name }} · {{ item.variant }} · revision {{ item.revision }}</label></fieldset>
      <label>Top-20 reference list<select v-model="referenceList"><option :value="null">Choose when using classical</option><option v-for="list in context?.rating_lists" :key="list.id" :value="list.id">{{ list.name }}</option></select></label>
      <details v-for="draft in drafts" :key="draft.queue_id" class="control-engine">
        <summary><label class="control-check" @click.stop><input v-model="draft.selected" type="checkbox">{{ context?.queue.acknowledged.find((item) => item.id === draft.queue_id)?.name }} {{ context?.queue.acknowledged.find((item) => item.id === draft.queue_id)?.version }}</label></summary>
        <div class="control-grid">
          <label>Git host<select v-model="draft.git_host_id"><option v-for="host in context?.git_hosts" :key="host.id" :value="host.id">{{ host.name }}</option></select></label>
          <label>Repository<input v-model="draft.repository_full_name" placeholder="owner/repository"></label>
          <label>Source commit<input v-model="draft.source_ref" placeholder="Full 40-character commit"></label>
          <label>Dockerfile name<input v-model="draft.recipe.path"></label>
          <label>DFRC support<select v-model="draft.dfrc"><option value="unknown">Unknown</option><option value="supported">Supported</option><option value="unsupported">Unsupported</option></select></label>
          <label v-for="variant in ['duck', 'duck960']" :key="variant">{{ variant === 'duck' ? 'Duck' : 'Duck960' }} support<select v-model="draft.variant_capabilities[variant]"><option value="unknown">Unknown</option><option value="supported">Supported</option><option value="unsupported">Unsupported</option></select></label>
          <label>Capability evidence<input v-model="draft.capability_evidence" placeholder="Source or verified assessment"></label>
          <label>Expected top 20<select v-model="draft.top20"><option value="unknown">Await rating evidence</option><option value="yes">Yes</option><option value="no">No</option></select></label>
          <label>Strength evidence<input v-model="draft.strength_evidence"></label>
        </div>
        <label>Dockerfile<textarea v-model="draft.recipe.content" rows="12" spellcheck="false" /></label>
      </details>
      <div class="control-actions"><button class="button button--ghost" :disabled="busy || !repoBase" @click="submit(true)">Preview intake</button><button class="button button--primary" :disabled="busy || !repoBase" @click="submit(false)">Execute intake</button></div>
    </section>

    <details v-if="owner" class="control-panel">
      <summary>Create a tournament preset</summary>
      <form class="page-stack" @submit.prevent="savePreset">
        <p>Saving an existing key creates a new immutable revision.</p>
        <div class="control-grid">
          <label>Key<input v-model="preset.key" required pattern="[a-z][a-z0-9_-]*"></label><label>Name<input v-model="preset.name" required></label>
          <label>Rating list<select v-model="preset.rating_list_id" required><option :value="0" disabled>Choose list</option><option v-for="list in context?.rating_lists.filter(item => !item.variant || item.variant === preset.variant)" :key="list.id" :value="list.id">{{ list.name }}</option></select></label>
          <label>Category<select v-model="preset.category"><option value="bullet">Bullet</option><option value="rapid">Rapid</option><option value="classical">Classical · 8 threads</option></select></label>
          <label>Variant<VariantPicker v-model="preset.variant" /></label>
          <label>Opening suite<select v-model="preset.opening_suite_id"><option :value="0">Standard starting position</option><option v-for="suite in context?.opening_suites.filter(item => !item.variant || item.variant === preset.variant || item.variant === 'legacy' && !preset.variant.startsWith('duck'))" :key="suite.id" :value="suite.id">{{ suite.name }}</option></select></label>
          <label>Initial seconds<input v-model.number="preset.seconds" type="number" min="1" required></label><label>Increment seconds<input v-model.number="preset.increment" type="number" min="0" step="0.1" required></label>
          <label>Engine threads<input v-model.number="preset.threads" type="number" min="1" :disabled="preset.category === 'classical'"></label><label>Hash per engine, MB<input v-model.number="preset.hash" type="number" min="1" required></label>
          <label>Opponents<input v-model.number="preset.opponents" type="number" min="1" max="499" required></label><label>Cycles<input v-model.number="preset.cycles" type="number" min="1" required></label><label>Concurrent games<input v-model.number="preset.concurrency" type="number" min="1" required></label>
        </div>
        <button class="button button--primary" :disabled="busy">Save preset revision</button>
      </form>
    </details>

    <details v-if="owner" class="control-panel">
      <summary>Connect an assistant</summary>
      <p>Connect to <code>{{ `${origin}/mcp` }}</code> using a control bearer token. Owner tokens grant every registered action on this installation and expire after 30 days.</p>
      <form class="control-actions" @submit.prevent="mintToken"><label>Connection name<input v-model="tokenLabel" required maxlength="100" autocomplete="off"></label><button class="button button--primary" :disabled="busy">Create owner token</button></form>
      <div v-if="newToken"><p>Copy this token now; it is shown once.</p><input :value="newToken" readonly aria-label="New control token" autocomplete="off" @focus="($event.target as HTMLInputElement).select()"><button class="button button--ghost" @click="newToken = ''">Hide token</button></div>
      <ul><li v-for="token in tokens" :key="token.id">{{ token.label }} · {{ token.revoked_at ? 'Revoked' : `Expires ${new Date(token.expires_at).toLocaleString()}` }} <button v-if="!token.revoked_at" class="control-link" :disabled="busy" @click="revokeToken(token.id)">Revoke</button></li></ul>
    </details>
  </div>
</template>

<style scoped>
.control-panel { padding: 1.2rem; border: 1px solid var(--color-border); border-radius: .75rem; background: var(--color-surface); }
.control-panel h2 { margin-top: 0; font-size: 1.2rem; }
.control-panel p { color: var(--color-text-muted); }
.control-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); gap: 1rem; margin-block: 1rem; }
.control-page label { display: grid; gap: .35rem; font-size: .85rem; }
.control-page input:not([type=checkbox]), .control-page select, .control-page textarea { width: 100%; min-width: 0; padding: .55rem; color: var(--color-text); background: var(--color-surface); border: 1px solid var(--color-border); border-radius: .35rem; }
.control-page textarea, .control-page pre { font-family: ui-monospace, monospace; font-size: .8rem; }
.control-page pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 25rem; overflow: auto; }
.control-check { display: inline-flex !important; align-items: center; gap: .5rem; margin: .4rem .8rem .4rem 0; }
.control-actions { display: flex; flex-wrap: wrap; gap: .75rem; align-items: end; }
.control-scroll { overflow: auto; }
.control-page table { width: 100%; border-collapse: collapse; text-align: left; font-size: .85rem; }
.control-page th, .control-page td { padding: .65rem; border-bottom: 1px solid var(--color-border); }
.control-link { color: var(--color-primary, #6676e8); background: none; border: 0; cursor: pointer; text-align: left; }
.control-page .control-error { color: var(--color-danger, #c44); overflow-wrap: anywhere; }
.control-engine { border-top: 1px solid var(--color-border); padding-block: .7rem; }
.control-steps { display: grid; gap: .7rem; padding-left: 1.5rem; }
.control-page summary { cursor: pointer; }
</style>
