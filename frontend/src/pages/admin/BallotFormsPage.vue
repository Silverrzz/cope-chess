<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ballotApi, errorText, type AdminSummary, type Page } from '@/api/ballots'
import api from '@/api/client'
import BallotStatus from '@/components/forms/BallotStatus.vue'
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
  <div class="ballot-shell">
    <header class="ballot-hero">
      <div><h1>Forms</h1><p>Manage voter eligibility, ballot publication and aggregate results.</p></div>
      <BaseButton variant="primary" :to="{ name: 'admin-new-ballot-form' }">
        <template #icon><AppIcon name="plus" :size="17" /></template>
        New ballot form
      </BaseButton>
    </header>
    <nav class="ballot-tabs" aria-label="Form types">
      <RouterLink to="/admin/forms" aria-current="page">Ballot forms</RouterLink>
      <RouterLink to="/admin/forms/collections">Response collections</RouterLink>
      <RouterLink to="/admin/form-responses">Response inbox</RouterLink>
    </nav>
    <section class="ballot-panel ballot-catalogue" :aria-busy="pending">
      <div class="ballot-section-heading ballot-section-heading--split">
        <h2>Ballot forms</h2>
        <BaseButton variant="ghost" size="small" :loading="pending" @click="load()">Refresh</BaseButton>
      </div>
      <p v-if="error" class="ballot-message ballot-message--error" role="alert">{{ error }}</p>
      <div v-else-if="!rows.length" class="ballot-empty">
        <AppIcon name="archive" :size="26" />
        <h3>{{ pending ? 'Loading ballot forms…' : 'No ballot forms yet' }}</h3>
        <p v-if="!pending">Create a draft to set up questions, voters and trustees.</p>
      </div>
      <ul v-else class="ballot-admin-list">
        <li v-for="form in rows" :key="form.slug">
          <div>
            <RouterLink class="ballot-row-title" :to="{ name: 'admin-ballot-form', params: { slug: form.slug } }">{{ form.title }}</RouterLink>
            <small>{{ form.question_count }} {{ form.question_count === 1 ? 'question' : 'questions' }} · {{ form.ballot_count }} {{ form.ballot_count === 1 ? 'ballot' : 'ballots' }}</small>
          </div>
          <BallotStatus :status="form.status" />
          <span class="ballot-visibility"><AppIcon :name="form.listed ? 'eye' : 'eye-off'" :size="16" />{{ form.listed ? form.status === 'draft' ? 'List on opening' : 'Listed' : 'Unlisted' }}</span>
          <BaseButton size="small" :to="{ name: 'admin-ballot-form', params: { slug: form.slug } }">Manage</BaseButton>
        </li>
      </ul>
      <BaseButton v-if="more" :loading="pending" @click="load(true)">Load more</BaseButton>
    </section>
  </div>
</template>
