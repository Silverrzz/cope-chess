<script setup lang="ts">
import { computed, useId } from 'vue'
import FormFieldControl from '@/components/ui/FormField.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import FormFieldEditor from './FormFieldEditor.vue'
import FormMarkdownEditor from './FormMarkdownEditor.vue'
import { useSessionStore } from '@/stores/session'
import type { FormDefinition, FormErrors, FormField } from '@/types/forms'

export interface BindingDescription { key: string; label: string; permits_anonymous: boolean; arguments: { name: string; label: string; kinds: string[]; required: boolean }[] }
const props = withDefaults(defineProps<{ modelValue: FormDefinition; bindings: BindingDescription[]; errors?: FormErrors; disabled?: boolean }>(), { errors: () => ({}), disabled: false })
const emit = defineEmits<{ 'update:modelValue': [document: FormDefinition] }>()
const session = useSessionStore()
const titleId = useId()
const binding = computed(() => props.bindings.find((item) => item.key === props.modelValue.binding))
function change(changes: Partial<FormDefinition>): void { emit('update:modelValue', { ...props.modelValue, ...changes }) }
function map(name: string, id: string): void { const mapping = { ...props.modelValue.mapping }; if (id) mapping[name] = id; else delete mapping[name]; change({ mapping }) }
function compatible(kinds: string[]): FormField[] { return props.modelValue.fields.filter((field) => kinds.includes(field.kind)) }
</script>

<template>
  <fieldset class="definition-editor" :disabled="disabled">
    <legend class="sr-only">Form definition</legend>
    <section class="definition-section" aria-labelledby="form-details-heading">
      <h3 id="form-details-heading">Form details</h3>
      <FormFieldControl :id="titleId" label="Title" :error="errors.title ?? null" required>
        <template #default="{ describedBy, invalid }">
          <input :id="titleId" class="input" :value="modelValue.title" maxlength="300" required
            :aria-describedby="describedBy" :aria-invalid="invalid || undefined"
            @input="change({ title: ($event.target as HTMLInputElement).value })">
        </template>
      </FormFieldControl>
      <FormMarkdownEditor :model-value="modelValue.description" label="Introduction" :max-length="12000" :disabled="disabled" @update:model-value="change({ description: $event })" />
    </section>
    <section class="definition-section" aria-labelledby="form-questions-heading">
      <h3 id="form-questions-heading">Questions and content</h3>
      <FormFieldEditor :model-value="modelValue.fields" :errors="errors" :disabled="disabled" @update:model-value="change({ fields: $event })" />
    </section>
    <details class="definition-section response-settings">
      <summary>Response settings</summary>
      <div class="settings-grid">
        <label>Who can respond?<select class="input" :value="modelValue.audience" @change="change({ audience: ($event.target as HTMLSelectElement).value as FormDefinition['audience'] })">
          <option value="public" :disabled="!binding?.permits_anonymous">Anyone with the link</option>
          <option value="authenticated">Signed-in users</option><option value="invited">Invited users</option>
        </select></label>
        <label>Response purpose<select class="input" :value="modelValue.binding" @change="change({ binding: ($event.target as HTMLSelectElement).value, mapping: {} })">
          <option v-for="item in bindings" :key="item.key" :value="item.key">{{ item.label }}</option>
        </select></label>
      </div>
      <label class="check"><input type="checkbox" :checked="modelValue.allow_amendments !== false" @change="change({ allow_amendments: ($event.target as HTMLInputElement).checked })">Allow submitters to edit their responses</label>
      <InlineFeedback v-if="session.session?.privatise_platform && modelValue.audience === 'public'" tone="info" message="The site currently requires sign-in, including for public forms." />
      <section v-if="binding?.arguments.length" class="mapping">
        <h4>Answer mapping</h4>
        <label v-for="argument in binding.arguments" :key="argument.name">
          {{ argument.label }}{{ argument.required ? ' (required)' : '' }}
          <select class="input" :value="modelValue.mapping?.[argument.name] ?? ''" @change="map(argument.name, ($event.target as HTMLSelectElement).value)">
            <option value="">Choose a question</option>
            <option v-for="field in compatible(argument.kinds)" :key="field.id" :value="field.id">{{ field.label }}</option>
          </select>
          <span v-for="message in errors[`mapping.${argument.name}`] ?? []" :key="message" class="error">{{ message }}</span>
        </label>
      </section>
      <label class="limit-control">Submissions per person per hour<input class="input" type="number" min="1" max="1000" :value="modelValue.submissions_per_hour ?? 20"
        @input="change({ submissions_per_hour: ($event.target as HTMLInputElement).valueAsNumber })"></label>
    </details>
  </fieldset>
</template>

<style scoped>
.definition-editor { display: grid; gap: 1.4rem; border: 0; margin: 0; padding: 0; min-width: 0; }
.definition-section { display: grid; gap: .85rem; }
.definition-section + .definition-section { padding-top: 1.2rem; border-top: 1px solid var(--color-border); }
.definition-section h3, .definition-section h4 { margin: 0; font-size: .95rem; }
.definition-section label { display: grid; gap: .4rem; font-size: .85rem; }
.settings-grid { display: grid; gap: .85rem; grid-template-columns: 1fr 1fr; }
.definition-section .check { display: flex; align-items: center; }
.mapping { display: grid; gap: .75rem; }
.response-settings summary { cursor: pointer; font-weight: 650; }
.response-settings[open] > :not(summary) { margin-top: .85rem; }
.limit-control { max-width: 18rem; }
.error { color: var(--color-danger); }
@media (max-width: 42rem) { .settings-grid { grid-template-columns: 1fr; } }
</style>
