<script setup lang="ts">
import BaseButton from '@/components/ui/BaseButton.vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, onBeforeRouteLeave, useRouter } from 'vue-router'
import { ApiError, api } from '@/api/client'
import { claimHeaders, publicForm, rememberClaim, storedClaim, type FormClaim } from '@/api/forms'
import { createFormUploader } from '@/api/form-uploads'
import FormFields from '@/components/forms/FormFields.vue'
import { useApplicationMutation } from '@/composables/useApplicationMutation'
import { useSessionStore } from '@/stores/session'
import { canonicalAnswers, initialAnswers, type FormAnswers, type FormErrors, type FormField, type PublicForm } from '@/types/forms'

const props = defineProps<{ slug: string }>()
const router = useRouter()
const session = useSessionStore()
const form = ref<PublicForm | null>(null)
const answers = ref<FormAnswers>({})
const claim = ref<FormClaim | null>(null)
const mutation = useApplicationMutation('/api/forms/commands', () => claimHeaders(claim.value?.token))
const loading = ref(true)
const error = ref('')
const errors = ref<FormErrors>({})
const errorSummary = ref<HTMLElement | null>(null)
const fields = ref<InstanceType<typeof FormFields> | null>(null)
const finished = ref(false)
const attachmentSubject = crypto.randomUUID()
const uploading = ref(0)
let claimRequest: Promise<void> | null = null
async function ensureClaim(): Promise<void> {
  if (session.authenticated || claim.value) return
  claimRequest ??= (async () => {
    claim.value = await api.post<FormClaim>(`/api/forms/${encodeURIComponent(props.slug)}/claims`)
    rememberClaim(claim.value)
  })().finally(() => { claimRequest = null })
  await claimRequest
}
const uploader = createFormUploader(() => {
  if (!form.value) throw new Error('Wait for the form to load.')
  return { form_id: form.value.id, release_id: form.value.release_id, subject: attachmentSubject, max_bytes: form.value.attachment_policy.max_file_bytes }
}, ensureClaim, () => claimHeaders(claim.value?.token))
async function upload(file: File, field: FormField, path: string[]): Promise<string> {
  uploading.value += 1
  try { return await uploader(file,field,path) } finally { uploading.value -= 1 }
}
const newerRelease = ref<PublicForm | null>(null)
const hasAnswers = computed(() => Object.values(answers.value).some((value) => value !== '' && value !== false && value !== undefined))

async function load(): Promise<void> {
  try { form.value = await publicForm(props.slug); answers.value = initialAnswers(form.value.definition.fields); claim.value = session.authenticated ? null : storedClaim(form.value.id) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : 'This form could not be loaded.' }
  finally { loading.value = false }
}
async function submit(): Promise<void> {
  if (!form.value || uploading.value) return
  error.value = ''; errors.value = {}
  try {
    await ensureClaim()
    const receipt = await mutation.execute<'forms.submit.v1'>({ command: 'forms.submit.v1', target: form.value.id, input: { release_id: form.value.release_id, attachment_subject: attachmentSubject, answers: canonicalAnswers(form.value.definition.fields, answers.value) } })
    finished.value = true
    await router.push({ name: 'form-response', params: { id: receipt.output.response_id }, query: { form: form.value.id } })
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Your response could not be submitted.'
    errors.value = cause instanceof ApiError ? cause.fieldErrors : {}
    await nextTick(); errorSummary.value?.focus()
  }
}
async function checkRelease(): Promise<void> {
  try {
    const current = await publicForm(props.slug)
    if (current.release_id !== form.value?.release_id) newerRelease.value = current
    else error.value = 'You are viewing the latest release. Your answers have been kept.'
  } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Could not check the current release.' }
}
function useLatest(): void {
  if (!newerRelease.value || !window.confirm('Open the updated form? Answers will be kept only for unchanged questions. Review all answers before submitting.')) return
  const oldFields = form.value?.definition.fields ?? []
  const kept: FormAnswers = {}
  for (const field of newerRelease.value.definition.fields) {
    if (oldFields.some((old) => old.id === field.id && JSON.stringify(old) === JSON.stringify(field)) && field.id in answers.value) kept[field.id] = answers.value[field.id]
  }
  form.value = newerRelease.value; newerRelease.value = null; answers.value = initialAnswers(form.value.definition.fields, kept); errors.value = {}; error.value = ''
}
function beforeUnload(event: BeforeUnloadEvent): void { if (!finished.value && (hasAnswers.value || mutation.uncertain.value || uploading.value)) { event.preventDefault(); event.returnValue = '' } }
onMounted(() => { window.addEventListener('beforeunload', beforeUnload); void load() })
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
onBeforeRouteLeave(() => finished.value || !(hasAnswers.value || mutation.uncertain.value || uploading.value) || window.confirm('Leave this form? Your unsubmitted answers will be lost.'))
</script>

<template>
  <main class="public-form" :aria-busy="loading"><p v-if="loading">Loading form…</p><template v-if="form"><header><p class="eyebrow">Form · release {{ form.release_number }}</p><h1>{{ form.definition.title }}</h1><p class="intro">{{ form.definition.description }}</p><p class="privacy">Your response is private to you and the people authorised to review it. Answers stay in this page until you submit.</p></header><form novalidate @submit.prevent="submit"><div v-if="error" ref="errorSummary" class="form-notice error" tabindex="-1" role="alert"><strong>{{ error }}</strong><ul v-if="Object.keys(errors).length"><li v-for="(messages, path) in errors" :key="path"><BaseButton type="button" variant="ghost" size="small" @click="fields?.focusField(String(path))">{{ messages.join(' ') }}</BaseButton></li></ul><BaseButton type="button" class="button button--ghost" :disabled="mutation.busy.value || mutation.uncertain.value || uploading > 0" @click="checkRelease" variant="ghost">Check for an updated form</BaseButton></div><div v-if="newerRelease" class="form-notice"><p>A newer release is available. Your current answers are still on this page.</p><BaseButton type="button" class="button" @click="useLatest">Review updated form</BaseButton></div><FormFields ref="fields" v-model="answers" :fields="form.definition.fields" :errors="errors" :upload="upload" :disabled="mutation.busy.value || mutation.uncertain.value || uploading > 0" /><p v-if="mutation.uncertain.value" class="form-notice" role="alert">The submission outcome is unconfirmed. Retry to recover the same response receipt.</p><BaseButton type="submit" class="button submit" :disabled="mutation.busy.value || uploading > 0">{{ mutation.uncertain.value ? 'Retry submission' : 'Submit response' }}</BaseButton><p v-if="!session.authenticated" class="privacy">A private follow-up credential will be saved for this browser tab. After submitting, download your access receipt to return from another browser.</p></form></template><div v-else-if="error" class="form-notice error" role="alert"><p>{{ error }}</p><RouterLink :to="{ name: 'admin-login', query: { redirect: `/forms/${slug}` } }">Sign in</RouterLink></div></main>
</template>

<style scoped>
.public-form{margin:2rem auto;max-width:44rem;padding:0 1rem}.public-form h1{font-size:2rem}.public-form form{display:grid;gap:1.4rem;margin-top:1.7rem}.intro{white-space:pre-wrap}.privacy{color:var(--color-text-muted,#64748b);font-size:.8rem;line-height:1.6}.form-notice{border:1px solid var(--color-border);border-radius:.6rem;font-size:.85rem;padding:1rem}.error{color:var(--color-danger,#b91c1c)}.submit{justify-self:start;min-width:11rem}
</style>
