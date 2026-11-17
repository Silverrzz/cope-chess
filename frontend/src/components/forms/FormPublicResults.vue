<script setup lang="ts">
import { ref, watch } from 'vue'
import { formResults, type PublicResults } from '@/api/forms'
import ContentState from '@/components/public/ContentState.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import FormAnswerSummary from './FormAnswerSummary.vue'
import type { FormField } from '@/types/forms'

const props = defineProps<{ slug: string; fields: FormField[] }>()
const results = ref<PublicResults | null>(null)
const loading = ref(false)
const error = ref('')

async function load(more = false, signal?: AbortSignal) {
  loading.value = true
  error.value = ''
  const current = more && results.value?.published && results.value.mode === 'individual' ? results.value : null
  try {
    const incoming = await formResults(props.slug, current?.next_cursor, signal)
    signal?.throwIfAborted()
    results.value = current && incoming.published && incoming.mode === 'individual'
      ? { ...incoming, items: [...current.items, ...incoming.items] }
      : incoming
  } catch (cause) {
    if (!signal?.aborted) error.value = cause instanceof Error ? cause.message : 'Could not load results.'
  } finally {
    if (!signal?.aborted) loading.value = false
  }
}

watch(() => props.slug, (_, __, cleanup) => {
  const controller = new AbortController()
  cleanup(() => controller.abort())
  results.value = null
  void load(false, controller.signal)
}, { immediate: true })
</script>

<template>
  <section class="panel panel__body section-stack" aria-labelledby="public-form-results">
    <h2 id="public-form-results">Results</h2>
    <ContentState v-if="error" kind="error" compact :message="error" action-label="Try again" @action="load()" />
    <p v-else-if="loading && !results" class="muted" role="status">Loading results…</p>
    <p v-else-if="results && !results.published" class="muted">Results will be published after the form closes.</p>
    <template v-if="results?.published && results.mode === 'summary'">
      <p class="muted">{{ results.total }} {{ results.total === 1 ? 'response' : 'responses' }}</p>
      <section v-for="field in results.fields" :key="field.id" class="panel panel__body section-stack">
        <header>
          <h3>{{ field.label }}</h3>
          <small class="muted">{{ field.answered }} answered<template v-if="field.average !== null"> · Average {{ field.average }}</template></small>
        </header>
        <dl v-if="field.options.length" class="definition-list">
          <template v-for="option in field.options" :key="option.label">
            <dt>{{ option.label }}</dt><dd>{{ option.count }}</dd>
          </template>
        </dl>
      </section>
    </template>
    <template v-else-if="results?.published && results.mode === 'individual'">
      <p v-if="!results.items.length" class="muted">No responses yet.</p>
      <details v-for="(item, index) in results.items" :key="item.id" class="panel panel__body">
        <summary>Response {{ index + 1 }}</summary>
        <FormAnswerSummary :fields="fields" :answers="item.answers" />
      </details>
      <div v-if="results.next_cursor" class="button-row"><BaseButton :loading="loading" @click="load(true)">Load more responses</BaseButton></div>
    </template>
  </section>
</template>
