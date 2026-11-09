<script setup lang="ts">
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import StatusBadge from '@/components/admin/StatusBadge.vue'
import { formatDate, humanize } from '@/components/admin/format'
import FormAnswerSummary from '@/components/forms/FormAnswerSummary.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, onBeforeRouteLeave, useRoute } from 'vue-router'
import { ApiError } from '@/api/client'
import { claimHeaders, formResponse, rememberClaim, storedClaim, type FormClaim } from '@/api/forms'
import { createFormUploader, downloadAttachment } from '@/api/form-uploads'
import FormFields from '@/components/forms/FormFields.vue'
import { useApplicationMutation } from '@/composables/useApplicationMutation'
import { useConfirm } from '@/composables/useConfirm'
import { useSessionStore } from '@/stores/session'
import { canonicalAnswers, initialAnswers, type FormAnswers, type FormErrors, type FormField, type FormResponse } from '@/types/forms'

const props = defineProps<{ id: string }>()
const route = useRoute()
const { confirm } = useConfirm()
const adminView = computed(() => route.name === 'admin-form-response')
const editing = ref(false)
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
const decision = ref<'' | 'accepted' | 'declined' | 'changes_requested'>('')
const reason = ref('')
const uploading = ref(0)
const dirty = computed(() => JSON.stringify(answers.value) !== saved.value && !!response.value)
const busy = computed(() => mutation.busy.value || reviewMutation.busy.value || uploading.value > 0)
const uncertain = computed(() => mutation.uncertain.value || reviewMutation.uncertain.value)
const inboxLink = computed(() => response.value ? `/admin/form-responses?form=${response.value.form_id}` : '/admin/form-responses')
const publicTitle = computed(() => response.value ? 'Your response' : 'Open your response')
const guestExtensions = computed(() => claim.value ? response.value?.attachment_policy.guest_extensions : undefined)
const maxUpload = computed(() => {
  const policy = response.value?.attachment_policy
  return policy && claim.value ? Math.min(policy.max_file_bytes, policy.guest_max_file_bytes ?? policy.max_file_bytes) : policy?.max_file_bytes
})
const uploader = createFormUploader(() => {
  if (!response.value?.attachment_subject) throw new Error('Only the submitter can upload amendments for this response.')
  return { form_id: response.value.form_id, release_id: response.value.release_id, subject: response.value.attachment_subject, response_id: props.id, max_bytes: maxUpload.value ?? response.value.attachment_policy.max_file_bytes, ...(guestExtensions.value ? { guest_extensions: guestExtensions.value } : {}) }
}, async () => {}, () => claimHeaders(claim.value?.token))
async function upload(file: File, field: FormField, path: string[]): Promise<string> {
  uploading.value += 1
  try { return await uploader(file,field,path) } finally { uploading.value -= 1 }
}
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
  if (!response.value || uploading.value) return
  error.value = ''; errors.value = {}; message.value = ''
  const snapshot = canonicalAnswers(response.value.definition.fields, answers.value)
  const clear = Object.keys(response.value.answers).filter((key) => !(key in snapshot))
  try {
    const receipt = await mutation.execute<'forms.amend.v1'>({ command: 'forms.amend.v1', target: props.id, expected_version: response.value.revision, input: { set: snapshot, clear } })
    await load()
    editing.value = false
    message.value = `Response updated to revision ${receipt.output.revision}.`
  } catch (cause) { await failure(cause) }
}
async function withdraw(): Promise<void> {
  if (!response.value) return
  if (!mutation.uncertain.value && !await confirm({
    title: 'Withdraw this response?',
    message: 'Your response will be marked withdrawn and its history will be kept.',
    confirmLabel: 'Withdraw response', tone: 'danger',
  })) return
  try {
    await mutation.execute<'forms.withdraw.v1'>({ command: 'forms.withdraw.v1', target: props.id, expected_version: response.value.state_version, input: {} })
    await load(); editing.value = false; message.value = 'Response withdrawn.'
  } catch (cause) { await failure(cause) }
}
async function review(): Promise<void> {
  if (!response.value || !decision.value || !adminView.value) return
  try {
    await reviewMutation.execute<'forms.review.v1'>({ command: 'forms.review.v1', target: props.id, expected_version: response.value.revision, input: { state_version: response.value.state_version, subject_digest: response.value.subject_digest, decision: decision.value, reason: reason.value } })
    await load(); decision.value = ''; reason.value = ''; message.value = 'Review decision recorded.'
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
    if (response.value && response.value.form_id === claim.value.form_id) rememberClaim(claim.value, props.id)
  } catch (cause) { await failure(cause) }
  finally { (event.target as HTMLInputElement).value = '' }
}
function download(): void {
  if (!response.value) return
  const data = { response_id: props.id, form_id: response.value.form_id, url: new URL(`/forms/responses/${props.id}?form=${response.value.form_id}`, location.origin).href, claim: claim.value }
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = `cope-response-${props.id}.json`; anchor.click(); URL.revokeObjectURL(url)
}
function startEditing(): void {
  if (!response.value?.can_amend) return
  answers.value = initialAnswers(response.value.definition.fields, structuredClone(response.value.answers))
  saved.value = JSON.stringify(answers.value)
  editing.value = true
}

async function cancelEdit(): Promise<void> {
  if (dirty.value && !await confirm({ title: 'Discard changes?', message: 'Your submitted response will stay as it is.', confirmLabel: 'Discard changes' })) return
  if (response.value) answers.value = initialAnswers(response.value.definition.fields, response.value.answers)
  saved.value = JSON.stringify(answers.value)
  errors.value = {}
  error.value = ''
  editing.value = false
}

function beforeUnload(event: BeforeUnloadEvent): void { if (dirty.value || uncertain.value) { event.preventDefault(); event.returnValue = '' } }
onMounted(() => {
  const formId = typeof route.query.form === 'string' ? route.query.form : ''
  claim.value = adminView.value || session.authenticated ? null : storedClaim(formId, props.id)
  window.addEventListener('beforeunload', beforeUnload); void load()
})
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
onBeforeRouteLeave(() => !(dirty.value || uncertain.value) || window.confirm('Leave this response? Unsaved edits or an unconfirmed action may remain.'))
</script>

<template>
  <div class="page-stack response-page" :class="adminView ? 'admin-page' : 'response-page--public'" :aria-busy="loading">
    <template v-if="adminView">
      <nav class="response-breadcrumbs" aria-label="Breadcrumb"><RouterLink :to="inboxLink">Responses</RouterLink><span aria-hidden="true">/</span><span aria-current="page">Review response</span></nav>
      <AdminPageHeader :title="response?.definition.title ?? 'Review response'">
        <template v-if="response" #actions><BaseButton :to="`/forms/${response.slug}`" variant="secondary">Open form</BaseButton></template>
      </AdminPageHeader>
    </template>
    <header v-else class="panel panel__body response-hero">
      <StatusBadge v-if="response" :status="response.state === 'awaiting_review' ? 'completed' : response.state"
        :label="response.state === 'awaiting_review' ? 'Submitted' : humanize(response.state)" />
      <h1>{{ publicTitle }}</h1>
      <p v-if="response">{{ response.definition.title }}</p>
      <p v-else-if="!loading">Use your access receipt to reopen a guest response.</p>
    </header>

    <div v-if="error" ref="errorSummary" class="response-alert" tabindex="-1">
      <InlineFeedback :message="error" />
      <ul v-if="Object.keys(errors).length" class="form-errors">
        <li v-for="(messages, path) in errors" :key="path"><BaseButton variant="ghost" size="small" @click="fields?.focusField(String(path))">{{ messages.join(' ') }}</BaseButton></li>
      </ul>
      <BaseButton v-if="response && !uncertain" variant="ghost" size="small" @click="load(editing)">Refresh response</BaseButton>
    </div>
    <InlineFeedback :message="message" tone="info" />
    <InlineFeedback v-if="uncertain" message="Retry the same action to recover its receipt." tone="info" />
    <div v-if="loading && !response" class="panel panel__body" role="status">Loading response…</div>
    <section v-else-if="!adminView && !session.authenticated && !response" class="panel panel__body response-panel">
      <h2>Restore access</h2>
      <label>Access receipt<input class="input" type="file" accept="application/json,.json" @change="recover"></label>
      <p class="response-muted">Use the JSON receipt you downloaded after submitting.</p>
    </section>

    <template v-if="response">
      <div class="response-workspace" :class="{ 'response-workspace--review': adminView && response.can_review }">
        <section class="panel panel__body response-panel">
          <header class="response-heading">
            <div><h2>{{ editing ? 'Edit your answers' : 'Submitted answers' }}</h2><p class="response-muted">Revision {{ response.revision }} · {{ formatDate(response.created_at) }}</p></div>
            <StatusBadge v-if="adminView" :status="response.state" />
            <BaseButton v-else-if="response.can_amend && !editing" size="small" variant="secondary" :disabled="busy || uncertain" @click="startEditing">Edit response</BaseButton>
          </header>
          <form v-if="editing && !adminView" novalidate class="response-edit" @submit.prevent="amend">
            <FormFields ref="fields" v-model="answers" :fields="response.definition.fields" :errors="errors" :upload="upload"
              :max-upload-bytes="maxUpload" :guest-extensions="guestExtensions" :download="downloadFile" :disabled="!response.can_amend || busy || uncertain" />
            <div class="response-actions">
              <BaseButton variant="ghost" :disabled="busy || uncertain" @click="cancelEdit">Cancel</BaseButton>
              <BaseButton type="submit" variant="primary" :disabled="(!dirty && !mutation.uncertain.value) || mutation.unavailable('forms.amend.v1')">{{ mutation.uncertain.value ? 'Retry save' : 'Save changes' }}</BaseButton>
            </div>
          </form>
          <FormAnswerSummary v-else :fields="response.definition.fields" :answers="response.answers" :download="downloadFile" />
        </section>

        <section v-if="adminView && response.can_review" class="panel panel__body response-panel">
          <header class="response-heading"><div><h2>Review</h2><p class="response-muted">Decision for revision {{ response.revision }}</p></div></header>
          <form class="response-edit" @submit.prevent="review">
            <fieldset :disabled="busy || uncertain">
              <label>Decision<select v-model="decision" class="input" required>
                <option value="" disabled>Choose a decision</option><option value="accepted">Accept</option>
                <option value="changes_requested">Request changes</option><option value="declined">Decline</option>
              </select></label>
              <label>Note to the respondent<textarea v-model="reason" class="input" rows="4" maxlength="8000" /></label>
            </fieldset>
            <BaseButton type="submit" variant="primary" :disabled="!decision || reviewMutation.unavailable('forms.review.v1')">{{ reviewMutation.uncertain.value ? 'Retry decision' : 'Save decision' }}</BaseButton>
          </form>
        </section>
      </div>

      <section v-if="!adminView" class="panel panel__body response-panel">
        <header class="response-heading">
          <div><h2>Response access</h2><p v-if="claim" class="response-muted">Keep your receipt private to reopen this response from another browser.</p></div>
        </header>
        <div class="response-actions">
          <BaseButton variant="secondary" @click="download">Download receipt</BaseButton>
          <BaseButton :to="`/forms/${response.slug}`" variant="ghost">Back to form</BaseButton>
          <BaseButton v-if="response.can_withdraw && !editing" variant="ghost" :disabled="mutation.unavailable('forms.withdraw.v1')" @click="withdraw">Withdraw response</BaseButton>
          <BaseButton v-if="response.can_review" :to="`/admin/form-responses/${response.id}`" variant="ghost">Open admin review</BaseButton>
        </div>
      </section>

      <section v-if="response.reviews.length" class="panel panel__body response-panel">
        <h2>Review history</h2>
        <ol class="response-history">
          <li v-for="entry in response.reviews" :key="entry.id">
            <div class="response-actions"><StatusBadge :status="entry.decision" /><span class="response-muted">Revision {{ entry.response_revision }} · {{ formatDate(entry.created_at) }}</span></div>
            <p v-if="entry.reason">{{ entry.reason }}</p>
          </li>
        </ol>
      </section>
    </template>
  </div>
</template>

<style scoped>
.response-page--public { width: min(100%, 50rem); margin-inline: auto; padding: clamp(1rem, 4vw, 2.5rem) 1rem; }
.response-breadcrumbs, .response-actions { display: flex; align-items: center; flex-wrap: wrap; gap: .65rem; }
.response-breadcrumbs, .response-muted { font-size: .85rem; color: var(--color-text-muted); }
.response-hero, .response-panel, .response-edit, .response-alert { display: grid; gap: .85rem; }
.response-hero { justify-items: start; }
.response-hero h1, .response-hero p, .response-panel h2, .response-panel p { margin: 0; }
.response-hero h1 { font-size: clamp(1.65rem, 4vw, 2.15rem); }
.response-panel h2 { font-size: 1.1rem; }
.response-heading { display: flex; justify-content: space-between; align-items: start; gap: .75rem; flex-wrap: wrap; padding-bottom: .85rem; border-bottom: 1px solid var(--color-border); }
.response-heading p { margin-top: .25rem; }
.response-workspace { display: grid; gap: 1rem; align-items: start; }
.response-workspace--review { grid-template-columns: minmax(0, 1.5fr) minmax(17rem, 1fr); }
.response-edit fieldset { display: grid; gap: .85rem; padding: 0; margin: 0; border: 0; min-width: 0; }
.response-edit label, .response-panel > label { display: grid; gap: .35rem; font-size: .85rem; font-weight: 600; }
.response-edit > .base-button { justify-self: start; }
.response-history { list-style: none; padding: 0; margin: 0; }
.response-history li { display: grid; gap: .5rem; border-top: 1px solid var(--color-border); padding-block: .8rem; }
.response-history p { white-space: pre-wrap; overflow-wrap: anywhere; }
@media (max-width: 54rem) { .response-workspace--review { grid-template-columns: 1fr; } }
</style>
