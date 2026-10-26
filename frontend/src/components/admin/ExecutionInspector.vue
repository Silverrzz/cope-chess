<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { ApiError } from '@/api/client'
import { applicationQuery, executeCommand } from '@/api/application'
import type { ApplicationQueryOutputs } from '@/types/application-inputs'
import BaseButton from '@/components/ui/BaseButton.vue'

const props = defineProps<{ id: string; kind: 'operation' | 'job'; version: number }>()
const emit = defineEmits<{ refresh: []; inspect: [id: string]; denied: [message: string] }>()
type Detail = ApplicationQueryOutputs['execution.operations.detail.v1'] | ApplicationQueryOutputs['execution.jobs.detail.v1']
const data = ref<Detail | null>(null)
const loading = ref(false)
const cancelling = ref(false)
const error = ref('')
let generation = 0
let active = true
let controller: AbortController | null = null
let pending: { id: string; version: number; key: string } | null = null

function message(cause: unknown): string { return cause instanceof Error ? cause.message : 'Could not inspect execution.' }
function time(value: string): string { const result = new Date(value); return Number.isFinite(result.getTime()) ? result.toLocaleString() : 'Unavailable' }
async function load(): Promise<void> {
  const current = ++generation
  controller?.abort(); controller = new AbortController()
  data.value = null; loading.value = true; error.value = ''
  try {
    const result = props.kind === 'operation'
      ? await applicationQuery('execution.operations.detail.v1',{},props.id,controller.signal)
      : await applicationQuery('execution.jobs.detail.v1',{},props.id,controller.signal)
    if (active && current === generation) data.value = result
  } catch (cause) {
    if (!active || current !== generation || cause instanceof DOMException && cause.name === 'AbortError') return
    error.value = message(cause)
    if (cause instanceof ApiError && [401,403].includes(cause.status)) emit('denied',error.value)
  } finally { if (active && current === generation) loading.value = false }
}
async function cancel(): Promise<void> {
  const selected = data.value
  if (!selected || selected.kind !== 'operation' || !selected.may_cancel || cancelling.value) return
  const current = generation
  const request = pending?.id === selected.id && pending.version === selected.state_version ? pending : {id:selected.id,version:selected.state_version,key:crypto.randomUUID()}
  pending = request; cancelling.value = true; error.value = ''
  try {
    await executeCommand({command:'operations.cancel.v1',target:request.id,expected_version:request.version,idempotency_key:request.key,input:{reason:'Cancelled from the activity inspector.'}})
    if (!active || current !== generation) return
    pending = null
    await load()
    emit('refresh')
  } catch (cause) {
    if (!active || current !== generation) return
    error.value = message(cause)
    if (cause instanceof ApiError && [401,403].includes(cause.status)) { data.value = null; emit('denied',error.value) }
  } finally { if (active) cancelling.value = false }
}
watch(() => [props.id,props.kind,props.version] as const,() => { pending = null; void load() },{immediate:true})
onBeforeUnmount(() => { active = false; ++generation; controller?.abort(); data.value = null; pending = null })
</script>

<template>
  <section class="execution-inspector" :aria-busy="loading">
    <h2>{{ kind === 'operation' ? 'Preparation operation' : 'Preparation job' }}</h2>
    <p v-if="loading">Loading current execution state…</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <BaseButton variant="ghost" size="small" :disabled="loading || cancelling" @click="load">Refresh execution</BaseButton>
    <template v-if="data">
      <dl><dt>State</dt><dd>{{ data.status }}</dd><dt>Reference</dt><dd>{{ data.id }}</dd><dt>Created</dt><dd>{{ time(data.created_at) }}</dd><dt>Cleanup</dt><dd>No attempt has started</dd></dl>
      <template v-if="data.kind === 'operation'">
        <p>This receipt admitted preparation. Execution and artifact verification have separate completion states.</p>
        <ul><li v-for="job in data.jobs" :key="job.id"><BaseButton variant="ghost" size="small" @click="emit('inspect',job.id)">Inspect preparation job</BaseButton><p>{{ job.status }}<template v-if="job.readiness"> · {{ job.readiness.message }}</template></p></li></ul>
        <BaseButton v-if="data.may_cancel" :loading="cancelling" @click="cancel">Cancel pending preparation</BaseButton>
        <p v-if="data.status === 'cancelled'">This operation's interest is cancelled. Shared jobs keep their other consumers.</p>
      </template>
      <template v-else>
        <p v-if="data.readiness">{{ data.readiness.message }}</p><p v-if="data.status === 'cancelled'">This preparation job was cancelled before any attempt started.</p>
        <p v-if="data.recovery_review_required">This request predates the current control authority. It needs a new preparation decision after recovery reconciliation.</p>
        <dl><dt>Admission deadline</dt><dd>{{ time(data.not_after) }}</dd><dt>Authority</dt><dd>Bound to the initiating credential; new dispatch checks current permission</dd><dt>Revocation policy</dt><dd>Already admitted pure work may finish; no further dispatch after revocation</dd></dl>
        <template v-if="data.input"><h3>Admitted inputs and bounds</h3><dl><dt>Engine version</dt><dd>{{ data.input.engine_version_id }}</dd><dt>Build</dt><dd>{{ data.input.build_hash }}</dd><dt>Archive</dt><dd>{{ data.input.archive_sha256 }}</dd><dt>Archive size</dt><dd>{{ data.input.archive_bytes.toLocaleString() }} bytes</dd><dt>Runtime image</dt><dd>{{ data.input.runtime_image }}</dd><dt>Profile</dt><dd>{{ data.input.profile }}</dd><dt>CPU ceiling</dt><dd>{{ data.input.cpu_millis / 1000 }} CPU</dd><dt>Memory ceiling</dt><dd>{{ (data.input.memory_bytes / 1048576).toLocaleString() }} MiB</dd><dt>Execution timeout</dt><dd>{{ data.input.timeout_seconds }} seconds</dd></dl></template>
        <p v-else>Input details require current engine preparation permission.</p>
        <BaseButton variant="ghost" size="small" @click="emit('inspect',data.operation_id)">View operation</BaseButton>
      </template>
    </template>
  </section>
</template>

<style scoped>
.execution-inspector{border:1px solid var(--color-border);border-radius:.7rem;padding:1.2rem}.execution-inspector h2{font-size:1.05rem;margin-top:0}.execution-inspector h3{font-size:.9rem}.execution-inspector p,.execution-inspector dl{font-size:.8rem;line-height:1.6}.execution-inspector dl{display:grid;gap:.5rem 1rem;grid-template-columns:8rem minmax(0,1fr)}.execution-inspector dt{color:var(--color-text-muted)}.execution-inspector dd{margin:0;overflow-wrap:anywhere}.execution-inspector ul{list-style:none;margin:0;padding:0}.error{color:var(--color-danger)}@media(max-width:42rem){.execution-inspector dl{grid-template-columns:1fr}}
</style>
