<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { formResponses, type FormResponseRow } from '@/api/forms'
import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import StatusBadge from '@/components/admin/StatusBadge.vue'
import { errorText, formatDate } from '@/components/admin/format'
import BaseButton from '@/components/ui/BaseButton.vue'
import { useApplicationMutation } from '@/composables/useApplicationMutation'

const props = defineProps<{ formId: string }>()
const emit = defineEmits<{ title: [value: string] }>()
const route = useRoute()
const router = useRouter()
const showClosed = computed({
  get: () => route.query.show_closed === '1',
  set: value => { void router.replace({ query: { ...route.query, show_closed: value ? '1' : undefined } }) },
})
const mutation = useApplicationMutation()
const pendingId = ref('')
const items = ref<FormResponseRow[]>([])
const total = ref(0)
const cursor = ref<string | null>(null)
const loading = ref(true)
const error = ref('')
const message = ref('')

async function load(more = false) {
  loading.value = true
  error.value = ''
  try {
    const page = await formResponses(props.formId, showClosed.value, more ? cursor.value : null)
    items.value = more ? [...items.value, ...page.items] : page.items
    cursor.value = page.next_cursor
    total.value = page.total
    const title = items.value[0]?.form_title
    if (title) emit('title', title)
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    loading.value = false
  }
}

async function toggleClosed(item: FormResponseRow) {
  const closed = item.state !== 'closed'
  pendingId.value = item.id
  error.value = ''
  message.value = ''
  try {
    await mutation.execute<'forms.responses.close.v1'>({
      command: 'forms.responses.close.v1', target: item.id, expected_version: item.state_version,
      input: { closed, revision: item.revision },
    })
    message.value = closed ? 'Response closed.' : 'Response reopened.'
    items.value = []
    cursor.value = null
    await load()
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    if (!mutation.uncertain.value) pendingId.value = ''
  }
}

watch(showClosed, () => {
  items.value = []
  cursor.value = null
  void load()
}, { immediate: true })
</script>

<template>
  <section class="panel" aria-labelledby="collection-responses" :aria-busy="loading">
    <header class="panel__header">
      <div>
        <h2 id="collection-responses">Responses</h2>
        <small v-if="!error && (!loading || items.length)" class="muted">{{ total.toLocaleString() }} {{ total === 1 ? 'response' : 'responses' }}</small>
      </div>
      <div class="button-row">
        <label class="switch-row"><input v-model="showClosed" type="checkbox" :disabled="loading || mutation.busy.value || mutation.uncertain.value"> <span>Show closed</span></label>
        <BaseButton size="small" variant="ghost" :loading="loading" :disabled="mutation.busy.value || mutation.uncertain.value" @click="load()">Refresh</BaseButton>
      </div>
    </header>
    <div v-if="error" class="panel__body"><InlineFeedback :message="error" /></div>
    <div v-if="message" class="panel__body"><InlineFeedback :message="message" tone="info" /></div>
    <p v-if="loading && !items.length" class="panel__body muted" role="status">Loading responses…</p>
    <div v-else-if="items.length" class="table-wrap">
      <table class="data-table data-table--list" aria-labelledby="collection-responses">
        <thead><tr><th scope="col">Submitted</th><th scope="col">Status</th><th scope="col">Respondent</th><th scope="col">Release</th><th scope="col">Revision</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
        <tbody>
          <tr v-for="item in items" :key="item.id">
            <th scope="row"><time :datetime="item.created_at">{{ formatDate(item.created_at) }}</time></th>
            <td><StatusBadge :status="item.state" /></td>
            <td>{{ item.respondent ? '@' + item.respondent.username : item.anonymous ? 'Anonymous' : '—' }}</td>
            <td>{{ item.release_number }}</td>
            <td>{{ item.revision }}</td>
            <td>
              <div class="button-row">
                <BaseButton variant="ghost" size="small" :disabled="mutation.busy.value || mutation.uncertain.value" :to="{ query: { ...route.query, view: 'responses', response: item.id } }">View response</BaseButton>
                <BaseButton v-if="item.can_close" variant="ghost" size="small" :disabled="loading || mutation.busy.value || (mutation.uncertain.value && pendingId !== item.id)" @click="toggleClosed(item)">
                  {{ item.state === 'closed' ? 'Reopen' : 'Close' }}
                </BaseButton>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <AdminEmptyState v-else-if="!error" title="No responses yet" />
    <div v-if="cursor" class="panel__body button-row">
      <BaseButton size="small" :loading="loading" :disabled="mutation.busy.value || mutation.uncertain.value" @click="load(true)">Load more responses</BaseButton>
    </div>
  </section>
</template>
