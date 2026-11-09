<script setup lang="ts">
import { computed, ref } from 'vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { contentKinds, fieldActive, type FormAnswers, type FormField } from '@/types/forms'

const props = defineProps<{
  fields: FormField[]
  answers: FormAnswers
  download: (id: string) => Promise<void>
}>()

const visible = computed(() => props.fields.filter(field => !contentKinds.has(field.kind) && fieldActive(field, props.answers)))
const downloadError = ref('')
const downloading = ref<string | null>(null)

function groups(value: unknown): FormAnswers[] {
  return Array.isArray(value)
    ? value.filter((item): item is FormAnswers => item !== null && typeof item === 'object' && !Array.isArray(item))
    : []
}

function choiceLabel(field: FormField, value: unknown): string {
  return field.choices?.find(choice => choice.id === value)?.label ?? String(value)
}

function text(field: FormField, value: unknown): string[] {
  if (value == null || value === '') return []
  if (typeof value === 'boolean') return [field.kind === 'consent' ? value ? 'Agreed' : 'Not agreed' : value ? 'Yes' : 'No']
  if (field.kind === 'matrix' && typeof value === 'object') {
    return (field.rows ?? []).filter(row => Object.hasOwn(value, row.id)).map(row =>
      `${row.label} · ${choiceLabel(field, (value as FormAnswers)[row.id])}`)
  }
  if (Array.isArray(value)) return value.map(item => choiceLabel(field, item))
  if (typeof value === 'string' || typeof value === 'number') {
    const label = choiceLabel(field, value)
    return [field.unit ? `${label} ${field.unit}` : label]
  }
  return []
}

function resource(field: FormField, value: unknown): { label: string; url: string } | undefined {
  if (typeof value !== 'string') return
  if (field.kind === 'chess_game' && /^[1-9]\d*\/[1-9]\d*$/.test(value)) {
    const [tournament, game] = value.split('/')
    return { label: `Game #${game}`, url: `/tournaments/${tournament}?game_id=${game}` }
  }
  if (field.kind === 'rating_list' && /^[1-9]\d*$/.test(value)) {
    return { label: `Rating list #${value}`, url: `/ratings?rating_list_id=${value}` }
  }
}

async function openFile(id: string): Promise<void> {
  downloading.value = id
  downloadError.value = ''
  try { await props.download(id) }
  catch (cause) { downloadError.value = cause instanceof Error ? cause.message : 'Could not download the attachment.' }
  finally { downloading.value = null }
}
</script>

<template>
  <dl class="answer-summary">
    <div v-for="field in visible" :key="field.id" class="answer-summary-row">
      <dt>{{ field.label }}</dt>
      <dd v-if="field.kind === 'group' && groups(answers[field.id]).length">
        <section v-for="(row, index) in groups(answers[field.id])" :key="String(row._row_id ?? index)" class="answer-group">
          <strong>Entry {{ index + 1 }}</strong>
          <FormAnswerSummary :fields="field.fields ?? []" :answers="row" :download="download" />
        </section>
      </dd>
      <dd v-else-if="field.kind === 'attachment' && typeof answers[field.id] === 'string'">
        <BaseButton size="small" :loading="downloading === answers[field.id]" :disabled="downloading !== null" @click="openFile(String(answers[field.id]))">Download attachment</BaseButton>
      </dd>
      <dd v-else-if="resource(field, answers[field.id])">
        <RouterLink :to="resource(field, answers[field.id])!.url">{{ resource(field, answers[field.id])!.label }}</RouterLink>
      </dd>
      <dd v-else-if="text(field, answers[field.id]).length === 1">{{ text(field, answers[field.id])[0] }}</dd>
      <dd v-else-if="text(field, answers[field.id]).length">
        <component :is="field.kind === 'ranking' ? 'ol' : 'ul'">
          <li v-for="(value, index) in text(field, answers[field.id])" :key="index">{{ value }}</li>
        </component>
      </dd>
      <dd v-else class="empty-answer">No answer</dd>
    </div>
  </dl>
  <p v-if="downloadError" class="answer-error" role="alert">{{ downloadError }}</p>
</template>

<style scoped>
.answer-summary { margin: 0; }
.answer-summary-row { padding: .85rem 0; display: grid; gap: .35rem; }
.answer-summary-row + .answer-summary-row { border-top: 1px solid var(--color-border); }
dt { font-weight: 650; }
dd { margin: 0; overflow-wrap: anywhere; white-space: pre-wrap; }
dd ul, dd ol { margin: 0; padding-inline-start: 1.25rem; }
.empty-answer { color: var(--color-text-muted); font-size: .85rem; }
.answer-group { margin-top: .45rem; padding: .75rem; border: 1px solid var(--color-border); border-radius: var(--radius-md); }
.answer-group strong { font-size: .8rem; color: var(--color-text-muted); }
.answer-error { color: var(--color-danger); font-size: .85rem; }
</style>
