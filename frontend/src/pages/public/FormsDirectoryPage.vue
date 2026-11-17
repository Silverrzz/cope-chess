<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { formDirectory, type ListedForm } from '@/api/forms'
import ContentState from '@/components/public/ContentState.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'

const items = ref<ListedForm[]>([])
const more = ref(false)
const loading = ref(true)
const error = ref('')

async function load(append = false) {
  loading.value = true
  error.value = ''
  try {
    const page = await formDirectory(append ? items.value.length : 0)
    items.value = append ? [...items.value, ...page.items] : page.items
    more.value = page.more
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not load forms.'
  } finally {
    loading.value = false
  }
}

onMounted(() => load())
</script>

<template>
  <div class="page-container page-stack">
    <PageHeader title="Forms" />
    <ContentState v-if="error" kind="error" compact :message="error" action-label="Try again" @action="load(items.length > 0)" />
    <ContentState v-else-if="loading && !items.length" kind="loading" compact title="Loading forms" />
    <ContentState v-else-if="!items.length" kind="empty" compact title="No listed forms" />
    <div v-if="items.length" class="content-grid">
      <article v-for="form in items" :key="form.slug" class="panel panel__body section-stack">
        <div class="toolbar toolbar--between">
          <h2><RouterLink :to="{ name: 'public-form', params: { slug: form.slug } }">{{ form.title }}</RouterLink></h2>
          <StatusBadge :tone="form.closed ? 'neutral' : 'info'" dot>{{ form.closed ? 'Closed' : 'Open' }}</StatusBadge>
        </div>
        <p v-if="form.description" class="muted">{{ form.description }}</p>
        <div class="button-row">
          <BaseButton size="small" :to="{ name: 'public-form', params: { slug: form.slug } }">
            {{ form.closed && form.response_visibility !== 'private' ? 'View results' : 'Open form' }}
          </BaseButton>
        </div>
      </article>
    </div>
    <div v-if="more && !error" class="button-row"><BaseButton :loading="loading" @click="load(true)">Load more forms</BaseButton></div>
  </div>
</template>
