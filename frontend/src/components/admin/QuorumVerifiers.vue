<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import api from '@/api/client'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { useConfirm } from '@/composables/useConfirm'
import type { Witness } from '@/quorum'

type ManagedWitness = Witness & { enabled: boolean }
interface Verifier extends ManagedWitness {
  status: 'awaiting_checkin' | 'online' | 'offline'
  checkin: { seen_at: string; last_scan: number | null; archived: number; scan_errors: number; runtime: string; app_commit: string | null } | null
  signed_count: number
  last_signed: string | null
}
interface Directory { revision: number; witnesses: Verifier[] }

const props = defineProps<{ disabled?: boolean }>()
const emit = defineEmits<{ saved: [fromRevision: number, revision: number, witnesses: ManagedWitness[]] }>()
const directory = ref<Directory>()
const editor = ref<HTMLDialogElement>()
const editing = ref<ManagedWitness>()
const original = ref('')
const busy = ref(false)
const error = ref('')
const editorError = ref('')
const { confirm } = useConfirm()
let timer: ReturnType<typeof setInterval> | undefined
const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'short', timeStyle: 'medium' })
const statusLabel = { awaiting_checkin: 'Awaiting check-in', online: 'Online', offline: 'Offline' }

function fields({ name, public_key, report_origin, enabled }: ManagedWitness): ManagedWitness {
  return { name, public_key, report_origin, enabled }
}

async function load() {
  if (busy.value || editor.value?.open) return
  try {
    const result = await api.get<Directory>('/api/admin/quorum/witnesses')
    if (busy.value || editor.value?.open || result.revision < (directory.value?.revision ?? 0)) return
    directory.value = result
    error.value = ''
  } catch (cause) {
    error.value = (cause as Error).message
  }
}

function openEditor(witness?: Verifier) {
  original.value = witness?.public_key ?? ''
  editing.value = witness ? fields(witness) : { name: '', public_key: '', report_origin: '', enabled: true }
  editorError.value = ''
  editor.value?.showModal()
}

async function save(witnesses: ManagedWitness[]) {
  if (!directory.value || busy.value || props.disabled) return
  busy.value = true
  error.value = ''
  editorError.value = ''
  const revision = directory.value.revision
  try {
    const result = await api.put<Directory>('/api/admin/quorum/witnesses', { body: { revision, witnesses } })
    directory.value = result
    emit('saved', revision, result.revision, result.witnesses.map(fields))
    editor.value?.close()
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
  return save(rows)
}

function toggle(witness: Verifier) {
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
    <p v-if="directory && !directory.witnesses.length" class="muted">No verifiers registered.</p>
    <ul v-else-if="directory" class="verifier-list">
      <li v-for="witness in directory.witnesses" :key="witness.public_key" class="verifier-row">
        <div class="toolbar toolbar--between">
          <div class="verifier-identity">
            <h3>{{ witness.name }}</h3>
            <StatusBadge :tone="witness.status === 'online' ? 'success' : witness.status === 'offline' ? 'warning' : 'neutral'" dot>{{ statusLabel[witness.status] }}</StatusBadge>
            <StatusBadge v-if="!witness.enabled">Paused for new ballots</StatusBadge>
          </div>
          <div class="button-row">
            <BaseButton size="small" variant="ghost" :disabled="disabled || busy" @click="openEditor(witness)">Edit</BaseButton>
            <BaseButton size="small" variant="ghost" :disabled="disabled || busy" @click="toggle(witness)">{{ witness.enabled ? 'Pause' : 'Resume' }}</BaseButton>
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
