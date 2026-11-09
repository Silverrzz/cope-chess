<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { applicationQuery, describeTarget } from '@/api/application'
import { ApiError } from '@/api/client'
import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import StatusBadge from '@/components/admin/StatusBadge.vue'
import { formatDate } from '@/components/admin/format'
import FormAccessEditor from '@/components/forms/FormAccessEditor.vue'
import FormDefinitionEditor, { type BindingDescription } from '@/components/forms/FormDefinitionEditor.vue'
import FormExperience from '@/components/forms/FormExperience.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { useApplicationMutation } from '@/composables/useApplicationMutation'
import { useConfirm } from '@/composables/useConfirm'
import { canonicalAnswers, contentKinds, initialAnswers, type FormAnswers, type FormDefinition, type FormDetail, type FormErrors, type FormWorkingCopy } from '@/types/forms'

const props = defineProps<{ id: string }>()
const route = useRoute()
const router = useRouter()
const mutation = useApplicationMutation()
const { confirm } = useConfirm()
type View = 'published' | 'draft' | 'preview' | 'settings'
type Draft = FormDetail['copies'][number]

const view = ref<View>('published')
const previewSource = ref<'published' | 'draft'>('published')
const form = ref<FormDetail | null>(null)
const copy = ref<FormWorkingCopy | null>(null)
const olderLink = ref<Draft | null>(null)
const document = ref<FormDefinition>({ title: '', fields: [], audience: 'authenticated', binding: 'feedback.v1', mapping: {} })
const bindings = ref<BindingDescription[]>([])
const saved = ref('')
const loading = ref(true)
const opening = ref(false)
const error = ref('')
const message = ref('')
const errors = ref<FormErrors>({})
const publicationOpen = ref(false)
const previousHours = ref(0)
const archiveVersion = ref(0)
const previewAnswers = ref<FormAnswers>({})
const previewErrors = ref<FormErrors>({})
const previewStatus = ref('')
const narrowPreview = ref(false)
const checkingPreview = ref(false)
const previewUpload = async () => crypto.randomUUID()

const live = computed(() => form.value?.releases.find(release => release.id === form.value?.published_release))
const questions = computed(() => live.value?.definition.fields.filter(field => !contentKinds.has(field.kind)) ?? [])
const activeDrafts = computed(() => form.value?.copies.filter(item => !item.archived) ?? [])
const currentDraft = computed(() => activeDrafts.value.find(item => item.basis_version === form.value?.content_revision))
const previewDefinition = computed(() => previewSource.value === 'draft' ? document.value : live.value?.definition)
const dirty = computed(() => !!copy.value && JSON.stringify(document.value) !== saved.value)
const blocked = computed(() => opening.value || mutation.busy.value || mutation.uncertain.value)
const stale = computed(() => !!form.value && !!copy.value && copy.value.basis_version !== form.value.content_revision)

function failure(cause: unknown): void {
  error.value = cause instanceof Error ? cause.message : 'The action could not be completed.'
  errors.value = cause instanceof ApiError ? cause.fieldErrors : {}
}

function clear(): void {
  error.value = ''
  message.value = ''
  errors.value = {}
}

async function refreshForm(): Promise<void> {
  form.value = await applicationQuery<FormDetail>('forms.detail.v1', {}, props.id)
}

async function replaceCopyQuery(id?: string): Promise<void> {
  const query = { ...route.query }
  if (id) query.copy = id
  else delete query.copy
  await router.replace({ query })
}

async function openCopy(id: string): Promise<void> {
  opening.value = true
  try {
    const loaded = await applicationQuery<FormWorkingCopy>('forms.drafts.detail.v1', {}, id)
    if (loaded.form_id !== props.id || loaded.archived) throw new Error('This draft is unavailable.')
    copy.value = loaded
    document.value = { title: '', fields: [], audience: 'authenticated', binding: 'feedback.v1', mapping: {}, ...structuredClone(loaded.document) }
    saved.value = JSON.stringify(document.value)
    publicationOpen.value = false
    view.value = 'draft'
    await replaceCopyQuery(id)
  } finally {
    opening.value = false
  }
}

async function selectDraft(id: string): Promise<void> {
  if (!id || blocked.value || id === copy.value?.object_id) return
  if (dirty.value && !await confirm({ title: 'Open another draft?', message: 'Save this draft first if you want to keep its unsaved changes.', confirmLabel: 'Discard changes' })) return
  clear()
  try { await openCopy(id) }
  catch (cause) { failure(cause) }
}

function changeDraft(event: Event): void {
  const select = event.target as HTMLSelectElement
  const id = select.value
  select.value = copy.value?.object_id ?? ''
  void selectDraft(id)
}

async function load(): Promise<void> {
  loading.value = true
  clear()
  copy.value = null
  olderLink.value = null
  view.value = 'published'
  try {
    const [catalogue] = await Promise.all([
      applicationQuery<{ items: BindingDescription[] }>('forms.bindings.v1', {}),
      refreshForm(),
    ])
    bindings.value = catalogue.items
    const requested = typeof route.query.copy === 'string' ? route.query.copy : ''
    const selected = activeDrafts.value.find(item => item.id === requested)
    if (selected && (!live.value || selected.basis_version === form.value?.content_revision)) {
      await openCopy(selected.id)
    } else if (live.value) {
      if (selected) olderLink.value = selected
      if (requested) await replaceCopyQuery()
    } else if (currentDraft.value) {
      await openCopy(currentDraft.value.id)
    } else if (requested) {
      await replaceCopyQuery()
    }
  } catch (cause) { failure(cause) }
  finally { loading.value = false }
}

async function editCurrent(): Promise<void> {
  if (!form.value) return
  clear()
  if (copy.value && !stale.value) {
    view.value = 'draft'
    return
  }
  if (dirty.value && !mutation.uncertain.value && !await confirm({
    title: 'Open the current form?',
    message: 'Save the older draft first if you want to keep its unsaved changes.',
    confirmLabel: 'Discard changes',
  })) return
  try {
    if (currentDraft.value && !mutation.uncertain.value) {
      await openCopy(currentDraft.value.id)
    } else {
      await createDraft(null)
    }
    olderLink.value = null
  } catch (cause) { failure(cause) }
}

async function createDraft(source: FormDefinition | null): Promise<void> {
  if (!form.value) return
  const receipt = await mutation.execute<'forms.drafts.create.v1'>({
    command: 'forms.drafts.create.v1', target: props.id, expected_version: form.value.content_revision,
    input: { document: source ? JSON.parse(JSON.stringify(source)) : null },
  })
  await refreshForm()
  await openCopy(receipt.output.copy_id)
}

async function duplicateDraft(): Promise<void> {
  if (!copy.value) return
  if (stale.value && !mutation.uncertain.value && !await confirm({
    title: 'Copy the older questions?',
    message: 'This creates a separate draft with the older questions. Review it against the published form before publishing.',
    confirmLabel: 'Create separate draft',
  })) return
  clear()
  try {
    await createDraft(document.value)
    message.value = 'New private draft created.'
  } catch (cause) { failure(cause) }
}

async function save(): Promise<void> {
  if (!copy.value) return
  clear()
  const snapshot = JSON.stringify(document.value)
  try {
    const receipt = await mutation.execute<'forms.drafts.save.v1'>({
      command: 'forms.drafts.save.v1', target: copy.value.object_id, expected_version: copy.value.version,
      input: { document: JSON.parse(snapshot) },
    })
    copy.value = { ...copy.value, version: receipt.output.copy_revision }
    saved.value = snapshot
    await refreshForm()
    message.value = 'Draft saved.'
  } catch (cause) { failure(cause) }
}

function showPreview(source: 'draft' | 'published'): void {
  previewSource.value = source
  const definition = previewDefinition.value
  if (!definition) return
  previewAnswers.value = initialAnswers(definition.fields)
  previewErrors.value = {}
  previewStatus.value = ''
  view.value = 'preview'
}

async function validatePreview(): Promise<void> {
  const definition = previewDefinition.value
  if (!definition || checkingPreview.value) return
  checkingPreview.value = true
  previewErrors.value = {}
  previewStatus.value = ''
  try {
    await applicationQuery('forms.preview.check.v1', {
      document: JSON.parse(JSON.stringify(definition)),
      answers: canonicalAnswers(definition.fields, previewAnswers.value),
    })
    previewStatus.value = 'Preview answers pass validation.'
  } catch (cause) {
    previewErrors.value = cause instanceof ApiError ? cause.fieldErrors : {}
    previewStatus.value = cause instanceof Error ? cause.message : 'Could not validate the preview.'
  } finally { checkingPreview.value = false }
}

async function publish(): Promise<void> {
  if (!form.value || !copy.value || stale.value || dirty.value) return
  clear()
  try {
    const receipt = await mutation.execute<'forms.publish.v1'>({
      command: 'forms.publish.v1', target: props.id, expected_version: form.value.content_revision,
      input: { copy_id: copy.value.object_id, copy_revision: copy.value.version, state_version: form.value.state_version, keep_previous_hours: previousHours.value },
    })
    publicationOpen.value = false
    await refreshForm()
    copy.value = null
    olderLink.value = null
    view.value = 'published'
    await replaceCopyQuery()
    message.value = `Release ${receipt.output.release_number} published.`
  } catch (cause) { failure(cause) }
}

async function toggleClosed(): Promise<void> {
  if (!form.value) return
  clear()
  try {
    await mutation.execute<'forms.close.v1'>({ command: 'forms.close.v1', target: props.id, expected_version: form.value.state_version, input: { closed: !form.value.closed } })
    await refreshForm()
    message.value = form.value?.closed ? 'Submissions closed.' : 'Submissions reopened.'
  } catch (cause) { failure(cause) }
}

async function archive(): Promise<void> {
  if (!copy.value) return
  if (!mutation.uncertain.value && !await confirm({
    title: 'Archive this draft?',
    message: dirty.value ? 'The saved draft will be archived and unsaved changes discarded.' : 'The saved draft will be kept in the form’s history.',
    confirmLabel: 'Archive draft',
  })) return
  clear()
  try {
    if (!mutation.uncertain.value) archiveVersion.value = (await describeTarget('forms.drafts.archive.v1', copy.value.object_id)).version
    await mutation.execute<'forms.drafts.archive.v1'>({
      command: 'forms.drafts.archive.v1', target: copy.value.object_id, expected_version: archiveVersion.value, input: { archived: true },
    })
    copy.value = null
    olderLink.value = null
    saved.value = ''
    view.value = 'published'
    await refreshForm()
    await replaceCopyQuery()
    message.value = 'Draft archived.'
  } catch (cause) { failure(cause) }
}

function beforeUnload(event: BeforeUnloadEvent): void {
  if (dirty.value || mutation.uncertain.value) { event.preventDefault(); event.returnValue = '' }
}
const allowLeave = () => !(dirty.value || mutation.uncertain.value) || window.confirm('Leave this form? Unsaved changes may be lost.')
watch(() => props.id, load, { immediate: true })
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
onBeforeRouteLeave(allowLeave)
onBeforeRouteUpdate((to, from) => to.params.id === from.params.id || (!loading.value && !blocked.value && allowLeave()))
</script>

<template>
  <div class="admin-page page-stack form-editor-page" :aria-busy="loading || opening">
    <nav class="form-breadcrumbs" aria-label="Breadcrumb"><RouterLink to="/admin/forms">Forms</RouterLink><span aria-hidden="true">/</span><span aria-current="page">{{ form?.label ?? 'Form' }}</span></nav>
    <AdminPageHeader :title="live?.definition.title ?? form?.label ?? 'Form editor'">
      <template v-if="form" #actions>
        <BaseButton :to="`/admin/form-responses?form=${form.id}`" variant="secondary">Responses</BaseButton>
        <BaseButton v-if="live" :to="`/forms/${form.slug}`" variant="secondary">Open live form</BaseButton>
      </template>
    </AdminPageHeader>
    <InlineFeedback :message="error" />
    <InlineFeedback :message="message" tone="info" />
    <InlineFeedback v-if="mutation.uncertain.value" message="Retry the same action to recover its receipt." tone="info" />
    <ul v-if="Object.keys(errors).length" class="form-errors"><li v-for="(messages, path) in errors" :key="path">{{ messages.join(' ') }}</li></ul>
    <div v-if="loading" class="panel panel__body" role="status">Loading form…</div>
    <template v-else-if="form">
      <nav class="form-tabs" aria-label="Form workspace">
        <button type="button" :aria-current="view === 'published' ? 'page' : undefined" :disabled="blocked" @click="view = 'published'">Published</button>
        <button type="button" :aria-current="view === 'draft' ? 'page' : undefined" :disabled="blocked" @click="editCurrent">Edit</button>
        <button type="button" :aria-current="view === 'preview' ? 'page' : undefined" :disabled="blocked || (!live && !copy)" @click="showPreview(!live || (view === 'draft' && copy) ? 'draft' : 'published')">Preview</button>
        <button type="button" :aria-current="view === 'settings' ? 'page' : undefined" :disabled="blocked" @click="view = 'settings'">Settings</button>
      </nav>

      <template v-if="view === 'published'">
        <div v-if="olderLink" class="panel panel__body form-panel linked-draft">
          <div><strong>This link opened an older draft</strong><p>You’re viewing the current published form. The older draft is still available in your drafts.</p></div>
          <BaseButton size="small" variant="secondary" :disabled="blocked" @click="selectDraft(olderLink.id)">Open older draft</BaseButton>
        </div>
        <section v-if="live" class="panel panel__body form-panel">
          <header class="form-panel-heading">
            <div><h2>Published form</h2><p>Release {{ live.number }} · {{ questions.length }} {{ questions.length === 1 ? 'question' : 'questions' }}</p></div>
            <div class="form-actions">
              <StatusBadge :status="form.closed ? 'offline' : 'published'" :label="form.closed ? 'Submissions closed' : 'Published'" />
              <BaseButton variant="primary" :disabled="mutation.unavailable('forms.drafts.create.v1')" @click="editCurrent">Edit current form</BaseButton>
            </div>
          </header>
          <p v-if="live.definition.description" class="form-introduction">{{ live.definition.description }}</p>
          <ol class="published-questions">
            <li v-for="field in questions" :key="field.id">
              <strong>{{ field.label }}</strong>
              <small v-if="field.required">Required</small>
              <ul v-if="field.choices?.length"><li v-for="choice in field.choices" :key="choice.id">{{ choice.label }}</li></ul>
            </li>
          </ol>
        </section>
        <section v-else class="panel">
          <AdminEmptyState title="No published form yet">
            <BaseButton variant="primary" :disabled="mutation.unavailable('forms.drafts.create.v1')" @click="editCurrent">Open draft</BaseButton>
          </AdminEmptyState>
        </section>
      </template>

      <template v-else-if="view === 'draft' && copy">
        <section class="panel panel__body form-panel">
          <header class="form-panel-heading">
            <div><h2>Private draft</h2><p>{{ dirty ? 'Unsaved changes' : `Saved revision ${copy.version}` }}</p></div>
            <div class="form-actions">
              <BaseButton variant="secondary" :disabled="(!dirty && !mutation.uncertain.value) || mutation.unavailable('forms.drafts.save.v1')" @click="save">Save draft</BaseButton>
              <BaseButton variant="primary" :disabled="dirty || stale || mutation.unavailable('forms.publish.v1')" @click="publicationOpen = true">Publish…</BaseButton>
            </div>
          </header>
          <div v-if="stale" class="linked-draft">
            <InlineFeedback message="This draft is based on an older release. Edit the current form to publish new changes." tone="info" />
            <BaseButton :disabled="mutation.unavailable('forms.drafts.create.v1')" @click="editCurrent">Edit current form</BaseButton>
          </div>
          <FormDefinitionEditor v-model="document" :bindings="bindings" :errors="errors" :disabled="blocked" />
        </section>
        <section v-if="publicationOpen" class="panel panel__body form-panel publish-panel">
          <div><h2>Publish this draft?</h2><p>The live form will use the questions in saved revision {{ copy.version }}.</p></div>
          <label>Previous releases<select v-model="previousHours" class="input" :disabled="blocked">
            <option :value="0">Close immediately</option><option :value="1">Keep open for one hour</option>
            <option :value="24">Keep open for one day</option><option :value="168">Keep open for seven days</option>
          </select></label>
          <div class="form-actions"><BaseButton variant="ghost" :disabled="blocked" @click="publicationOpen = false">Cancel</BaseButton><BaseButton variant="primary" :disabled="dirty || stale || mutation.unavailable('forms.publish.v1')" @click="publish">{{ mutation.uncertain.value ? 'Retry publication' : 'Publish form' }}</BaseButton></div>
        </section>
      </template>

      <section v-else-if="view === 'preview' && previewDefinition" class="panel panel__body form-panel">
        <header class="form-panel-heading">
          <div><h2>Preview</h2><p>{{ previewSource === 'draft' ? 'Private draft' : `Published release ${live?.number ?? ''}` }}</p></div>
          <div class="form-actions">
            <label v-if="copy && live">Source<select class="input" :value="previewSource" @change="showPreview(($event.target as HTMLSelectElement).value === 'draft' ? 'draft' : 'published')"><option value="published">Published</option><option value="draft">Draft</option></select></label>
            <label class="mobile-preview"><input v-model="narrowPreview" type="checkbox"> Mobile width</label>
          </div>
        </header>
        <div class="preview-surface" :class="{ 'preview-surface--narrow': narrowPreview }">
          <FormExperience v-model="previewAnswers" :definition="previewDefinition" :errors="previewErrors" :disabled="checkingPreview" :upload="previewUpload" preview>
            <template #footer>
              <InlineFeedback :message="previewStatus" :tone="Object.keys(previewErrors).length ? 'error' : 'info'" />
              <div class="form-actions">
                <BaseButton variant="ghost" :disabled="checkingPreview" @click="showPreview(previewSource)">Reset</BaseButton>
                <BaseButton variant="secondary" :loading="checkingPreview" @click="validatePreview">Check answers</BaseButton>
              </div>
            </template>
          </FormExperience>
        </div>
      </section>

      <template v-else-if="view === 'settings'">
        <section v-if="live" class="panel panel__body form-panel">
          <header class="form-panel-heading">
            <div><h2>Submissions</h2><p>{{ form.closed ? 'This form is closed.' : 'The live form is accepting responses.' }}</p></div>
            <BaseButton :disabled="mutation.unavailable('forms.close.v1')" @click="toggleClosed">{{ form.closed ? 'Reopen submissions' : 'Close submissions' }}</BaseButton>
          </header>
        </section>
        <section v-if="activeDrafts.length" class="panel panel__body form-panel">
          <header class="form-panel-heading"><div><h2>Your drafts</h2><p>Drafts are private to your account.</p></div></header>
          <div class="form-actions">
            <label>Open draft<select class="input" :value="copy?.object_id ?? ''" :disabled="blocked" @change="changeDraft">
              <option value="" disabled>Choose a saved draft</option>
              <option v-for="item in activeDrafts" :key="item.id" :value="item.id">{{ item.basis_version === form.content_revision ? 'Current basis' : 'Older release' }} · {{ formatDate(item.updated_at) }}</option>
            </select></label>
            <BaseButton v-if="copy" variant="secondary" :disabled="mutation.unavailable('forms.drafts.create.v1')" @click="duplicateDraft">Copy to new draft</BaseButton>
            <BaseButton v-if="copy" variant="ghost" :disabled="mutation.unavailable('forms.drafts.archive.v1')" @click="archive">Archive open draft</BaseButton>
          </div>
        </section>
        <section v-if="form.releases.length" class="panel panel__body form-panel">
          <h2>Release history</h2>
          <ul class="release-list"><li v-for="release in form.releases" :key="release.id">
            <div><strong>Release {{ release.number }}</strong><small>{{ formatDate(release.created_at) }}</small></div>
            <StatusBadge :status="release.id === form.published_release ? 'published' : 'idle'" :label="release.id === form.published_release ? 'Current' : 'Previous'" />
          </li></ul>
        </section>
        <FormAccessEditor :form-id="form.id" :disabled="blocked" @changed="refreshForm" />
      </template>
    </template>
  </div>
</template>

<style scoped>
.form-breadcrumbs, .form-tabs, .form-actions { display: flex; align-items: center; flex-wrap: wrap; gap: .65rem; }
.form-breadcrumbs { color: var(--color-text-muted); font-size: .85rem; }
.form-tabs { gap: .25rem; border-bottom: 1px solid var(--color-border); }
.form-tabs button { background: transparent; border: 0; border-bottom: 2px solid transparent; color: var(--color-text-muted); cursor: pointer; font: inherit; padding: .65rem .85rem; }
.form-tabs button[aria-current] { border-color: var(--color-accent); color: var(--color-text); font-weight: 650; }
.form-tabs button:disabled { opacity: .55; cursor: default; }
.form-panel { display: grid; gap: 1rem; }
.form-panel h2 { font-size: 1.1rem; margin: 0; }
.form-panel p { margin: .25rem 0 0; color: var(--color-text-muted); }
.form-panel-heading, .linked-draft { display: flex; justify-content: space-between; align-items: center; gap: .9rem; flex-wrap: wrap; }
.form-panel-heading { border-bottom: 1px solid var(--color-border); padding-bottom: 1rem; }
.form-panel-heading .form-actions { margin-inline-start: auto; }
.form-actions label, .publish-panel label { display: grid; gap: .35rem; font-size: .85rem; }
.form-actions .mobile-preview { display: flex; align-items: center; }
.published-questions, .release-list { margin: 0; padding-inline-start: 1.25rem; }
.published-questions > li { padding-block: .85rem; }
.published-questions > li + li, .release-list > li + li { border-top: 1px solid var(--color-border); }
.published-questions small { color: var(--color-text-muted); margin-inline-start: .65rem; }
.published-questions ul { margin: .5rem 0 0; padding-inline-start: 1.25rem; color: var(--color-text-secondary); }
.form-introduction { white-space: pre-wrap; }
.preview-surface { width: min(100%, 48rem); margin-inline: auto; }
.preview-surface--narrow { width: min(100%, 24rem); }
.publish-panel { max-width: 32rem; }
.release-list { padding: 0; list-style: none; }
.release-list li { display: flex; justify-content: space-between; gap: .75rem; padding-block: .75rem; }
.release-list small { margin-inline-start: .65rem; color: var(--color-text-muted); }
@media (max-width: 42rem) { .form-panel-heading .form-actions { margin-inline-start: 0; } }
</style>
