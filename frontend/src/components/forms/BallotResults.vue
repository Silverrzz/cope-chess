<script setup lang="ts">
import type { Question } from '@/api/ballots'

defineProps<{ questions: Question[]; results: number[][]; total: number }>()

const percent = new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 1 })
</script>

<template>
  <div class="section-stack">
    <section v-for="(question, index) in questions" :key="index" class="panel panel__body section-stack">
      <h2>{{ question.title }}</h2>
      <div v-for="(option, optionIndex) in question.options" :key="option" class="result-option">
        <div class="toolbar toolbar--between">
          <strong>{{ option }}</strong>
          <small class="muted">{{ results[index]?.[optionIndex] ?? 0 }} votes · {{ percent.format(total ? (results[index]?.[optionIndex] ?? 0) / total : 0) }}</small>
        </div>
        <progress :value="results[index]?.[optionIndex] ?? 0" :max="Math.max(total, 1)" :aria-label="option" />
      </div>
    </section>
  </div>
</template>

<style scoped>
.result-option {
  display: grid;
  gap: var(--space-1);
  overflow-wrap: anywhere;
}

.result-option progress {
  width: 100%;
  height: 0.5rem;
  accent-color: var(--color-accent);
}
</style>
