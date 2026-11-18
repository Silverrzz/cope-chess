<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import api from '@/api/client'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { useConfirm } from '@/composables/useConfirm'
import type { Witness } from '@/quorum'
import type { WitnessPolicy } from '@/quorum-history'
import { downloadQuorumFile } from '@/quorum-voter'

type ManagedWitness = Witness & { enabled: boolean }
interface Verifier extends ManagedWitness {
  status: 'awaiting_checkin' | 'online' | 'offline'
  checkin: { seen_at: string; last_scan: number | null; archived: number; scan_errors: number; runtime: string; app_commit: string | null } | null
  signed_count: number
  last_signed: string | null
}
interface Directory { revision: number; witnesses: Verifier[]; policy: WitnessPolicy | null; policy_error: string | null }

const props = defineProps<{ disabled?: boolean }>()
const emit = defineEmits<{ saved: [fromRevision: number, revision: number, witnesses: ManagedWitness[]] }>()
const directory = ref<Directory>()
const editor = ref<HTMLDialogElement>()
const editing = ref<ManagedWitness>()
const original = ref('')
const busy = ref(false)
const error = ref('')
const editorError = ref('')
const proposedKeys = ref<string[]>([])
const proposedPolicy = ref<WitnessPolicy>()
const proposing = ref(false)
const proposalError = ref('')
const selection = computed(() => [...proposedKeys.value].sort().join(','))
const policy = computed(() => directory.value?.policy)
const policyKeys = computed(() => new Set(Object.values(policy.value?.operators ?? {})))
const faults = (value: WitnessPolicy) => value.max_faulty ?? 2 * value.threshold - Object.keys(value.operators).length - 1
const noPolicyChange = computed(() => {
  const current = policy.value
  const proposed = proposedPolicy.value
  if (!current || !proposed || current.threshold !== proposed.threshold || faults(current) !== faults(proposed)) return false
  const members = Object.entries(proposed.operators)
  return members.length === policyKeys.value.size && members.every(([id, key]) => current.operators[id] === key)
})
const { confirm } = useConfirm()
let timer: ReturnType<typeof setInterval> | undefined
const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'short', timeStyle: 'medium' })
const statusLabel = { awaiting_checkin: 'Awaiting check-in', online: 'Online', offline: 'Offline' }

function fields({ name, public_key, report_origin, enabled }: ManagedWitness): ManagedWitness {
  return { name, public_key, report_origin, enabled }
}

function clearProposal() {
  proposedPolicy.value = undefined
  proposalError.value = ''
}

watch(selection, clearProposal)

function showDirectory(result: Directory, registered?: string) {
  const previous = directory.value
  const policyChanged = JSON.stringify(previous?.policy) !== JSON.stringify(result.policy)
  if (policyChanged || previous?.revision !== result.revision || previous?.policy_error !== result.policy_error) clearProposal()
  const keys = new Set(result.witnesses.map(item => item.public_key))
  if (!previous || policyChanged) {
    proposedKeys.value = result.policy
      ? Object.values(result.policy.operators).filter(key => keys.has(key))
      : result.witnesses.filter(item => item.enabled).map(item => item.public_key)
  } else {
    proposedKeys.value = proposedKeys.value.filter(key => keys.has(key))
  }
  if (registered && !proposedKeys.value.includes(registered)) proposedKeys.value.push(registered)
  directory.value = result
}

async function suggestPolicy() {
  if (!directory.value || directory.value.policy_error || !proposedKeys.value.length || proposing.value) return
  const requested = selection.value
  const revision = directory.value.revision
  const configured = JSON.stringify(policy.value)
  const stillCurrent = () => selection.value === requested && directory.value?.revision === revision
    && !directory.value.policy_error && JSON.stringify(policy.value) === configured
  proposing.value = true
  proposalError.value = ''
  try {
    const proposal = await api.post<WitnessPolicy>('/api/admin/quorum/witnesses/policy-proposal', {
      body: { public_keys: [...proposedKeys.value] },
    })
    if (stillCurrent()) proposedPolicy.value = proposal
  } catch (cause) {
    if (stillCurrent()) proposalError.value = (cause as Error).message
  } finally {
    proposing.value = false
  }
}

function downloadPolicy() {
  if (proposedPolicy.value && !noPolicyChange.value) {
    downloadQuorumFile('quorum-proposed-policy.json', JSON.stringify(proposedPolicy.value, null, 2) + '\n')
  }
}

async function load() {
  if (busy.value || editor.value?.open) return
  try {
    const result = await api.get<Directory>('/api/admin/quorum/witnesses')
    if (busy.value || editor.value?.open || result.revision < (directory.value?.revision ?? 0)) return
    showDirectory(result)
    error.value = ''
  } catch (cause) {
    error.value = (cause as Error).message
  }
}

function openEditor(witness?: Verifier) {
  original.value = witness?.public_key ?? ''
  editing.value = witness ? fields(witness) : {
    name: '', public_key: '', report_origin: '', enabled: !policy.value && !directory.value?.policy_error,
  }
  editorError.value = ''
  editor.value?.showModal()
}

async function save(witnesses: ManagedWitness[], registered?: string) {
  if (!directory.value || busy.value || props.disabled) return
  busy.value = true
  error.value = ''
  editorError.value = ''
  const revision = directory.value.revision
  try {
    const result = await api.put<Directory>('/api/admin/quorum/witnesses', { body: { revision, witnesses } })
    showDirectory(result, registered)
    emit('saved', revision, result.revision, result.witnesses.map(fields))
    editor.value?.close()
    if (registered) void suggestPolicy()
  } catch (cause) {
    if (editor.value?.open) editorError.value = (cause as Error).message
    else error.value = (cause as Error).message
  } finally {
    busy.value = false
  }
}

function saveEditor() {
  if (!editing.value || !directory.value) return
  const rows = directory.value.witnesses.map(fields)
  const index = rows.findIndex(item => item.public_key === original.value)
  if (index < 0) rows.push(editing.value)
  else rows[index] = editing.value
  return save(rows, index < 0 ? editing.value.public_key : undefined)
}

function toggle(witness: Verifier) {
  if (!witness.enabled && ((policy.value && !policyKeys.value.has(witness.public_key)) || directory.value?.policy_error)) return
  return save((directory.value?.witnesses ?? []).map(item => item.public_key === witness.public_key
    ? { ...fields(item), enabled: !item.enabled }
    : fields(item)))
}

async function remove(witness: Verifier) {
  const accepted = await confirm({
    title: 'Remove verifier?',
    message: 'Remove ' + witness.name + ' from the witness list for future ballots?',
    confirmLabel: 'Remove verifier',
    tone: 'danger',
  })
  if (accepted) await save((directory.value?.witnesses ?? []).filter(item => item.public_key !== witness.public_key).map(fields))
}

onMounted(() => { void load(); timer = setInterval(load, 15000) })
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <section class="panel panel__body section-stack">
    <div class="toolbar toolbar--between">
      <h2>Verifiers</h2>
      <div class="button-row">
        <BaseButton size="small" variant="ghost" :disabled="busy" @click="load">Refresh</BaseButton>
        <BaseButton size="small" :disabled="disabled || busy || !directory || directory.witnesses.length >= 64" @click="openEditor()">Register verifier</BaseButton>
      </div>
    </div>
    <InlineFeedback :message="error" />
    <div v-if="directory && (directory.witnesses.length || policy || directory.policy_error)" class="policy-proposal section-stack" aria-labelledby="policy-proposal-title">
      <h3 id="policy-proposal-title">Witness policy</h3>
      <p v-if="policy">Configured policy: <strong>{{ policy.threshold }} of {{ policyKeys.size }}</strong>.</p>
      <InlineFeedback v-if="directory.policy_error" :message="'Could not load the configured policy: ' + directory.policy_error" />
      <template v-else>
        <p class="muted">Select the verifiers to include, then ask COPE for a recommended threshold. Newly registered verifiers can be included in a proposal before they join the configured policy.</p>
        <div class="button-row">
          <BaseButton size="small" :disabled="busy || disabled || !proposedKeys.length" :loading="proposing" @click="suggestPolicy">Suggest policy for {{ proposedKeys.length }} {{ proposedKeys.length === 1 ? 'verifier' : 'verifiers' }}</BaseButton>
          <BaseButton v-if="proposedPolicy && !noPolicyChange" size="small" variant="primary" @click="downloadPolicy">Download proposed policy</BaseButton>
        </div>
        <InlineFeedback :message="proposalError" />
        <div v-if="proposedPolicy" class="section-stack" aria-live="polite">
          <p><strong>Recommended: {{ proposedPolicy.threshold }} of {{ proposedKeys.length }}</strong>.
            <template v-if="proposedPolicy.max_faulty">Can still reach quorum with up to {{ proposedPolicy.max_faulty }} unavailable or malicious {{ proposedPolicy.max_faulty === 1 ? 'verifier' : 'verifiers' }}.</template>
            <template v-else>This policy assumes no malicious verifiers.</template>
          </p>
          <p v-if="noPolicyChange" class="muted">This matches the configured policy.</p>
          <template v-else>
            <p v-if="policy && faults(proposedPolicy) < faults(policy)">This proposal reduces the configured fault allowance from {{ faults(policy) }} to {{ faults(proposedPolicy) }}.</p>
            <p class="muted">{{ policy
              ? 'Send the downloaded proposal to the current verifiers for approval. Downloading it leaves the configured policy in force.'
              : 'Use the download to agree on an initial policy and give each verifier an independently authenticated copy.' }}</p>
          </template>
        </div>
      </template>
    </div>
    <p v-if="directory && !directory.witnesses.length" class="muted">No verifiers registered.</p>
    <ul v-else-if="directory" class="verifier-list">
      <li v-for="witness in directory.witnesses" :key="witness.public_key" class="verifier-row">
        <div class="toolbar toolbar--between">
          <div class="verifier-identity">
            <h3>{{ witness.name }}</h3>
            <StatusBadge :tone="witness.status === 'online' ? 'success' : witness.status === 'offline' ? 'warning' : 'neutral'" dot>{{ statusLabel[witness.status] }}</StatusBadge>
            <StatusBadge v-if="policy && !policyKeys.has(witness.public_key)">Outside configured policy</StatusBadge>
            <StatusBadge v-else-if="!witness.enabled">Paused for new ballots</StatusBadge>
          </div>
          <div class="button-row">
            <BaseButton size="small" variant="ghost" :disabled="disabled || busy" @click="openEditor(witness)">Edit</BaseButton>
            <BaseButton size="small" variant="ghost" :disabled="disabled || busy || (!witness.enabled && ((policy && !policyKeys.has(witness.public_key)) || Boolean(directory.policy_error)))" @click="toggle(witness)">{{ witness.enabled ? 'Pause' : 'Resume' }}</BaseButton>
            <BaseButton size="small" variant="ghost" :disabled="disabled || busy" @click="remove(witness)">Remove</BaseButton>
          </div>
        </div>
        <dl class="verifier-metrics">
          <div><dt>Last check-in</dt><dd>{{ witness.checkin ? dateFormat.format(new Date(witness.checkin.seen_at)) : '—' }}</dd></div>
          <div><dt>Last scan</dt><dd>{{ witness.checkin?.last_scan ? dateFormat.format(new Date(witness.checkin.last_scan * 1000)) : '—' }}</dd></div>
          <div><dt>Archived ballots</dt><dd>{{ witness.checkin?.archived ?? '—' }}</dd></div>
          <div><dt>Signed ballots</dt><dd>{{ witness.signed_count }}</dd></div>
          <div v-if="witness.checkin"><dt>Last scan errors</dt><dd>{{ witness.checkin.scan_errors }}</dd></div>
        </dl>
        <div class="verifier-meta muted">
          <code :title="witness.public_key">{{ witness.public_key.slice(0, 12) }}…{{ witness.public_key.slice(-6) }}</code>
          <span v-if="witness.checkin">Node {{ witness.checkin.runtime }}</span>
          <code v-if="witness.checkin?.app_commit" :title="witness.checkin.app_commit">{{ witness.checkin.app_commit.slice(0, 8) }}</code>
          <a v-if="witness.report_origin" :href="witness.report_origin" rel="noopener noreferrer" target="_blank">External reports</a>
        </div>
        <label v-if="!directory.policy_error">
          <input v-model="proposedKeys" type="checkbox" :value="witness.public_key" :disabled="busy || disabled">
          Include {{ witness.name }} in proposal
        </label>
      </li>
    </ul>
    <Teleport to="body">
      <dialog ref="editor" class="panel verifier-dialog" aria-labelledby="verifier-editor-title" @cancel="busy && $event.preventDefault()">
        <form v-if="editing" class="panel__body section-stack" @submit.prevent="saveEditor">
          <h2 id="verifier-editor-title">{{ original ? 'Edit verifier' : 'Register verifier' }}</h2>
          <label class="field"><span>Name</span><input v-model.trim="editing.name" class="input" maxlength="80" required></label>
          <label class="field"><span>Signing public key</span><input v-model.trim="editing.public_key" class="input input--mono" pattern="[0-9a-f]{64}" maxlength="64" :readonly="Boolean(original)" required></label>
          <label class="field"><span>External report URL (optional)</span><input v-model.trim="editing.report_origin" class="input" type="url" maxlength="250"></label>
          <InlineFeedback :message="editorError" />
          <div class="button-row">
            <BaseButton type="submit" variant="primary" :loading="busy">{{ original ? 'Save changes' : 'Register' }}</BaseButton>
            <BaseButton variant="ghost" :disabled="busy" @click="editor?.close()">Cancel</BaseButton>
          </div>
        </form>
      </dialog>
    </Teleport>
  </section>
</template>

<style scoped>
.policy-proposal { border: 1px solid var(--color-border); border-radius: var(--radius-md); padding: var(--space-4); }
.policy-proposal p, .policy-proposal h3 { margin: 0; }
.verifier-list { list-style: none; margin: 0; padding: 0; }
.verifier-row { display: grid; gap: var(--space-3); padding-block: var(--space-4); border-top: 1px solid var(--color-border); }
.verifier-row:last-child { padding-bottom: 0; }
.verifier-identity, .verifier-meta { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2) var(--space-3); }
.verifier-identity h3 { margin: 0; }
.verifier-meta { font-size: .8125rem; }
.verifier-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 9rem), 1fr)); gap: var(--space-3); margin: 0; }
.verifier-metrics dt { color: var(--color-text-muted); font-size: .8125rem; }
.verifier-metrics dd { margin: 0; font-variant-numeric: tabular-nums; }
.verifier-dialog { width: min(32rem, calc(100vw - 2rem)); max-height: calc(100dvh - 2rem); margin: auto; padding: 0; color: var(--color-text); }
.verifier-dialog::backdrop { background: var(--color-overlay); }
</style>
