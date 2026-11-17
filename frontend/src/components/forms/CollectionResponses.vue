<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { formResponses, type FormResponseRow } from '@/api/forms'
import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import StatusBadge from '@/components/admin/StatusBadge.vue'
import { errorText, formatDate } from '@/components/admin/format'
import BaseButton from '@/components/ui/BaseButton.vue'

const props = defineProps<{ formId: string }>()
const emit = defineEmits<{ title: [value: string] }>()
const route = useRoute()
const items = ref<FormResponseRow[]>([])
const total = ref(0)
const cursor = ref<string | null>(null)
const loading = ref(true)
const error = ref('')

async function load(more = false) {
  loading.value = true
  error.value = ''
  try {
    const page = await formResponses(props.formId, more ? cursor.value : null)
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

onMounted(() => load())
</script>

<template>
  <section class="panel" aria-labelledby="collection-responses" :aria-busy="loading">
    <header class="panel__header">
      <div>
        <h2 id="collection-responses">Responses</h2>
        <small v-if="!error && (!loading || items.length)" class="muted">{{ total.toLocaleString() }} {{ total === 1 ? 'response' : 'responses' }}</small>
      </div>
      <BaseButton size="small" variant="ghost" :loading="loading" @click="load()">Refresh</BaseButton>
    </header>
    <div v-if="error" class="panel__body"><InlineFeedback :message="error" /></div>
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
              <RouterLink class="button button--ghost button--small" :to="{ query: { ...route.query, view: 'responses', response: item.id } }">
                View response
              </RouterLink>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <AdminEmptyState v-else-if="!error" title="No responses yet" />
    <div v-if="cursor" class="panel__body button-row">
      <BaseButton size="small" :loading="loading" @click="load(true)">Load more responses</BaseButton>
    </div>
  </section>
</template>
