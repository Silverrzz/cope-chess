<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, onBeforeRouteLeave, onBeforeRouteUpdate, useRouter } from 'vue-router'
import { ApiError } from '@/api/client'
import { claimHeaders, publicForm, rememberClaim, type FormClaim } from '@/api/forms'
import { verifyGuest } from '@/api/form-guest'
import { createFormUploader } from '@/api/form-uploads'
import FormExperience from '@/components/forms/FormExperience.vue'
import { requiredProgress } from '@/components/forms/progress'
import BaseButton from '@/components/ui/BaseButton.vue'
import { useApplicationMutation } from '@/composables/useApplicationMutation'
import { useSessionStore } from '@/stores/session'
import { canonicalAnswers, contentKinds, initialAnswers, type FormAnswers, type FormErrors, type FormField, type PublicForm } from '@/types/forms'

const props = defineProps<{ slug: string }>()
const router = useRouter()
const session = useSessionStore()
const form = ref<PublicForm | null>(null)
const answers = ref<FormAnswers>({})
const claim = ref<FormClaim | null>(null)
const mutation = useApplicationMutation('/api/forms/commands', () => claimHeaders(claim.value?.token))
const loading = ref(true)
const error = ref('')
const needsSignIn = ref(false)
const errors = ref<FormErrors>({})
const errorSummary = ref<HTMLElement | null>(null)
const fields = ref<InstanceType<typeof FormExperience> | null>(null)
const finished = ref(false)
const attachmentSubject = ref(crypto.randomUUID())
const uploading = ref(0)
const verifying = ref(false)
const submitting = ref(false)
const newerRelease = ref<PublicForm | null>(null)
const busy = computed(() => verifying.value || submitting.value || mutation.busy.value || uploading.value > 0)
const guestExpired = () => !!claim.value?.submit_until && Date.parse(claim.value.submit_until) <= Date.now()
const guestExtensions = computed(() => !session.authenticated ? form.value?.attachment_policy.guest_extensions : undefined)
const maxUpload = computed(() => {
  const policy = form.value?.attachment_policy
  if (!policy) return 10485760
  return session.authenticated ? policy.max_file_bytes : Math.min(policy.max_file_bytes, policy.guest_max_file_bytes ?? policy.max_file_bytes)
})
const hasAnswers = computed(() => Object.values(answers.value).some(value => value !== '' && value !== false && value !== undefined))
let page = new AbortController()
const unsubmitted = computed(() => !finished.value && (hasAnswers.value || mutation.uncertain.value || busy.value))

async function ensureClaim(): Promise<void> {
  if (session.authenticated || claim.value || !form.value) return
  const releaseId = form.value.release_id
  const signal = page.signal
  verifying.value = true
  try {
    const result = await verifyGuest(props.slug, releaseId, signal)
    signal.throwIfAborted()
    claim.value = result
  }
  finally { verifying.value = false }
}

const uploader = createFormUploader(() => {
  if (!form.value) throw new Error('Wait for the form to load.')
  return {
    form_id: form.value.id,
    release_id: form.value.release_id,
    subject: attachmentSubject.value,
    max_bytes: maxUpload.value,
    ...(guestExtensions.value ? { guest_extensions: guestExtensions.value } : {}),
  }
}, ensureClaim, () => claimHeaders(claim.value?.token))

async function upload(file: File, field: FormField, path: string[]): Promise<string> {
  uploading.value += 1
  try { return await uploader(file, field, path) }
  finally { uploading.value -= 1 }
}

watch(() => props.slug, async (slug, _, cleanup) => {
  page.abort()
  const controller = page = new AbortController()
  cleanup(() => controller.abort())
  form.value = null
  answers.value = {}
  claim.value = null
  newerRelease.value = null
  finished.value = false
  error.value = ''
  errors.value = {}
  needsSignIn.value = false
  loading.value = true
  attachmentSubject.value = crypto.randomUUID()
  try {
    const loaded = await publicForm(slug, controller.signal)
    if (!controller.signal.aborted) {
      form.value = loaded
      answers.value = initialAnswers(loaded.definition.fields)
    }
  } catch (cause) {
    if (!controller.signal.aborted) {
      error.value = cause instanceof Error ? cause.message : 'This form could not be loaded.'
      needsSignIn.value = cause instanceof ApiError && cause.status === 401
    }
  } finally {
    if (!controller.signal.aborted) loading.value = false
  }
}, { immediate: true })

async function showError(message: string, issues: FormErrors = {}): Promise<void> {
  error.value = message
  errors.value = issues
  await nextTick()
  errorSummary.value?.focus()
}

async function submit(): Promise<void> {
  if (!form.value || busy.value) return
  const definition = form.value.definition
  const issues = requiredProgress(definition.fields, answers.value).errors
  if (Object.keys(issues).length) {
    await showError('A few required answers are missing.', issues)
    return
  }
  const controller = page
  const formId = form.value.id
  submitting.value = true
  error.value = ''
  errors.value = {}
  try {
    await ensureClaim()
    controller.signal.throwIfAborted()
    const credential = claim.value
    const receipt = await mutation.execute<'forms.submit.v1'>({
      command: 'forms.submit.v1',
      target: form.value.id,
      input: {
        release_id: form.value.release_id,
        attachment_subject: attachmentSubject.value,
        answers: canonicalAnswers(definition.fields, answers.value),
      },
    })
    if (credential) rememberClaim(credential, receipt.output.response_id)
    if (!controller.signal.aborted) {
      finished.value = true
      await router.push({ name: 'form-response', params: { id: receipt.output.response_id }, query: { form: formId } })
    }
  } catch (cause) {
    if (!controller.signal.aborted) {
      await showError(cause instanceof Error ? cause.message : 'Your response could not be submitted.', cause instanceof ApiError ? cause.fieldErrors : {})
    }
  } finally { submitting.value = false }
}

async function renewGuest(): Promise<void> {
  if (!form.value || mutation.uncertain.value) return
  answers.value = carriedAnswers(form.value.definition.fields, form.value.definition.fields, answers.value)
  attachmentSubject.value = crypto.randomUUID()
  claim.value = null
  error.value = ''
  errors.value = {}
}

async function checkRelease(): Promise<void> {
  try {
    const current = await publicForm(props.slug)
    if (current.release_id !== form.value?.release_id) newerRelease.value = current
    else error.value = 'You are viewing the latest release. Your answers are still here.'
  } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Could not check the current release.' }
}

function carriedAnswers(previous: FormField[], next: FormField[], values: FormAnswers): FormAnswers {
  const result: FormAnswers = {}
  for (const field of next) {
    if (contentKinds.has(field.kind) || field.kind === 'attachment' || !(field.id in values)) continue
    const old = previous.find(item => item.id === field.id && item.kind === field.kind)
    if (!old) continue
    const value = values[field.id]
    if (field.kind === 'group' && Array.isArray(value)) {
      result[field.id] = (value as FormAnswers[]).map(row => ({ _row_id: row._row_id, ...carriedAnswers(old.fields ?? [], field.fields ?? [], row) }))
    } else if (JSON.stringify(old.choices) === JSON.stringify(field.choices) && JSON.stringify(old.rows) === JSON.stringify(field.rows)) {
      result[field.id] = value
    }
  }
  return initialAnswers(next, result)
}

function useLatest(): void {
  if (!newerRelease.value || !form.value || !window.confirm('Open the updated form? Review the carried-over answers and reattach any files.')) return
  const next = newerRelease.value
  answers.value = carriedAnswers(form.value.definition.fields, next.definition.fields, answers.value)
  claim.value = null
  attachmentSubject.value = crypto.randomUUID()
  form.value = next
  newerRelease.value = null
  error.value = ''
  errors.value = {}
}

function beforeUnload(event: BeforeUnloadEvent): void {
  if (unsubmitted.value) { event.preventDefault(); event.returnValue = '' }
}
window.addEventListener('beforeunload', beforeUnload)
onBeforeUnmount(() => {
  page.abort()
  window.removeEventListener('beforeunload', beforeUnload)
})
const allowLeave = () => !unsubmitted.value || window.confirm('Leave this form? Your unsubmitted answers will be lost.')
onBeforeRouteLeave(allowLeave)
onBeforeRouteUpdate((to, from) => to.params.slug === from.params.slug || (!mutation.busy.value && !mutation.uncertain.value && allowLeave()))
</script>

<template>
  <main class="public-form" :aria-busy="loading">
    <div v-if="loading" class="form-notice" role="status">Loading form…</div>
    <form v-else-if="form" novalidate @submit.prevent="submit">
      <FormExperience ref="fields" v-model="answers" :definition="form.definition" :errors="errors" :upload="upload"
        :max-upload-bytes="maxUpload" :guest-extensions="guestExtensions" :disabled="busy || mutation.uncertain.value">
        <template #context><p class="privacy">Your response is private to you and the people reviewing it.</p></template>
        <template #before-fields>
          <div v-if="error" ref="errorSummary" class="form-notice error" tabindex="-1" role="alert">
            <strong>{{ error }}</strong>
            <ul v-if="Object.keys(errors).length">
              <li v-for="(messages, path) in errors" :key="path"><button type="button" class="error-link" @click="fields?.focusField(String(path))">{{ messages.join(' ') }}</button></li>
            </ul>
            <template v-else>
              <BaseButton type="button" :disabled="busy || mutation.uncertain.value" @click="checkRelease" variant="ghost">Check for an updated form</BaseButton>
              <BaseButton v-if="guestExpired()" type="button" :disabled="busy || mutation.uncertain.value" @click="renewGuest" variant="ghost">Start a fresh guest session and reattach files</BaseButton>
            </template>
          </div>
          <div v-if="newerRelease" class="form-notice">
            <p>A newer release is available. Review the questions before submitting.</p>
            <BaseButton type="button" :disabled="busy || mutation.uncertain.value" @click="useLatest">Open updated form</BaseButton>
          </div>
        </template>
        <template #footer>
          <footer class="form-submit">
            <p v-if="verifying" role="status">Verifying your guest session…</p>
            <p v-else-if="mutation.uncertain.value" role="alert">Submission is unconfirmed. Retry to recover the same receipt.</p>
            <p v-else class="privacy">{{ session.authenticated ? 'Your response will open after you submit.' : 'Submit as a guest. You can save a private access receipt afterwards.' }}</p>
            <BaseButton type="submit" size="large" :loading="submitting" :disabled="busy">
              {{ mutation.uncertain.value ? 'Retry submission' : 'Submit response' }}
            </BaseButton>
          </footer>
        </template>
      </FormExperience>
    </form>
    <div v-else-if="error" class="form-notice error" role="alert">
      <p>{{ error }}</p>
      <RouterLink v-if="needsSignIn" :to="{ name: 'admin-login', query: { redirect: `/forms/${slug}` } }">Sign in to open this form</RouterLink>
    </div>
  </main>
</template>

<style scoped>
.public-form { width: min(100%, 50rem); margin: 0 auto; padding: clamp(1rem, 5vw, 3rem) clamp(1rem, 3vw, 1.5rem); }
.public-form form { margin: 0; }
.privacy { margin: .25rem 0 0; color: var(--color-text-muted); font-size: .85rem; line-height: 1.5; }
.form-notice { border: 1px solid var(--color-border); border-radius: var(--radius-md); padding: .9rem 1rem; font-size: .9rem; background: var(--color-surface-raised); }
.form-notice ul { padding-inline-start: 1.25rem; }
.error, .error-link { color: var(--color-danger); }
.error-link { background: none; border: none; text-align: left; cursor: pointer; text-decoration: underline; }
.form-submit { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: .7rem; padding-top: 1rem; border-top: 1px solid var(--color-border); }
.form-submit p { margin: 0; }
@media (max-width: 36rem) { .form-submit { display: grid; } }
</style>
