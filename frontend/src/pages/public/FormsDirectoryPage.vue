<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ballotApi, ballotTool, errorText, type Page, type Summary } from '@/api/ballots'
import api from '@/api/client'
import BallotStatus from '@/components/forms/BallotStatus.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'

const items = ref<Summary[]>([])
const busy = ref(false)
const more = ref(false)
const error = ref('')

async function load(append = false) {
  error.value = ''
  busy.value = true
  try {
    const page = await api.get<Page<Summary>>(ballotApi + '/directory', {
      query: { offset: append ? items.value.length : 0 },
    })
    items.value = append ? [...items.value, ...page.items] : page.items
    more.value = page.more
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}

onMounted(() => load())
</script>

<template>
  <div class="ballot-shell page-container">
    <header class="ballot-hero">
      <div>
        <h1>Forms</h1>
        <p>Vote with a registered proof and explore the published results of Cope community ballots.</p>
      </div>
      <BaseButton :href="ballotTool" variant="secondary">
        <template #icon><AppIcon name="download" :size="16" /></template>
        Offline voting tool
      </BaseButton>
    </header>

    <p v-if="error" class="ballot-message ballot-message--error" role="alert">{{ error }}</p>
    <p v-if="busy && !items.length" role="status">Loading forms…</p>
    <div v-else-if="!items.length && !error" class="ballot-panel ballot-empty">
      <AppIcon name="archive" :size="26" />
      <h2>No listed forms</h2>
      <p>Forms appear here when an organizer opens them and adds them to the directory.</p>
    </div>
    <div v-else class="ballot-directory">
      <article v-for="form in items" :key="form.slug" class="ballot-card">
        <div class="ballot-card__body">
          <BallotStatus :status="form.status" />
          <h2><RouterLink :to="{ name: 'secret-ballot', params: { slug: form.slug } }">{{ form.title }}</RouterLink></h2>
          <p v-if="form.description">{{ form.description }}</p>
        </div>
        <div class="ballot-card__foot">
          <span>{{ form.question_count }} {{ form.question_count === 1 ? 'question' : 'questions' }} · {{ form.ballot_count }} {{ form.ballot_count === 1 ? 'ballot' : 'ballots' }}</span>
          <RouterLink :to="{ name: 'secret-ballot', params: { slug: form.slug } }">
            {{ form.status === 'published' ? 'View results' : 'Open form' }}
            <AppIcon name="arrow-right" :size="16" />
          </RouterLink>
        </div>
      </article>
    </div>
    <BaseButton v-if="more || error" class="ballot-more" :loading="busy" @click="load(!error && more)">
      {{ error ? 'Retry' : 'Load more forms' }}
    </BaseButton>
  </div>
</template>
