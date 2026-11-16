<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import StatusBadge from '@/components/admin/StatusBadge.vue'
import FormAdminNav from '@/components/forms/FormAdminNav.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { RouterLink, useRouter } from 'vue-router'
import { applicationQuery } from '@/api/application'
import { responseCollections, type ResponseCollection } from '@/api/forms'
import { useSessionStore } from '@/stores/session'
import { useApplicationMutation } from '@/composables/useApplicationMutation'
import type { FormDefinition, FormDetail, Page } from '@/types/forms'

const router = useRouter()
const session = useSessionStore()
const canEdit = computed(() => session.user?.role === 'admin')
const mutation = useApplicationMutation()
type CollectionRow = Pick<FormDetail, 'id' | 'label' | 'closed' | 'published_release'> | ResponseCollection

const rows = ref<CollectionRow[]>([])
const cursor = ref<string | null>(null)
const total = ref(0)
const loading = ref(true)
const error = ref('')
const label = ref('')
const slug = ref('')
const audience = ref<FormDefinition['audience']>('public')
const purpose = ref<'response_collection.v1' | 'feedback.v1'>('response_collection.v1')
async function load(more = false): Promise<void> {
  loading.value = true; error.value = ''
  try {
    const next = more ? cursor.value : null
    const page = canEdit.value
      ? await applicationQuery<Page<FormDetail>>('forms.list.v1', { limit: 30, cursor: next })
      : await responseCollections(next)
    rows.value = more ? [...rows.value, ...page.items] : page.items
    total.value = page.total; cursor.value = page.next_cursor
  } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Could not load forms.' }
  finally { loading.value = false }
}
async function create(): Promise<void> {
  error.value = ''
  try {
    const feedback = purpose.value === 'feedback.v1'
    const receipt = await mutation.execute<'forms.create.v1'>({
      command: 'forms.create.v1',
      input: {
        slug: slug.value,
        label: label.value,
        document: {
          title: label.value,
          audience: audience.value,
          binding: purpose.value,
          mapping: feedback ? { message: 'message' } : {},
          fields: [{ id: 'message', kind: 'long_text', label: feedback ? 'Message' : 'Response', required: true }],
        },
      },
    })
    await router.push(`/admin/forms/${receipt.output.form_id}?copy=${receipt.output.copy_id}`)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Could not create the form.' }
}
onMounted(() => load())
</script>

<template>
  <div class="admin-page page-stack">
    <AdminPageHeader title="Response collections" />
    <FormAdminNav v-if="canEdit" />
    <InlineFeedback :message="error" />
    <section class="panel" :aria-busy="loading">
      <header class="panel__header">
        <h2>Collections <small v-if="total" class="muted">{{ total.toLocaleString() }}</small></h2>
        <BaseButton size="small" variant="ghost" :loading="loading" @click="load()">Refresh</BaseButton>
      </header>
      <p v-if="loading && !rows.length" class="panel__body muted" role="status">Loading collections…</p>
      <div v-else-if="rows.length" class="table-wrap">
        <table class="data-table data-table--list" aria-label="Response collections">
          <thead><tr><th scope="col">Form</th><th scope="col">{{ canEdit ? 'Status' : 'Responses' }}</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
          <tbody>
            <tr v-for="form in rows" :key="form.id">
              <th scope="row"><RouterLink :to="{ name: 'admin-form-editor', params: { id: form.id }, query: canEdit ? {} : { view: 'responses' } }">{{ form.label }}</RouterLink></th>
              <td v-if="'response_count' in form">{{ form.response_count.toLocaleString() }}</td>
              <td v-else><StatusBadge :status="form.closed ? 'offline' : form.published_release ? 'published' : 'draft'" :label="form.closed ? 'Closed' : form.published_release ? 'Published' : 'Draft'" /></td>
              <td><BaseButton size="small" variant="ghost" :to="{ name: 'admin-form-editor', params: { id: form.id }, query: { view: 'responses' } }">Responses</BaseButton></td>
            </tr>
          </tbody>
        </table>
      </div>
      <AdminEmptyState v-else-if="!error" title="No response collections yet" />
      <div v-if="cursor" class="panel__body button-row">
        <BaseButton size="small" :loading="loading" @click="load(true)">Load more</BaseButton>
      </div>
    </section>
    <form v-if="canEdit" class="panel panel__body section-stack" @submit.prevent="create">
      <h2>Create a collection</h2>
      <fieldset class="form-grid collection-fields" :disabled="mutation.busy.value || mutation.uncertain.value">
        <label class="field"><span>Internal name</span><input v-model="label" class="input" required maxlength="300"></label>
        <label class="field"><span>Public link name</span><input v-model="slug" class="input" required maxlength="100" pattern="[a-z0-9]+(-[a-z0-9]+)*" placeholder="event-feedback"><small>Lowercase words separated by hyphens.</small></label>
        <label class="field"><span>Who can respond?</span><select v-model="audience" class="select"><option value="public">Anyone with the link</option><option value="authenticated">Signed-in users</option><option value="invited">Invited users</option></select></label>
        <label class="field"><span>Response purpose</span><select v-model="purpose" class="select"><option value="response_collection.v1">Collect responses</option><option value="feedback.v1">Feedback</option></select></label>
      </fieldset>
      <div class="form-actions">
        <BaseButton type="submit" variant="primary" :loading="mutation.busy.value">{{ mutation.uncertain.value ? 'Retry creation' : 'Create private draft' }}</BaseButton>
      </div>
    </form>
  </div>
</template>

<style scoped>
.collection-fields {
  min-width: 0;
  border: 0;
  margin: 0;
  padding: 0;
}
</style>
