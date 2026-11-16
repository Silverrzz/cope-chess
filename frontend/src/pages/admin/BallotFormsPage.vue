<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ballotApi, errorText, type AdminSummary, type Page } from '@/api/ballots'
import api from '@/api/client'
import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import BallotStatus from '@/components/forms/BallotStatus.vue'
import FormAdminNav from '@/components/forms/FormAdminNav.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'

const rows = ref<AdminSummary[]>([])
const more = ref(false)
const pending = ref(false)
const error = ref('')

async function load(append = false) {
  error.value = ''
  pending.value = true
  try {
    const page = await api.get<Page<AdminSummary>>(ballotApi + '/admin', {
      query: { offset: append ? rows.value.length : 0 },
    })
    rows.value = append ? [...rows.value, ...page.items] : page.items
    more.value = page.more
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    pending.value = false
  }
}

onMounted(() => load())
</script>

<template>
  <div class="admin-page page-stack">
    <AdminPageHeader title="Forms" description="Manage ballots and response collections.">
      <template #actions>
        <BaseButton variant="primary" :to="{ name: 'admin-new-ballot-form' }">
          <template #icon><AppIcon name="plus" :size="16" /></template>
          New ballot form
        </BaseButton>
      </template>
    </AdminPageHeader>
    <FormAdminNav />
    <InlineFeedback :message="error" />
    <section class="panel" :aria-busy="pending">
      <header class="panel__header">
        <h2>Ballot forms</h2>
        <BaseButton variant="ghost" size="small" :loading="pending" @click="load()">Refresh</BaseButton>
      </header>
      <p v-if="pending && !rows.length" class="panel__body muted" role="status">Loading ballot forms…</p>
      <div v-else-if="rows.length" class="table-wrap">
        <table class="data-table data-table--list" aria-label="Ballot forms">
          <thead><tr><th scope="col">Form</th><th scope="col">Status</th><th scope="col">Ballots</th><th scope="col">Directory</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
          <tbody>
            <tr v-for="form in rows" :key="form.slug">
              <th scope="row"><RouterLink :to="{ name: 'admin-ballot-form', params: { slug: form.slug } }">{{ form.title }}</RouterLink></th>
              <td><BallotStatus :status="form.status" /></td>
              <td>{{ form.ballot_count }}</td>
              <td>{{ form.listed ? form.status === 'draft' ? 'List on opening' : 'Listed' : 'Unlisted' }}</td>
              <td><BaseButton size="small" variant="ghost" :to="{ name: 'admin-ballot-form', params: { slug: form.slug } }">Manage</BaseButton></td>
            </tr>
          </tbody>
        </table>
      </div>
      <AdminEmptyState v-else-if="!error" title="No ballot forms yet" />
      <div v-if="more" class="panel__body button-row">
        <BaseButton :loading="pending" @click="load(true)">Load more</BaseButton>
      </div>
    </section>
  </div>
</template>
