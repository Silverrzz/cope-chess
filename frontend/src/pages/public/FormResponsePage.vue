<script setup lang="ts">
import BaseButton from '@/components/ui/BaseButton.vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, onBeforeRouteLeave, useRoute } from 'vue-router'
import { ApiError } from '@/api/client'
import { claimHeaders, formResponse, rememberClaim, storedClaim, type FormClaim } from '@/api/forms'
import { createFormUploader, downloadAttachment } from '@/api/form-uploads'
import FormFields from '@/components/forms/FormFields.vue'
import { useApplicationMutation } from '@/composables/useApplicationMutation'
import { useSessionStore } from '@/stores/session'
import { canonicalAnswers, initialAnswers, type FormAnswers, type FormErrors, type FormResponse } from '@/types/forms'

const props = defineProps<{ id: string }>()
const route = useRoute()
const session = useSessionStore()
const response = ref<FormResponse | null>(null)
const claim = ref<FormClaim | null>(null)
const mutation = useApplicationMutation('/api/forms/commands', () => claimHeaders(claim.value?.token))
const reviewMutation = useApplicationMutation()
const answers = ref<FormAnswers>({})
const saved = ref('')
const loading = ref(false)
const error = ref('')
const errors = ref<FormErrors>({})
const message = ref('')
const errorSummary = ref<HTMLElement | null>(null)
const fields = ref<InstanceType<typeof FormFields> | null>(null)
const decision = ref<'accepted' | 'declined' | 'changes_requested'>('changes_requested')
const reason = ref('')
const dirty = computed(() => JSON.stringify(answers.value) !== saved.value && !!response.value)
const busy = computed(() => mutation.busy.value || reviewMutation.busy.value)
const uncertain = computed(() => mutation.uncertain.value || reviewMutation.uncertain.value)
const labels: Record<string, string> = { awaiting_review: 'Awaiting review', accepted: 'Accepted', declined: 'Declined', changes_requested: 'Changes requested', withdrawn: 'Withdrawn' }
const upload = createFormUploader(() => {
  if (!response.value?.attachment_subject) throw new Error('Only the submitter can upload amendments for this response.')
  return { form_id: response.value.form_id, release_id: response.value.release_id, subject: response.value.attachment_subject, response_id: props.id, max_bytes: response.value.attachment_policy.max_file_bytes }
}, async () => {}, () => claimHeaders(claim.value?.token))
async function downloadFile(id: string): Promise<void> { await downloadAttachment(id, claimHeaders(claim.value?.token)) }

async function failure(cause: unknown): Promise<void> {
  error.value = cause instanceof Error ? cause.message : 'The action could not be completed.'
  errors.value = cause instanceof ApiError ? cause.fieldErrors : {}
  await nextTick(); errorSummary.value?.focus()
}
async function load(preserve = false): Promise<void> {
  loading.value = true
  try {
    const loaded = await formResponse(props.id, claim.value?.token)
    response.value = loaded
    if (!preserve) answers.value = initialAnswers(loaded.definition.fields, structuredClone(loaded.answers))
    saved.value = JSON.stringify(initialAnswers(loaded.definition.fields, loaded.answers))
    error.value = ''; errors.value = {}
  } catch (cause) { await failure(cause) }
  finally { loading.value = false }
}
async function amend(): Promise<void> {
  if (!response.value) return
  error.value = ''; errors.value = {}; message.value = ''
  const snapshot = canonicalAnswers(response.value.definition.fields, answers.value)
  const clear = Object.keys(response.value.answers).filter((key) => !(key in snapshot))
  try {
    const receipt = await mutation.execute<'forms.amend.v1'>({ command: 'forms.amend.v1', target: props.id, expected_version: response.value.revision, input: { set: snapshot, clear } })
    await load(true)
    message.value = `Saved response revision ${receipt.output.revision}. Its current review status is shown below.`
  } catch (cause) { await failure(cause) }
}
async function withdraw(): Promise<void> {
  if (!response.value) return
  if (!mutation.uncertain.value && !window.confirm('Withdraw this response? It will no longer be available for acceptance. Its history will be retained.')) return
  try {
    await mutation.execute<'forms.withdraw.v1'>({ command: 'forms.withdraw.v1', target: props.id, expected_version: response.value.state_version, input: {} })
    await load(); message.value = 'Response withdrawn.'
  } catch (cause) { await failure(cause) }
}
async function review(): Promise<void> {
  if (!response.value) return
  try {
    await reviewMutation.execute<'forms.review.v1'>({ command: 'forms.review.v1', target: props.id, expected_version: response.value.revision, input: { state_version: response.value.state_version, subject_digest: response.value.subject_digest, decision: decision.value, reason: reason.value } })
    await load(); reason.value = ''; message.value = 'Review decision recorded for the displayed response revision.'
  } catch (cause) { await failure(cause) }
}
async function recover(event: Event): Promise<void> {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  try {
    if (file.size > 10000) throw new Error('Choose the small access receipt downloaded after submission.')
    const value = JSON.parse(await file.text()) as { response_id?: string; claim?: FormClaim }
    if (value.response_id !== props.id || !value.claim || !/^cope_form_[A-Za-z0-9_-]+$/.test(value.claim.token) || Date.parse(value.claim.expires_at) <= Date.now()) throw new Error('This receipt does not provide current access to this response.')
    claim.value = value.claim
    await load()
    if (response.value && response.value.form_id === claim.value.form_id) rememberClaim(claim.value)
  } catch (cause) { await failure(cause) }
  finally { (event.target as HTMLInputElement).value = '' }
}
function download(): void {
  if (!response.value) return
  const data = { response_id: props.id, form_id: response.value.form_id, url: new URL(`/forms/responses/${props.id}?form=${response.value.form_id}`, location.origin).href, claim: claim.value }
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = `cope-response-${props.id}.json`; anchor.click(); URL.revokeObjectURL(url)
}
function beforeUnload(event: BeforeUnloadEvent): void { if (dirty.value || uncertain.value) { event.preventDefault(); event.returnValue = '' } }
onMounted(() => {
  const formId = typeof route.query.form === 'string' ? route.query.form : ''
  claim.value = session.authenticated ? null : storedClaim(formId)
  window.addEventListener('beforeunload', beforeUnload); void load()
})
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
onBeforeRouteLeave(() => !(dirty.value || uncertain.value) || window.confirm('Leave this response? Unsaved edits or an unconfirmed action may remain.'))
</script>

<template>
  <main class="response-page" :aria-busy="loading"><header><p class="eyebrow">Private response</p><h1>{{ response?.definition.title ?? 'Your response' }}</h1><p v-if="response">{{ labels[response.state] }} · revision {{ response.revision }} · submitted {{ new Date(response.created_at).toLocaleString() }}</p><p v-if="response?.next_actor">Next action: {{ response.next_actor === 'reviewer' ? 'a reviewer will consider your response' : 'the submitter can review the requested changes' }}.</p></header>
    <div v-if="error" ref="errorSummary" class="notice error" role="alert" tabindex="-1"><strong>{{ error }}</strong><ul v-if="Object.keys(errors).length"><li v-for="(messages, path) in errors" :key="path"><BaseButton type="button" variant="ghost" size="small" @click="fields?.focusField(String(path))">{{ messages.join(' ') }}</BaseButton></li></ul><BaseButton v-if="response && !uncertain" type="button" class="button button--ghost" @click="load(true)" variant="ghost">Refresh current status and keep my edits</BaseButton></div><p v-if="message" class="notice" role="status">{{ message }}</p><p v-if="uncertain" class="notice" role="alert">The action’s outcome is unconfirmed. Retry the same action to recover its receipt.</p>
    <section v-if="!session.authenticated && !response" class="notice"><h2>Open a private response</h2><label>Restore access from your downloaded receipt<input type="file" accept="application/json,.json" @change="recover"></label><p>Keep this receipt private. It grants access to your response until the credential expires.</p></section>
    <template v-if="response"><div class="toolbar"><BaseButton type="button" class="button button--ghost" @click="download" variant="ghost">Download access receipt</BaseButton><RouterLink v-if="session.user?.role === 'admin'" :to="`/admin/form-responses?form=${response.form_id}`">Response inbox</RouterLink><RouterLink :to="`/forms/${response.slug}`">Open form</RouterLink></div><p v-if="claim" class="privacy">Save your access receipt to return later. It contains a private credential valid until {{ new Date(claim.expires_at).toLocaleString() }}.</p><form novalidate @submit.prevent="amend"><FormFields ref="fields" v-model="answers" :fields="response.definition.fields" :errors="errors" :upload="upload" :download="downloadFile" :disabled="!response.can_amend || busy || uncertain" /><div v-if="response.can_amend || response.can_withdraw" class="toolbar"><BaseButton type="submit" v-if="response.can_amend" class="button" :disabled="reviewMutation.uncertain.value || mutation.unavailable('forms.amend.v1')">Save amendment</BaseButton><BaseButton v-if="response.can_withdraw" type="button" class="button button--ghost" :disabled="reviewMutation.uncertain.value || mutation.unavailable('forms.withdraw.v1')" @click="withdraw" variant="ghost">Withdraw response</BaseButton></div></form>
    <section v-if="response.can_review" class="review"><h2>Review revision {{ response.revision }}</h2><p>This decision applies to the displayed answers and their current review subject.</p><dl v-if="response.review_context"><dt>Purpose</dt><dd>{{ response.review_context.binding }}</dd><dt>Policy</dt><dd>{{ response.review_context.policy_revision }}</dd></dl><form @submit.prevent="review"><fieldset :disabled="busy || uncertain"><label>Decision<select v-model="decision" class="input"><option value="changes_requested">Request changes</option><option value="accepted">Accept</option><option value="declined">Decline</option></select></label><label>Reason<textarea v-model="reason" class="input" rows="3" maxlength="8000" /></label></fieldset><BaseButton type="submit" class="button" :disabled="busy || mutation.uncertain.value">Record decision</BaseButton></form></section>
    <section class="history"><h2>Review history</h2><p v-if="!response.reviews.length">No review decision has been recorded.</p><ol><li v-for="entry in response.reviews" :key="entry.id"><strong>{{ labels[entry.decision] }} · response revision {{ entry.response_revision }}</strong><time>{{ new Date(entry.created_at).toLocaleString() }}</time><p v-if="entry.reason">{{ entry.reason }}</p><small v-if="entry.response_revision !== response.revision">A previous revision. This decision does not determine the current response status.</small></li></ol></section></template>
  </main>
</template>

<style scoped>
.response-page{display:grid;gap:1.3rem;margin:2rem auto;max-width:48rem;padding:0 1rem}.response-page h1{font-size:1.8rem}.response-page h2{font-size:1.1rem}.notice,.review{border:1px solid var(--color-border);border-radius:.7rem;padding:1rem}.notice{font-size:.85rem}.error{color:var(--color-danger,#b91c1c)}.toolbar{display:flex;flex-wrap:wrap;gap:.75rem;align-items:center;margin-top:1rem}.privacy{color:var(--color-text-muted,#64748b);font-size:.8rem}.review fieldset{border:0;display:grid;gap:1rem;margin:0 0 1rem;padding:0}.review label,.notice label{display:grid;gap:.4rem;font-size:.85rem}.review dl{display:grid;grid-template-columns:auto 1fr;font-size:.8rem;gap:.4rem .8rem}.review dd{margin:0}.history ol{list-style:none;margin:0;padding:0}.history li{border-top:1px solid var(--color-border);display:grid;gap:.4rem;padding:1rem 0;font-size:.85rem}.history li p{margin:0;white-space:pre-wrap}.history time,.history small{color:var(--color-text-muted,#64748b);font-size:.75rem}
</style>
