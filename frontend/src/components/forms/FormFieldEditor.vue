<script setup lang="ts">
import BaseButton from '@/components/ui/BaseButton.vue'
import type { FormErrors, FormField, FormKind } from '@/types/forms'

const props = withDefaults(defineProps<{ modelValue: FormField[]; errors?: FormErrors; path?: string[]; depth?: number; disabled?: boolean }>(), { errors: () => ({}), path: () => [], depth: 1, disabled: false })
const emit = defineEmits<{ 'update:modelValue': [fields: FormField[]] }>()
const kinds: { value: FormKind; label: string }[] = [
  { value: 'text', label: 'Short text' }, { value: 'long_text', label: 'Long text' }, { value: 'email', label: 'Email' },
  { value: 'integer', label: 'Whole number' }, { value: 'decimal', label: 'Decimal number' }, { value: 'checkbox', label: 'Checkbox' },
  { value: 'choice', label: 'One choice' }, { value: 'multiple_choice', label: 'Multiple choices' },
  { value: 'date', label: 'Date' }, { value: 'time', label: 'Time of day' }, { value: 'datetime', label: 'Date and time' },
  { value: 'group', label: 'Repeatable group' }, { value: 'attachment', label: 'File attachment' }, { value: 'reference', label: 'Cope resource' },
]

function update(index: number, changes: Partial<FormField>): void {
  emit('update:modelValue', props.modelValue.map((field, position) => position === index ? { ...field, ...changes } : field))
}
function add(): void {
  emit('update:modelValue', [...props.modelValue, { id: `field_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`, kind: 'text', label: 'New question' }])
}
function remove(index: number): void { emit('update:modelValue', props.modelValue.filter((_, position) => position !== index)) }
function move(index: number, direction: number): void {
  const result = [...props.modelValue]
  const field = result[index]
  if (!field || index + direction < 0 || index + direction >= result.length) return
  result.splice(index, 1); result.splice(index + direction, 0, field)
  emit('update:modelValue', result)
}
function changeKind(index: number, kind: FormKind): void {
  const old = props.modelValue[index]
  if (!old) return
  const replacement: FormField = { id: `field_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`, label: old.label, help: old.help ?? '', required: old.required ?? false, when: old.when ?? null, kind }
  if (kind === 'choice' || kind === 'multiple_choice') replacement.choices = [{ id: 'option_1', label: 'First option' }]
  if (kind === 'group') { replacement.fields = []; replacement.max_items = 20 }
  if (kind === 'reference') replacement.reference_type = 'engine'
  emit('update:modelValue', props.modelValue.map((field, position) => position === index ? replacement : field))
}
function updateChoice(index: number, choiceIndex: number, label: string): void {
  const choices = props.modelValue[index]?.choices ?? []
  update(index, { choices: choices.map((choice, position) => position === choiceIndex ? { ...choice, label } : choice) })
}
function addChoice(index: number): void {
  update(index, { choices: [...(props.modelValue[index]?.choices ?? []), { id: `option_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`, label: 'New option' }] })
}
function controls(field: FormField): FormField[] { return props.modelValue.filter((candidate) => candidate.id !== field.id && !candidate.when && ['choice', 'checkbox'].includes(candidate.kind)) }
function selectCondition(index: number, controlId: string): void {
  const control = props.modelValue.find((field) => field.id === controlId)
  update(index, { when: control ? { field: control.id, equals: control.kind === 'checkbox' ? true : control.choices?.[0]?.id ?? '' } : null })
}
function changeExpected(index: number, value: string): void {
  const field = props.modelValue[index]
  if (!field?.when) return
  const control = props.modelValue.find((candidate) => candidate.id === field.when?.field)
  update(index, { when: { field: field.when.field, equals: control?.kind === 'checkbox' ? value === 'true' : value } })
}
function messages(field: FormField, index: number): string[] {
  const prefixes = [[...props.path, field.id].join('.'), [...props.path, 'fields', String(index)].join('.')]
  return [...new Set(Object.entries(props.errors).filter(([key]) => prefixes.some((prefix) => key === prefix || key.startsWith(prefix + '.'))).flatMap(([, values]) => values))]
}
function number(event: Event, fallback = 0): number { const value = (event.target as HTMLInputElement).valueAsNumber; return Number.isFinite(value) ? value : fallback }
</script>

<template>
  <div class="field-editor">
    <fieldset v-for="(field, index) in modelValue" :key="field.id" class="question-card" :disabled="disabled">
      <legend class="visually-hidden">Question {{ index + 1 }}</legend>
      <div class="question-heading"><strong>Question {{ index + 1 }}</strong><div class="question-actions"><BaseButton type="button" class="button button--ghost button--small" :disabled="index === 0" :aria-label="`Move question ${index + 1} up`" @click="move(index, -1)" variant="ghost" size="small">↑</BaseButton><BaseButton type="button" class="button button--ghost button--small" :disabled="index === modelValue.length - 1" :aria-label="`Move question ${index + 1} down`" @click="move(index, 1)" variant="ghost" size="small">↓</BaseButton><BaseButton type="button" class="button button--ghost button--small" @click="remove(index)" variant="ghost" size="small">Remove</BaseButton></div></div>
      <div class="question-grid"><label>Question text<input class="input" :value="field.label" maxlength="300" @input="update(index, { label: ($event.target as HTMLInputElement).value })"></label><label>Answer type<select class="input" :value="field.kind" @change="changeKind(index, ($event.target as HTMLSelectElement).value as FormKind)"><option v-for="kind in kinds" :key="kind.value" :value="kind.value" :disabled="kind.value === 'group' && depth >= 3">{{ kind.label }}</option></select></label></div>
      <label>Help text<input class="input" :value="field.help ?? ''" maxlength="4000" @input="update(index, { help: ($event.target as HTMLInputElement).value })"></label>
      <label class="check"><input type="checkbox" :checked="field.required" @change="update(index, { required: ($event.target as HTMLInputElement).checked })">An answer is required</label>
      <div v-if="['choice', 'multiple_choice'].includes(field.kind)" class="choice-editor"><strong>Options</strong><div v-for="(choice, choiceIndex) in field.choices ?? []" :key="choice.id" class="choice-row"><input class="input" :value="choice.label" :aria-label="`Option ${choiceIndex + 1} label`" maxlength="300" @input="updateChoice(index, choiceIndex, ($event.target as HTMLInputElement).value)"><BaseButton type="button" class="button button--ghost button--small" :aria-label="`Remove option ${choiceIndex + 1}`" @click="update(index, { choices: field.choices?.filter((_, position) => position !== choiceIndex) ?? [] })" variant="ghost" size="small">Remove</BaseButton></div><BaseButton type="button" class="button button--ghost button--small" @click="addChoice(index)" variant="ghost" size="small">Add option</BaseButton></div>
      <template v-if="field.kind === 'group'"><div class="question-grid"><label>Minimum rows<input class="input" type="number" min="0" max="100" :value="field.min_items ?? 0" @input="update(index, { min_items: number($event) })"></label><label>Maximum rows<input class="input" type="number" min="1" max="100" :value="field.max_items ?? 20" @input="update(index, { max_items: number($event, 20) })"></label></div><FormFieldEditor :model-value="field.fields ?? []" :path="[...path, field.id]" :depth="depth + 1" :errors="errors" :disabled="disabled" @update:model-value="update(index, { fields: $event })" /></template>
      <details class="question-rules"><summary>Conditions and limits</summary><div class="rule-fields">
        <label>Show this question<select class="input" :value="field.when?.field ?? ''" @change="selectCondition(index, ($event.target as HTMLSelectElement).value)"><option value="">Always</option><option v-for="control in controls(field)" :key="control.id" :value="control.id">When “{{ control.label }}” has an answer</option></select></label>
        <label v-if="field.when">Expected answer<select class="input" :value="String(field.when.equals)" @change="changeExpected(index, ($event.target as HTMLSelectElement).value)"><template v-if="modelValue.find((candidate) => candidate.id === field.when?.field)?.kind === 'checkbox'"><option value="true">Checked</option><option value="false">Unchecked</option></template><template v-else><option v-for="choice in modelValue.find((candidate) => candidate.id === field.when?.field)?.choices ?? []" :key="choice.id" :value="choice.id">{{ choice.label }}</option></template></select></label>
        <label v-if="['text', 'long_text', 'email'].includes(field.kind)">Maximum characters<input class="input" type="number" min="1" max="65536" :value="field.max_length ?? 4096" @input="update(index, { max_length: number($event, 4096) })"></label>
        <template v-if="['integer', 'decimal'].includes(field.kind)"><div class="question-grid"><label>Minimum<input class="input" type="number" step="any" :value="field.minimum ?? ''" @input="update(index, { minimum: ($event.target as HTMLInputElement).value || null })"></label><label>Maximum<input class="input" type="number" step="any" :value="field.maximum ?? ''" @input="update(index, { maximum: ($event.target as HTMLInputElement).value || null })"></label></div><label>Unit<input class="input" :value="field.unit ?? ''" maxlength="80" @input="update(index, { unit: ($event.target as HTMLInputElement).value })"></label><label v-if="field.kind === 'decimal'">Decimal places<input class="input" type="number" min="0" max="12" :value="field.precision ?? 6" @input="update(index, { precision: number($event, 6) })"></label></template>
        <label v-if="field.kind === 'datetime'">Timezone<input class="input" :value="field.timezone ?? ''" placeholder="Use the visitor’s timezone, or enter Europe/London" @input="update(index, { timezone: ($event.target as HTMLInputElement).value || null })"></label>
        <template v-if="field.kind === 'attachment'"><label>Maximum file size in bytes<input class="input" type="number" min="1" :value="field.max_bytes ?? 10485760" @input="update(index, { max_bytes: number($event, 10485760) })"></label><label>Accepted content types<textarea class="input" :value="field.accepted_content_types?.join('\n') ?? ''" placeholder="One content type per line" @input="update(index, { accepted_content_types: ($event.target as HTMLTextAreaElement).value.split('\n').map((line) => line.trim()).filter(Boolean) })" /></label></template>
        <label v-if="field.kind === 'reference'">Resource type<input class="input" :value="field.reference_type ?? ''" @input="update(index, { reference_type: ($event.target as HTMLInputElement).value })"></label>
      </div></details>
      <ul v-if="messages(field, index).length" class="editor-errors"><li v-for="message in messages(field, index)" :key="message">{{ message }}</li></ul>
    </fieldset>
    <BaseButton type="button" class="button button--ghost" :disabled="disabled || modelValue.length >= 100" @click="add" variant="ghost">Add question</BaseButton>
  </div>
</template>

<style scoped>
.field-editor,.question-card,.rule-fields,.choice-editor{display:grid;gap:.85rem}.question-card{border:1px solid var(--color-border);border-radius:.7rem;margin:0;min-width:0;padding:1rem}.question-card label{display:grid;gap:.35rem;font-size:.8rem}.question-card .input{font:inherit;width:100%}.question-heading,.question-actions,.choice-row{align-items:center;display:flex;gap:.4rem;justify-content:space-between}.question-heading strong{font-size:.8rem}.question-grid{display:grid;gap:.8rem;grid-template-columns:1fr 1fr}.check{align-items:center;display:flex!important;gap:.55rem!important}.choice-row .input{flex:1;min-width:0}.choice-editor>strong{font-size:.8rem}.question-rules summary{cursor:pointer;font-size:.8rem}.rule-fields{padding-top:.8rem}.editor-errors{color:var(--color-danger,#b91c1c);font-size:.8rem;margin:0;padding-left:1.2rem}.visually-hidden{clip-path:inset(50%);height:1px;overflow:hidden;position:absolute;white-space:nowrap;width:1px}@media(max-width:42rem){.question-grid{grid-template-columns:1fr}.question-heading{align-items:flex-start;flex-wrap:wrap}}
</style>
