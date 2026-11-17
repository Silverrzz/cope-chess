<script setup lang="ts">
import { computed, ref } from 'vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import FormFields from './FormFields.vue'
import FormMarkdown from './FormMarkdown.vue'
import { requiredProgress } from './progress'
import type { FormAnswers, FormDefinition, FormErrors, FormField } from '@/types/forms'

const props = defineProps<{
  definition: FormDefinition
  modelValue: FormAnswers
  errors?: FormErrors
  disabled?: boolean
  upload?: ((file: File, field: FormField, path: string[]) => Promise<string>) | undefined
  maxUploadBytes?: number | undefined
  guestExtensions?: Record<string, string[]> | undefined
  preview?: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [value: FormAnswers] }>()
const fields = ref<InstanceType<typeof FormFields> | null>(null)
const progress = computed(() => requiredProgress(props.definition.fields, props.modelValue))
defineExpose({ focusField: (path: string) => fields.value?.focusField(path) })
</script>

<template>
  <article class="form-experience">
    <div v-if="preview" class="section-stack">
      <p class="muted">Respondent preview</p>
      <PageHeader :title="definition.title || 'Untitled form'">
        <template v-if="definition.description" #description>
          <FormMarkdown :source="definition.description" />
        </template>
      </PageHeader>
    </div>
    <slot name="context" />
    <section v-if="progress.total" class="form-progress" aria-label="Required answers">
      <span>Required answers</span><strong>{{ progress.answered }} of {{ progress.total }}</strong>
      <progress :value="progress.answered" :max="progress.total" aria-label="Required answers completed" />
    </section>
    <slot name="before-fields" />
    <FormFields ref="fields" :fields="definition.fields" :model-value="modelValue" :errors="errors ?? {}" :upload="upload"
      :max-upload-bytes="maxUploadBytes" :guest-extensions="guestExtensions" :disabled="!!disabled"
      @update:model-value="emit('update:modelValue', $event)" />
    <slot name="footer" />
  </article>
</template>

<style scoped>
.form-experience { min-width: 0; display: grid; gap: clamp(1rem, 4vw, 1.7rem); }
.form-progress { display: grid; grid-template-columns: 1fr auto; gap: .4rem; color: var(--color-text-muted); font-size: .8rem; }
.form-progress progress { grid-column: 1 / -1; width: 100%; height: .45rem; accent-color: var(--color-accent); }
</style>
