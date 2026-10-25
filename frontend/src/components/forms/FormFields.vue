<script setup lang="ts">
import BaseButton from '@/components/ui/BaseButton.vue'
import { nextTick, reactive, ref, useId } from 'vue'
import FormDateTime from './FormDateTime.vue'
import { fieldActive, initialAnswers, type FormAnswers, type FormChoice, type FormErrors, type FormField } from '@/types/forms'

const props = withDefaults(defineProps<{
  fields: FormField[]
  modelValue: FormAnswers
  errors?: FormErrors
  path?: string[]
  disabled?: boolean
  upload?: ((file: File, field: FormField, path: string[]) => Promise<string>) | undefined
  references?: ((field: FormField, search: string) => Promise<FormChoice[]>) | undefined
  download?: ((artifactId: string) => Promise<void>) | undefined
}>(), { errors: () => ({}), path: () => [], disabled: false })
const emit = defineEmits<{ 'update:modelValue': [value: FormAnswers] }>()
const prefix = useId()
const container = ref<HTMLElement | null>(null)
const transfers = reactive<Record<string, string>>({})
const transferErrors = reactive<Record<string, string>>({})
const resources = reactive<Record<string, FormChoice[]>>({})
const generations: Record<string, number> = {}

function key(field: FormField): string { return [...props.path, field.id].join('.') }
function elementId(field: FormField): string { return `${prefix}-${field.id}` }
function focusField(path: string): void {
  const field = container.value?.querySelector<HTMLElement>(`[data-field-path="${CSS.escape(path)}"]`)
  field?.querySelector<HTMLElement>('input,select,textarea,button')?.focus()
}
defineExpose({ focusField })
function display(field: FormField): string {
  const value = props.modelValue[field.id]
  return typeof value === 'string' || typeof value === 'number' ? String(value) : ''
}
function errors(field: FormField): string[] { return props.errors[key(field)] ?? [] }
function descriptions(field: FormField): string | undefined {
  const values = [field.help ? `${elementId(field)}-help` : '', errors(field).length ? `${elementId(field)}-error` : ''].filter(Boolean)
  return values.length ? values.join(' ') : undefined
}
function update(field: FormField, value: unknown): void {
  const next = { ...props.modelValue }
  if (value === undefined || value === '' && !['text', 'long_text'].includes(field.kind)) delete next[field.id]
  else next[field.id] = value
  emit('update:modelValue', next)
}
function inputKind(field: FormField): string {
  return ({ integer: 'number', decimal: 'number', email: 'email', date: 'date', time: 'time' } as Record<string, string>)[field.kind] ?? 'text'
}
function selected(field: FormField): string[] {
  const value = props.modelValue[field.id]
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}
function choose(field: FormField, id: string, checked: boolean): void {
  const values = selected(field).filter((item) => item !== id)
  if (checked) values.push(id)
  update(field, values)
}
function rows(field: FormField): FormAnswers[] {
  const value = props.modelValue[field.id]
  return Array.isArray(value) ? value.filter((item): item is FormAnswers => !!item && typeof item === 'object' && !Array.isArray(item)) : []
}
async function addRow(field: FormField): Promise<void> {
  const id = crypto.randomUUID()
  update(field, [...rows(field), { _row_id: id, ...initialAnswers(field.fields ?? []) }])
  await nextTick()
  container.value?.querySelector<HTMLElement>(`[data-row-id="${id}"] input, [data-row-id="${id}"] select, [data-row-id="${id}"] textarea`)?.focus()
}
function updateRow(field: FormField, index: number, value: FormAnswers): void {
  const next = [...rows(field)]
  next[index] = { ...value, _row_id: next[index]?._row_id }
  update(field, next)
}
async function removeRow(field: FormField, index: number): Promise<void> {
  const next = rows(field).filter((_, rowIndex) => index !== rowIndex)
  update(field, next)
  await nextTick()
  const remaining = next[Math.min(index, next.length - 1)]?._row_id
  if (typeof remaining === 'string') container.value?.querySelector<HTMLElement>(`[data-row-id="${CSS.escape(remaining)}"] input, [data-row-id="${CSS.escape(remaining)}"] select, [data-row-id="${CSS.escape(remaining)}"] textarea`)?.focus()
  else container.value?.querySelector<HTMLButtonElement>(`[data-add-group="${field.id}"]`)?.focus()
}
function moveRow(field: FormField, index: number, direction: number): void {
  const next = [...rows(field)]
  const row = next[index]
  if (!row || index + direction < 0 || index + direction >= next.length) return
  next.splice(index, 1)
  next.splice(index + direction, 0, row)
  update(field, next)
}
async function uploadFile(field: FormField, event: Event): Promise<void> {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file || !props.upload) return
  const path = key(field)
  const generation = generations[path] = (generations[path] ?? 0) + 1
  transfers[path] = file.name
  transferErrors[path] = ''
  try {
    const receipt = await props.upload(file, field, [...props.path, field.id])
    if (generations[path] === generation) update(field, receipt)
  } catch (cause) {
    if (generations[path] === generation) transferErrors[path] = cause instanceof Error ? cause.message : 'The attachment could not be uploaded.'
  } finally { if (generations[path] === generation) delete transfers[path]; (event.target as HTMLInputElement).value = '' }
}
async function downloadFile(field: FormField): Promise<void> {
  const id = props.modelValue[field.id]
  if (!props.download || typeof id !== 'string') return
  transferErrors[key(field)] = ''
  try { await props.download(id) } catch (cause) { transferErrors[key(field)] = cause instanceof Error ? cause.message : 'Could not download the attachment.' }
}
async function findReferences(field: FormField, search: string): Promise<void> {
  if (!props.references) return
  const path = key(field)
  const generation = generations[path] = (generations[path] ?? 0) + 1
  try {
    const options = await props.references(field, search)
    if (generations[path] === generation) resources[path] = options
  } catch (cause) {
    if (generations[path] === generation) transferErrors[path] = cause instanceof Error ? cause.message : 'The resource list could not be loaded.'
  }
}
</script>

<template>
  <div ref="container" class="form-fields">
    <template v-for="field in fields" :key="field.id">
      <div v-if="fieldActive(field, modelValue)" class="form-field" :data-field-path="key(field)" :class="{ 'form-field--invalid': errors(field).length }">
        <fieldset v-if="field.kind === 'group'" :disabled="disabled" class="form-group" :aria-describedby="descriptions(field)">
          <legend>{{ field.label }}<span v-if="field.required" class="required"> · required</span></legend>
          <div v-for="(row, index) in rows(field)" :key="String(row._row_id ?? index)" :data-row-id="String(row._row_id)" class="form-row">
            <div class="form-row__heading"><strong>Row {{ index + 1 }}</strong><div class="form-row__actions"><BaseButton type="button" class="button button--ghost button--small" :disabled="index === 0" :aria-label="`Move row ${index + 1} up`" @click="moveRow(field, index, -1)" variant="ghost" size="small">↑</BaseButton><BaseButton type="button" class="button button--ghost button--small" :disabled="index === rows(field).length - 1" :aria-label="`Move row ${index + 1} down`" @click="moveRow(field, index, 1)" variant="ghost" size="small">↓</BaseButton><BaseButton type="button" class="button button--ghost button--small" :aria-label="`Remove row ${index + 1} from ${field.label}`" @click="removeRow(field, index)" variant="ghost" size="small">Remove</BaseButton></div></div>
            <FormFields :fields="field.fields ?? []" :model-value="row" :path="[...path, field.id, String(row._row_id)]" :errors="props.errors" :disabled="disabled" :upload="upload" :references="references" :download="download" @update:model-value="updateRow(field, index, $event)" />
          </div>
          <BaseButton type="button" class="button button--ghost button--small" :data-add-group="field.id" :disabled="rows(field).length >= (field.max_items ?? 20)" @click="addRow(field)" variant="ghost" size="small">Add row</BaseButton>
        </fieldset>
        <fieldset v-else-if="field.kind === 'multiple_choice'" class="form-choices" :disabled="disabled" :aria-describedby="descriptions(field)">
          <legend>{{ field.label }}<span v-if="field.required" class="required"> · required</span></legend>
          <label v-for="choice in field.choices ?? []" :key="choice.id" class="form-check"><input type="checkbox" :checked="selected(field).includes(choice.id)" :aria-invalid="!!errors(field).length || undefined" @change="choose(field, choice.id, ($event.target as HTMLInputElement).checked)"><span>{{ choice.label }}</span></label>
        </fieldset>
        <template v-else-if="field.kind === 'checkbox'"><label class="form-check" :for="elementId(field)"><input :id="elementId(field)" type="checkbox" :checked="modelValue[field.id] === true" :disabled="disabled" :aria-invalid="!!errors(field).length || undefined" :aria-describedby="descriptions(field)" @change="update(field, ($event.target as HTMLInputElement).checked)"><span>{{ field.label }}<small v-if="field.required" class="required"> · required</small></span></label></template>
        <template v-else>
          <label :id="`${elementId(field)}-label`" :for="elementId(field)">{{ field.label }}<span v-if="field.required" class="required"> · required</span><small v-if="field.unit"> ({{ field.unit }})</small></label>
          <textarea v-if="field.kind === 'long_text'" :id="elementId(field)" class="input" rows="5" :value="display(field)" :maxlength="field.max_length ?? 4096" :required="field.required" :disabled="disabled" :aria-invalid="!!errors(field).length || undefined" :aria-describedby="descriptions(field)" @input="update(field, ($event.target as HTMLTextAreaElement).value)" />
          <select v-else-if="field.kind === 'choice'" :id="elementId(field)" class="input" :value="display(field)" :required="field.required" :disabled="disabled" :aria-invalid="!!errors(field).length || undefined" :aria-describedby="descriptions(field)" @change="update(field, ($event.target as HTMLSelectElement).value)"><option value="">Choose an option</option><option v-for="choice in field.choices ?? []" :key="choice.id" :value="choice.id">{{ choice.label }}</option></select>
          <FormDateTime v-else-if="field.kind === 'datetime'" :id="elementId(field)" :model-value="modelValue[field.id]" :timezone="field.timezone" :disabled="disabled" :invalid="!!errors(field).length" @update:model-value="update(field, $event)" />
          <template v-else-if="field.kind === 'attachment'">
            <input :id="elementId(field)" type="file" :accept="field.accepted_content_types?.join(',')" :disabled="disabled || !upload || !!transfers[key(field)]" :aria-describedby="descriptions(field)" @change="uploadFile(field, $event)">
            <small v-if="transfers[key(field)]" role="status">Uploading {{ transfers[key(field)] }}…</small><div v-else-if="modelValue[field.id]" class="attachment-receipt"><span>Attachment received</span><BaseButton v-if="download" type="button" variant="ghost" size="small" @click="downloadFile(field)">Download attachment</BaseButton><BaseButton type="button" class="button button--ghost button--small" :disabled="disabled" @click="update(field, undefined)" variant="ghost" size="small">Remove</BaseButton></div><small v-else-if="!upload">Attachment selection is unavailable here.</small>
          </template>
          <template v-else-if="field.kind === 'reference'">
            <input class="input" type="search" placeholder="Find a resource" :disabled="disabled || !references" :aria-label="`Find ${field.label}`" @input="findReferences(field, ($event.target as HTMLInputElement).value)" @focus="findReferences(field, '')">
            <select :id="elementId(field)" class="input" :value="display(field)" :disabled="disabled || !references" :aria-invalid="!!errors(field).length || undefined" @change="update(field, ($event.target as HTMLSelectElement).value)"><option value="">Choose a resource</option><option v-for="choice in resources[key(field)] ?? []" :key="choice.id" :value="choice.id">{{ choice.label }}</option></select>
          </template>
          <input v-else :id="elementId(field)" class="input" :type="inputKind(field)" :value="display(field)" :step="field.kind === 'integer' ? 1 : field.kind === 'decimal' ? 'any' : undefined" :min="field.minimum ?? undefined" :max="field.maximum ?? undefined" :maxlength="field.max_length ?? 4096" :required="field.required" :disabled="disabled" :aria-invalid="!!errors(field).length || undefined" :aria-describedby="descriptions(field)" @input="update(field, ($event.target as HTMLInputElement).value)">
        </template>
        <small v-if="field.help" :id="`${elementId(field)}-help`" class="form-help">{{ field.help }}</small>
        <ul v-if="errors(field).length" :id="`${elementId(field)}-error`" class="form-errors"><li v-for="message in errors(field)" :key="message">{{ message }}</li></ul>
        <p v-if="transferErrors[key(field)]" class="form-errors" role="alert">{{ transferErrors[key(field)] }}</p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.form-fields{display:grid;gap:1.15rem}.form-field{display:grid;gap:.4rem;min-width:0}.form-field>label,.form-choices legend,.form-group legend{font-size:.85rem;font-weight:650}.form-field .input{background:var(--color-surface,#fff);border:1px solid var(--color-border,#cbd5e1);border-radius:.5rem;color:var(--color-text,#172033);font:inherit;max-width:100%;padding:.65rem .75rem;width:100%}.form-field textarea{resize:vertical}.form-field .input:focus{border-color:var(--color-accent,#3276c8);outline:2px solid color-mix(in srgb,var(--color-accent,#3276c8) 25%,transparent);outline-offset:1px}.form-field--invalid .input{border-color:var(--color-danger,#b91c1c)}.required,.form-help,.form-field>small{color:var(--color-text-muted,#64748b);font-size:.74rem;font-weight:400}.form-check{align-items:flex-start;display:flex;gap:.65rem}.form-check input{accent-color:var(--color-accent,#3276c8);flex:none;margin-top:.2rem}.form-choices,.form-group{border:0;display:grid;gap:.7rem;margin:0;min-width:0;padding:0}.form-choices legend,.form-group legend{margin-bottom:.6rem}.form-row{border:1px solid var(--color-border,#cbd5e1);border-radius:.7rem;display:grid;gap:1rem;padding:1rem}.form-row__heading,.form-row__actions,.attachment-receipt{align-items:center;display:flex;gap:.45rem;justify-content:space-between}.form-row__heading strong{font-size:.8rem}.form-errors{color:var(--color-danger,#b91c1c);font-size:.8rem;margin:0;padding-left:1.2rem}.attachment-receipt{font-size:.8rem;justify-content:flex-start}.form-field input:disabled,.form-field select:disabled,.form-field textarea:disabled{opacity:.65}
</style>
