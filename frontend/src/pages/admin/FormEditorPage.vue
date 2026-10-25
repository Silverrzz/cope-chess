<script setup lang="ts">
import BaseButton from '@/components/ui/BaseButton.vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { applicationQuery, describeTarget } from '@/api/application'
import { ApiError } from '@/api/client'
import FormDefinitionEditor, { type BindingDescription } from '@/components/forms/FormDefinitionEditor.vue'
import FormFields from '@/components/forms/FormFields.vue'
import FormAccessEditor from '@/components/forms/FormAccessEditor.vue'
import { useApplicationMutation } from '@/composables/useApplicationMutation'
import { initialAnswers, type FormAnswers, type FormDefinition, type FormDetail, type FormErrors, type FormWorkingCopy } from '@/types/forms'

const props = defineProps<{ id: string }>()
const route = useRoute()
const router = useRouter()
const mutation = useApplicationMutation()
const form = ref<FormDetail | null>(null)
const copy = ref<FormWorkingCopy | null>(null)
const document = ref<FormDefinition>({ title: '', fields: [], audience: 'authenticated', binding: 'feedback.v1', mapping: {} })
const bindings = ref<BindingDescription[]>([])
const saved = ref('')
const previewAnswers = ref<FormAnswers>({})
const preview = ref(false)
const loading = ref(true)
const checking = ref(false)
const error = ref('')
const message = ref('')
const errors = ref<FormErrors>({})
const selectedCopy = ref('')
const previousHours = ref(0)
const publicationOpen = ref(false)
const archiveVersion = ref(0)
const dirty = computed(() => !!copy.value && JSON.stringify(document.value) !== saved.value)
const blocked = computed(() => mutation.busy.value || mutation.uncertain.value)
const stale = computed(() => !!form.value && !!copy.value && form.value.content_revision !== copy.value.basis_version)
const savedTitle = computed(() => { try { return (JSON.parse(saved.value) as FormDefinition).title } catch { return '' } })

function failure(cause: unknown): void {
  error.value = cause instanceof Error ? cause.message : 'The action could not be completed.'
  errors.value = cause instanceof ApiError ? cause.fieldErrors : {}
}
function clear(): void { error.value = ''; message.value = ''; errors.value = {} }
async function refreshForm(): Promise<void> { form.value = await applicationQuery<FormDetail>('forms.detail.v1', {}, props.id) }
async function openCopy(id: string): Promise<void> {
  const loaded = await applicationQuery<FormWorkingCopy>('forms.drafts.detail.v1', {}, id)
  copy.value = loaded; selectedCopy.value = id
  document.value = { title: '', fields: [], audience: 'authenticated', binding: 'feedback.v1', mapping: {}, ...structuredClone(loaded.document) }
  saved.value = JSON.stringify(document.value)
  previewAnswers.value = initialAnswers(document.value.fields)
  publicationOpen.value = false
  await router.replace({ query: { ...route.query, copy: id } })
}
async function selectCopy(): Promise<void> {
  if (dirty.value && !window.confirm('Discard your unsaved edits and open the selected private draft?')) { selectedCopy.value = copy.value?.object_id ?? ''; return }
  clear()
  try { await openCopy(selectedCopy.value) } catch (cause) { failure(cause) }
}
async function load(): Promise<void> {
  loading.value = true; clear()
  try {
    const catalogue = await applicationQuery<{ items: BindingDescription[] }>('forms.bindings.v1', {})
    bindings.value = catalogue.items
    await refreshForm()
    const requested = typeof route.query.copy === 'string' ? route.query.copy : ''
    const id = form.value?.copies.find((item) => item.id === requested && !item.archived)?.id ?? form.value?.copies.find((item) => !item.archived)?.id
    if (id) await openCopy(id)
  } catch (cause) { failure(cause) }
  finally { loading.value = false }
}
async function save(): Promise<void> {
  if (!copy.value) return
  clear()
  const snapshot = JSON.stringify(document.value)
  try {
    const receipt = await mutation.execute<'forms.drafts.save.v1'>({ command: 'forms.drafts.save.v1', target: copy.value.object_id, expected_version: copy.value.version, input: { document: JSON.parse(snapshot) } })
    copy.value = { ...copy.value, version: receipt.output.copy_revision }
    saved.value = snapshot
    message.value = `Saved private draft revision ${receipt.output.copy_revision}.`
    await refreshForm()
  } catch (cause) { failure(cause) }
}
async function newCopy(): Promise<void> {
  if (!form.value) return
  clear()
  try {
    const receipt = await mutation.execute<'forms.drafts.create.v1'>({ command: 'forms.drafts.create.v1', target: props.id, expected_version: form.value.content_revision, input: { document: copy.value ? JSON.parse(JSON.stringify(document.value)) : null } })
    await refreshForm(); await openCopy(receipt.output.copy_id)
    message.value = 'Created a private draft at the current form revision. Review its changes before publication.'
  } catch (cause) { failure(cause) }
}
async function check(): Promise<void> {
  clear(); checking.value = true
  try { await applicationQuery('forms.definition.check.v1', { document: JSON.parse(JSON.stringify(document.value)) }); message.value = 'The current definition passes publication validation. Save it to select this content for publication.' }
  catch (cause) { failure(cause) }
  finally { checking.value = false }
}
async function publish(): Promise<void> {
  if (!form.value || !copy.value) return
  clear()
  try {
    const receipt = await mutation.execute<'forms.publish.v1'>({ command: 'forms.publish.v1', target: props.id, expected_version: form.value.content_revision, input: { copy_id: copy.value.object_id, copy_revision: copy.value.version, state_version: form.value.state_version, keep_previous_hours: previousHours.value } })
    publicationOpen.value = false
    await refreshForm()
    message.value = `Published release ${receipt.output.release_number} from saved draft revision ${copy.value.version}.`
  } catch (cause) { failure(cause) }
}
async function toggleClosed(): Promise<void> {
  if (!form.value) return
  clear()
  try {
    await mutation.execute<'forms.close.v1'>({ command: 'forms.close.v1', target: props.id, expected_version: form.value.state_version, input: { closed: !form.value.closed } })
    await refreshForm()
  } catch (cause) { failure(cause) }
}
async function archive(): Promise<void> {
  if (!copy.value) return
  if (dirty.value && !window.confirm('Archive this draft and discard your unsaved edits?')) return
  clear()
  try {
    if (!mutation.uncertain.value) archiveVersion.value = (await describeTarget('forms.drafts.archive.v1', copy.value.object_id)).version
    await mutation.execute<'forms.drafts.archive.v1'>({ command: 'forms.drafts.archive.v1', target: copy.value.object_id, expected_version: archiveVersion.value, input: { archived: true } })
    copy.value = null; saved.value = ''; await refreshForm()
    message.value = 'Private draft archived. Its saved history is retained.'
  } catch (cause) { failure(cause) }
}
function beforeUnload(event: BeforeUnloadEvent): void { if (dirty.value || mutation.uncertain.value) { event.preventDefault(); event.returnValue = '' } }
onMounted(() => { window.addEventListener('beforeunload', beforeUnload); void load() })
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
onBeforeRouteLeave(() => !(dirty.value || mutation.uncertain.value) || window.confirm('Leave this editor? Unsaved edits or an unconfirmed action may remain.'))
</script>

<template>
  <div class="form-editor-page" :aria-busy="loading">
    <header><RouterLink to="/admin/forms">← Forms</RouterLink><h1>{{ form?.label ?? 'Form editor' }}</h1><div v-if="form" class="toolbar"><span>{{ form.closed ? 'Closed' : form.published_release ? 'Published' : 'Draft only' }}</span><RouterLink v-if="form.published_release" :to="`/forms/${form.slug}`">Open published form</RouterLink><RouterLink :to="`/admin/form-responses?form=${form.id}`">Response inbox</RouterLink><BaseButton type="button" class="button button--ghost" :disabled="mutation.unavailable('forms.close.v1')" @click="toggleClosed" variant="ghost">{{ form.closed ? 'Reopen submissions' : 'Close submissions' }}</BaseButton></div></header>
    <div v-if="error" class="notice error" role="alert" tabindex="-1"><strong>{{ error }}</strong><ul v-if="Object.keys(errors).length"><li v-for="(messages, path) in errors" :key="path">{{ path }}: {{ messages.join(' ') }}</li></ul></div><p v-if="message" class="notice" role="status">{{ message }}</p>
    <p v-if="mutation.uncertain.value" class="notice" role="alert">The action’s outcome is unconfirmed. Retry the same action to recover its receipt before editing further.</p>
    <template v-if="form && !loading">
      <section class="draft-toolbar"><label>Your private draft<select v-model="selectedCopy" class="input" :disabled="blocked" @change="selectCopy"><option value="" disabled>Select a draft</option><option v-for="item in form.copies.filter((item) => !item.archived)" :key="item.id" :value="item.id">Saved revision {{ item.current_revision }} · {{ new Date(item.updated_at).toLocaleString() }}</option></select></label><BaseButton type="button" class="button button--ghost" :disabled="mutation.unavailable('forms.drafts.create.v1')" @click="newCopy" variant="ghost">{{ stale ? 'Reconcile into a new draft' : 'Create another private draft' }}</BaseButton></section>
      <template v-if="copy"><p v-if="stale" class="notice">This draft began before the latest publication. Your edits are preserved. Create a new draft to reconcile them with the current form before publishing.</p><div class="toolbar"><strong>{{ dirty ? 'Unsaved changes' : `Saved revision ${copy.version}` }}</strong><BaseButton type="button" class="button" :disabled="mutation.unavailable('forms.drafts.save.v1')" @click="save">Save private draft</BaseButton><BaseButton type="button" class="button button--ghost" :disabled="blocked || checking" @click="check" variant="ghost">Check definition</BaseButton><BaseButton type="button" class="button button--ghost" :disabled="blocked" @click="preview = !preview" variant="ghost">{{ preview ? 'Edit questions' : 'Preview form' }}</BaseButton><BaseButton type="button" class="button button--ghost" :disabled="mutation.busy.value || stale" @click="publicationOpen = !publicationOpen" variant="ghost">Publish saved revision</BaseButton><BaseButton type="button" class="button button--ghost" :disabled="mutation.unavailable('forms.drafts.archive.v1')" @click="archive" variant="ghost">Archive draft</BaseButton></div>
        <section v-if="publicationOpen" class="publication"><h2>Publish “{{ savedTitle }}”</h2><p>This publishes saved draft revision {{ copy.version }}.{{ dirty ? ' Your unsaved edits stay in the editor and will not be included.' : '' }} It opens submissions on the new release.</p><label>Keep previous releases open<select v-model="previousHours" class="input" :disabled="blocked"><option :value="0">Close them immediately</option><option :value="1">For one hour</option><option :value="24">For one day</option><option :value="168">For seven days</option></select></label><BaseButton type="submit" class="button" :disabled="mutation.unavailable('forms.publish.v1')" @click="publish">Publish saved revision {{ copy.version }}</BaseButton></section>
        <section v-if="preview" class="preview"><p class="eyebrow">Preview · answers stay in this editor</p><h2>{{ document.title }}</h2><p class="intro">{{ document.description }}</p><FormFields v-model="previewAnswers" :fields="document.fields" :disabled="blocked" /></section><FormDefinitionEditor v-else v-model="document" :bindings="bindings" :errors="errors" :disabled="blocked" />
      </template>
      <section class="releases"><h2>Published releases</h2><p v-if="!form.releases.length">No release has been published.</p><ul><li v-for="release in form.releases" :key="release.id"><strong>Release {{ release.number }} · {{ release.definition.title }}</strong><span>{{ release.accepting && (!release.valid_until || Date.parse(release.valid_until) > Date.now()) && !form.closed ? 'Accepting submissions' : 'Closed to submissions' }}{{ release.valid_until ? ` · until ${new Date(release.valid_until).toLocaleString()}` : '' }}</span></li></ul></section>
      <FormAccessEditor :form-id="form.id" :disabled="blocked" @changed="refreshForm" />
    </template>
  </div>
</template>

<style scoped>
.form-editor-page{display:grid;gap:1.3rem;max-width:62rem}.form-editor-page h1{font-size:1.8rem}.toolbar{align-items:center;display:flex;flex-wrap:wrap;gap:.6rem}.toolbar>strong{flex:1;font-size:.85rem}.toolbar>span{font-size:.8rem}.draft-toolbar{align-items:end;display:flex;gap:1rem}.draft-toolbar label{display:grid;flex:1;gap:.4rem;font-size:.8rem}.notice,.publication{border:1px solid var(--color-border);border-radius:.6rem;padding:1rem}.notice{font-size:.85rem;margin:0}.error{color:var(--color-danger,#b91c1c)}.publication{display:grid;gap:.75rem}.publication h2,.publication p{margin:0}.publication label{display:grid;font-size:.85rem;gap:.4rem}.publication h2,.releases h2{font-size:1rem}.publication .button{justify-self:start}.preview{border:1px solid var(--color-border);border-radius:.8rem;padding:1.5rem}.intro{white-space:pre-wrap}.releases ul{list-style:none;padding:0}.releases li{border-top:1px solid var(--color-border);display:grid;font-size:.8rem;gap:.35rem;padding:.8rem 0}@media(max-width:42rem){.draft-toolbar{align-items:stretch;flex-direction:column}}
</style>
