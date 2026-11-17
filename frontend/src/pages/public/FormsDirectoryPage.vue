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

function hasResults(form: ListedForm) {
  return form.closed && form.response_visibility !== 'private'
}

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
  <div class="page-container page-stack" :aria-busy="loading">
    <PageHeader title="Forms">
      <template #actions><BaseButton :to="{ name: 'my-submissions' }">My submissions</BaseButton></template>
    </PageHeader>
    <ContentState v-if="error" kind="error" compact :message="error" action-label="Try again" @action="load(items.length > 0)" />
    <ContentState v-else-if="loading && !items.length" kind="loading" compact title="Loading forms" />
    <ContentState v-else-if="!items.length" kind="empty" compact title="No listed forms" />

    <div v-if="items.length" class="content-grid forms-grid">
      <article v-for="form in items" :key="form.slug" class="panel panel__body section-stack form-card">
        <header class="form-card__heading">
          <h2>
            <RouterLink :to="{ name: 'public-form', params: { slug: form.slug } }">{{ form.title }}</RouterLink>
          </h2>
          <StatusBadge :tone="form.closed ? 'neutral' : 'info'" dot>{{ form.closed ? 'Closed' : 'Open' }}</StatusBadge>
        </header>

        <p v-if="form.description" class="muted">{{ form.description }}</p>

        <div class="button-row form-card__actions">
          <BaseButton size="small" :to="{ name: 'public-form', params: { slug: form.slug } }">
            {{ hasResults(form) ? 'View results' : 'Open form' }}
          </BaseButton>
        </div>
      </article>
    </div>

    <div v-if="more && !error" class="button-row">
      <BaseButton :loading="loading" @click="load(true)">Load more forms</BaseButton>
    </div>
  </div>
</template>

<style scoped>
.forms-grid {
  align-items: stretch;
}

.form-card {
  overflow-wrap: anywhere;
}

.form-card__heading {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  gap: var(--space-3);
}

.form-card__heading h2 {
  font-size: 1.25rem;
  text-wrap: wrap;
}

.form-card__heading a {
  text-decoration: none;
}

.form-card__heading a:hover {
  color: var(--color-accent);
}

.form-card__actions {
  margin-block-start: auto;
}
</style>
