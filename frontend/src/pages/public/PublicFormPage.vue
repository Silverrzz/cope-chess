<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { ApiError } from '@/api/client'
import { publicForm, submitForm } from '@/api/forms'
import { createFormUploader } from '@/api/form-uploads'
import FormExperience from '@/components/forms/FormExperience.vue'
import FormMarkdown from '@/components/forms/FormMarkdown.vue'
import FormPublicResults from '@/components/forms/FormPublicResults.vue'
import { requiredProgress } from '@/components/forms/progress'
import ContentState from '@/components/public/ContentState.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { discordHeaders, discordLogin, useDiscordSession } from '@/stores/discord'
import { canonicalAnswers, initialAnswers, type FormAnswers, type FormErrors, type FormField, type PublicForm } from '@/types/forms'

const props = defineProps<{ slug: string }>()
const discord = useDiscordSession()
const form = ref<PublicForm | null>(null)
const answers = ref<FormAnswers>({})
const loading = ref(true)
const submitting = ref(false)
const uploading = ref(0)
const busy = computed(() => submitting.value || uploading.value > 0)
const error = ref('')
const errors = ref<FormErrors>({})
const needsSignIn = ref(false)
const releaseChanged = ref(false)
const fields = ref<InstanceType<typeof FormExperience> | null>(null)
const errorSummary = ref<HTMLElement | null>(null)
const attachmentSubject = ref(crypto.randomUUID())
const submitFormVisible = computed(() => !!discord.account && !!form.value && !form.value.closed && !form.value.submitted)
const unsubmitted = computed(() => submitFormVisible.value && Object.values(answers.value).some(
  value => value !== '' && value !== false && value !== undefined,
))
let controller: AbortController

const uploader = createFormUploader(() => {
  const active = form.value
  if (!active || active.definition.anonymous) throw new Error('This form does not support account-linked uploads.')
  return {
    form_id: active.id,
    release_id: active.release_id,
    subject: attachmentSubject.value,
    max_bytes: active.attachment_policy.max_file_bytes,
  }
}, async () => {
  if (!discord.account) throw new Error('Sign in with Discord to attach a file.')
}, () => discordHeaders)

async function upload(file: File, field: FormField, path: string[]): Promise<string> {
  uploading.value += 1
  try { return await uploader(file, field, path) }
  finally { uploading.value -= 1 }
}

async function load(slug: string, signal?: AbortSignal) {
  loading.value = true
  error.value = ''
  errors.value = {}
  needsSignIn.value = false
  releaseChanged.value = false
  try {
    const [loaded] = await Promise.all([publicForm(slug, signal), discord.load()])
    signal?.throwIfAborted()
    form.value = loaded
    answers.value = initialAnswers(loaded.definition.fields)
    attachmentSubject.value = crypto.randomUUID()
  } catch (cause) {
    if (signal?.aborted) return
    form.value = null
    error.value = cause instanceof Error ? cause.message : 'This form could not be loaded.'
    needsSignIn.value = cause instanceof ApiError && cause.status === 401
  } finally {
    if (!signal?.aborted) loading.value = false
  }
}

watch(() => props.slug, (slug, _, cleanup) => {
  controller = new AbortController()
  const current = controller
  cleanup(() => current.abort())
  form.value = null
  answers.value = {}
  void load(slug, current.signal)
}, { immediate: true })

async function showError(message: string, issues: FormErrors = {}) {
  error.value = message
  errors.value = issues
  await nextTick()
  errorSummary.value?.focus()
}

async function submit() {
  const active = form.value
  if (!submitFormVisible.value || !active || busy.value) return
  const issues = requiredProgress(active.definition.fields, answers.value).errors
  if (Object.keys(issues).length) {
    await showError('A few required answers are missing.', issues)
    return
  }
  const signal = controller.signal
  submitting.value = true
  error.value = ''
  errors.value = {}
  try {
    const accepted = await submitForm(active.slug, active.release_id, canonicalAnswers(active.definition.fields, answers.value), attachmentSubject.value)
    if (!signal.aborted) {
      form.value = { ...active, submitted: accepted.submitted, my_response_id: accepted.response_id }
      answers.value = {}
    }
  } catch (cause) {
    if (signal.aborted) return
    if (cause instanceof ApiError && cause.status === 401) needsSignIn.value = true
    if (cause instanceof ApiError && cause.status === 409) releaseChanged.value = true
    await showError(cause instanceof Error ? cause.message : 'Your response could not be submitted.', cause instanceof ApiError ? cause.fieldErrors : {})
  } finally {
    submitting.value = false
  }
}

function reload() {
  if (!unsubmitted.value || window.confirm('Load the latest form and clear your unsaved answers?')) void load(props.slug, controller.signal)
}

function beforeUnload(event: BeforeUnloadEvent) {
  if (unsubmitted.value || busy.value) event.preventDefault()
}

window.addEventListener('beforeunload', beforeUnload)
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
const allowLeave = () => !unsubmitted.value || window.confirm('Leave this form? Your unsaved answers will be lost.')
onBeforeRouteLeave(allowLeave)
onBeforeRouteUpdate((to, from) => to.params.slug === from.params.slug || (!busy.value && allowLeave()))
</script>

<template>
  <div class="page-container page-container--narrow page-stack public-form" :aria-busy="loading">
    <nav class="toolbar" aria-label="Breadcrumb"><RouterLink :to="{ name: 'forms' }">Forms</RouterLink></nav>
    <ContentState v-if="loading" kind="loading" compact title="Loading form" />
    <template v-else-if="form">
      <form v-if="submitFormVisible" novalidate @submit.prevent="submit">
        <FormExperience ref="fields" v-model="answers" :definition="form.definition" :errors="errors"
          :upload="form.definition.anonymous ? undefined : upload" :max-upload-bytes="form.attachment_policy.max_file_bytes" :disabled="busy">
          <template #context>
            <p class="muted">{{ form.definition.anonymous ? 'Your answers are anonymous.' : 'Form admins can see which Discord account submitted your response.' }}</p>
            <p v-if="form.definition.discord_role_id" class="muted">A Discord role in the Cope server is required to submit.</p>
            <p v-if="form.definition.response_visibility === 'individual'" class="muted">Individual answers will be public after this form closes.</p>
          </template>
          <template #before-fields>
            <div v-if="error" ref="errorSummary" class="panel panel__body section-stack" tabindex="-1" role="alert">
              <strong class="text-danger">{{ error }}</strong>
              <ul v-if="Object.keys(errors).length" class="section-stack form-issues">
                <li v-for="(messages, path) in errors" :key="path"><button type="button" class="link" @click="fields?.focusField(String(path))">{{ messages.join(' ') }}</button></li>
              </ul>
              <div v-if="needsSignIn || releaseChanged" class="button-row">
                <BaseButton v-if="needsSignIn && discord.enabled" :href="discordLogin()">Sign in with Discord</BaseButton>
                <BaseButton v-if="releaseChanged" @click="reload">Reload latest form</BaseButton>
              </div>
            </div>
          </template>
          <template #footer>
            <div class="form-actions"><BaseButton type="submit" variant="primary" :loading="submitting" :disabled="busy">Submit response</BaseButton></div>
          </template>
        </FormExperience>
      </form>
      <template v-else>
        <PageHeader :title="form.definition.title" />
        <FormMarkdown v-if="form.definition.description" :source="form.definition.description" />
        <section v-if="form.submitted" class="panel panel__body section-stack" role="status">
          <h2>Response submitted</h2>
          <div v-if="form.my_response_id" class="button-row">
            <BaseButton :to="{ name: 'form-response', params: { id: form.my_response_id }, query: { form: form.id } }">Open your response</BaseButton>
          </div>
        </section>
        <p v-else-if="form.closed" class="muted">This form is closed to new responses.</p>
        <section v-else class="panel panel__body section-stack">
          <h2>Sign in to respond</h2>
          <p class="muted">Each Discord account can respond once.</p>
          <div v-if="discord.enabled" class="button-row">
            <BaseButton :href="discordLogin()" variant="primary">
              <template #icon><AppIcon name="discord" :size="17" /></template>
              Sign in with Discord
            </BaseButton>
          </div>
          <p v-else class="muted">Discord sign-in is currently unavailable.</p>
        </section>
      </template>
      <FormPublicResults v-if="form.definition.response_visibility && form.definition.response_visibility !== 'private'" :slug="form.slug" :fields="form.definition.fields" />
    </template>
    <template v-else-if="error">
      <ContentState kind="error" compact :message="error" action-label="Try again" @action="reload" />
      <div v-if="needsSignIn && discord.enabled" class="button-row"><BaseButton :href="discordLogin()">Sign in with Discord</BaseButton></div>
    </template>
  </div>
</template>

<style scoped>
.form-issues {
  padding-inline-start: var(--space-6);
  gap: var(--space-1);
}
</style>
