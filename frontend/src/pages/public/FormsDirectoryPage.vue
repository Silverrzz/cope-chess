<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { formDirectory, type ListedForm } from '@/api/forms'
import ContentState from '@/components/public/ContentState.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
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
  <div class="page-container page-container--narrow page-stack" :aria-busy="loading">
    <PageHeader title="Forms" />
    <ContentState v-if="error" kind="error" compact :message="error" action-label="Try again" @action="load(items.length > 0)" />
    <ContentState v-else-if="loading && !items.length" kind="loading" compact title="Loading forms" />
    <ContentState v-else-if="!items.length" kind="empty" compact title="No listed forms" />
    <ul v-if="items.length" class="panel form-list" role="list">
      <li v-for="form in items" :key="form.slug">
        <RouterLink
          class="panel__body form-list__link" :to="{ name: 'public-form', params: { slug: form.slug } }"
          :aria-labelledby="`form-title-${form.slug}`" :aria-describedby="`form-status-${form.slug}`"
        >
          <div class="form-list__copy">
            <h2 :id="`form-title-${form.slug}`">{{ form.title }}</h2>
            <p v-if="form.description" class="muted">{{ form.description }}</p>
          </div>
          <StatusBadge :id="`form-status-${form.slug}`" :tone="hasResults(form) ? 'info' : form.closed ? 'neutral' : 'success'">
            {{ hasResults(form) ? 'Results available' : form.closed ? 'Closed' : 'Open' }}
          </StatusBadge>
          <AppIcon class="form-list__arrow" name="chevron-right" :size="18" />
        </RouterLink>
      </li>
    </ul>
    <div v-if="more && !error" class="button-row"><BaseButton :loading="loading" @click="load(true)">Load more forms</BaseButton></div>
  </div>
</template>

<style scoped>
.form-list {
  list-style: none;
}

.form-list > li + li {
  border-block-start: 1px solid var(--color-border);
}

.form-list__link {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: var(--space-3);
  text-decoration: none;
  transition: background-color var(--transition-fast);
}

.form-list > li:first-child .form-list__link {
  border-start-start-radius: var(--radius-lg);
  border-start-end-radius: var(--radius-lg);
}

.form-list > li:last-child .form-list__link {
  border-end-start-radius: var(--radius-lg);
  border-end-end-radius: var(--radius-lg);
}

.form-list__link:is(:hover, :focus-visible) {
  background: var(--color-surface-hover);
}

.form-list__copy {
  display: grid;
  gap: var(--space-1);
  overflow-wrap: anywhere;
}

.form-list__copy h2 {
  font-size: 1.0625rem;
  text-wrap: wrap;
}

.form-list__copy p {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  font-size: 0.875rem;
}

.form-list__arrow {
  color: var(--color-text-muted);
}

@media (max-width: 36rem) {
  .form-list__link {
    grid-template-columns: minmax(0, 1fr) auto;
    row-gap: var(--space-2);
  }

  .form-list__copy {
    grid-column: 1 / -1;
  }
}
</style>
