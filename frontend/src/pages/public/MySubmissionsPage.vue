<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { ApiError } from '@/api/client'
import { mySubmissions, type MySubmission } from '@/api/forms'
import StatusBadge from '@/components/admin/StatusBadge.vue'
import ContentState from '@/components/public/ContentState.vue'
import { formatDate } from '@/components/public/format'
import BaseButton from '@/components/ui/BaseButton.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { discordLogin, useDiscordSession } from '@/stores/discord'

const route = useRoute()
const discord = useDiscordSession()
const formId = computed(() => typeof route.query.form === 'string' ? route.query.form : undefined)
const items = ref<MySubmission[]>([])
const cursor = ref<string | null>(null)
const loading = ref(true)
const needsSignIn = ref(false)
const error = ref('')
let controller: AbortController

async function load(more = false) {
  const signal = controller.signal
  loading.value = true
  needsSignIn.value = false
  error.value = ''
  try {
    const page = await mySubmissions(formId.value, more ? cursor.value : null, signal)
    if (signal.aborted) return
    items.value = more ? [...items.value, ...page.items] : page.items
    cursor.value = page.next_cursor
  } catch (cause) {
    if (signal.aborted) return
    if (cause instanceof ApiError && cause.status === 401) {
      items.value = []
      cursor.value = null
      needsSignIn.value = true
      void discord.load().catch(() => {})
    } else {
      error.value = cause instanceof Error ? cause.message : 'Could not load your submissions.'
    }
  } finally {
    if (!signal.aborted) loading.value = false
  }
}

watch(formId, (_, __, cleanup) => {
  const current = new AbortController()
  controller = current
  cleanup(() => current.abort())
  items.value = []
  cursor.value = null
  void load()
}, { immediate: true })
</script>

<template>
  <div class="page-container page-stack" :aria-busy="loading">
    <PageHeader title="My submissions">
      <template v-if="formId && items[0]" #description>{{ items[0].form_title }}</template>
      <template #actions>
        <BaseButton v-if="formId" :to="{ name: 'my-submissions' }">All submissions</BaseButton>
        <BaseButton :to="{ name: 'forms' }">Forms</BaseButton>
      </template>
    </PageHeader>

    <section v-if="needsSignIn" class="panel panel__body section-stack">
      <h2>Sign in to see your submissions</h2>
      <div v-if="discord.enabled" class="button-row"><BaseButton :href="discordLogin()" variant="primary">Sign in with Discord</BaseButton></div>
      <p v-else-if="discord.loaded" class="muted">Discord sign-in is currently unavailable.</p>
    </section>
    <ContentState v-else-if="loading && !items.length" kind="loading" compact title="Loading submissions" />
    <ContentState v-else-if="!items.length && !error" kind="empty" compact :title="formId ? 'No submissions for this form' : 'No submissions yet'" />
    <ContentState v-if="error" kind="error" compact :message="error" action-label="Try again" @action="load()" />

    <section v-if="items.length" class="panel">
      <div class="table-wrap">
        <table class="data-table data-table--list" aria-label="My submissions">
          <thead>
            <tr><th scope="col">Form</th><th scope="col">Status</th><th scope="col">Submitted</th><th scope="col">Last updated</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in items" :key="item.id">
              <th scope="row">
                <RouterLink :to="{ name: 'form-response', params: { id: item.id }, query: { form: item.form_id } }">{{ item.form_title }}</RouterLink>
              </th>
              <td><StatusBadge :status="item.state" /></td>
              <td><time :datetime="item.created_at">{{ formatDate(item.created_at, true) }}</time></td>
              <td><time :datetime="item.updated_at">{{ formatDate(item.updated_at, true) }}</time></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="cursor && !error" class="panel__body button-row">
        <BaseButton size="small" :loading="loading" @click="load(true)">Load more submissions</BaseButton>
      </div>
    </section>
  </div>
</template>
