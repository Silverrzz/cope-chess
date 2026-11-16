<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ballotApi, ballotTool, errorText, type Page, type Summary } from '@/api/ballots'
import api from '@/api/client'
import BallotStatus from '@/components/forms/BallotStatus.vue'
import ContentState from '@/components/public/ContentState.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
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
  <div class="page-container page-stack">
    <PageHeader title="Forms" description="Browse community ballots and published results.">
      <template #actions>
        <BaseButton :href="ballotTool" variant="secondary">
          <template #icon><AppIcon name="download" :size="16" /></template>
          Offline voting tool
        </BaseButton>
      </template>
    </PageHeader>

    <ContentState v-if="error" kind="error" compact :message="error" action-label="Try again" @action="load(items.length > 0)" />
    <ContentState v-else-if="busy && !items.length" kind="loading" compact title="Loading forms" />
    <ContentState v-else-if="!items.length" kind="empty" compact title="No listed forms" />

    <div v-if="items.length" class="content-grid">
      <article v-for="form in items" :key="form.slug" class="panel">
        <div class="panel__body section-stack">
          <BallotStatus :status="form.status" />
          <h2><RouterLink :to="{ name: 'secret-ballot', params: { slug: form.slug } }">{{ form.title }}</RouterLink></h2>
          <p v-if="form.description" class="muted">{{ form.description }}</p>
        </div>
        <hr class="divider">
        <footer class="panel__body toolbar toolbar--between">
          <small class="muted">{{ form.question_count }} {{ form.question_count === 1 ? 'question' : 'questions' }} · {{ form.ballot_count }} {{ form.ballot_count === 1 ? 'ballot' : 'ballots' }}</small>
          <BaseButton size="small" :to="{ name: 'secret-ballot', params: { slug: form.slug } }">
            {{ form.status === 'published' ? 'View results' : 'Open form' }}
          </BaseButton>
        </footer>
      </article>
    </div>
    <div v-if="more && !error" class="button-row">
      <BaseButton :loading="busy" @click="load(true)">Load more forms</BaseButton>
    </div>
  </div>
</template>
