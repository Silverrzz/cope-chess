<script setup lang="ts">
import { ref, watch } from 'vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import FormMarkdownEditor from './FormMarkdownEditor.vue'
import { booleanKinds, contentKinds, type FormChoice, type FormErrors, type FormField, type FormKind } from '@/types/forms'

const props = withDefaults(defineProps<{
  modelValue: FormField[]; errors?: FormErrors; path?: string[]; depth?: number; disabled?: boolean
}>(), { errors: () => ({}), path: () => [], depth: 1, disabled: false })
const emit = defineEmits<{ 'update:modelValue': [fields: FormField[]] }>()
const groups: { label: string; fields: [FormKind, string][] }[] = [
  { label: 'Text and contact', fields: [['text', 'Short text'], ['long_text', 'Paragraph'], ['email', 'Email'], ['url', 'Website'], ['phone', 'Phone number']] },
  { label: 'Choices and feedback', fields: [['choice', 'Single choice'], ['dropdown', 'Dropdown'], ['multiple_choice', 'Multiple choices'], ['yes_no', 'Yes or no'], ['checkbox', 'Checkbox'], ['consent', 'Consent'], ['rating', 'Rating'], ['scale', 'Number scale'], ['ranking', 'Ranking'], ['matrix', 'Choice grid']] },
  { label: 'Numbers and dates', fields: [['integer', 'Whole number'], ['decimal', 'Decimal number'], ['date', 'Date'], ['time', 'Time'], ['datetime', 'Date and time']] },
  { label: 'Files and Cope resources', fields: [['attachment', 'File upload'], ['chess_game', 'Tournament game'], ['rating_list', 'Rating list'], ['reference', 'Internal object'], ['group', 'Repeatable group']] },
  { label: 'Layout and content', fields: [['section', 'Section heading'], ['markdown', 'Rich content']] },
]
const labels = new Map(groups.flatMap(group => group.fields))
const options = new Set<FormKind>(['choice', 'dropdown', 'multiple_choice', 'ranking', 'matrix'])
const numbers = new Set<FormKind>(['integer', 'decimal', 'rating', 'scale'])
const text = new Set<FormKind>(['text', 'long_text', 'email', 'url', 'phone'])
const media = [
  ['text/plain', 'Text and chess files'],
  ['image/png', 'PNG images'],
  ['image/jpeg', 'JPEG images'],
  ['image/webp', 'WebP images'],
  ['application/pdf', 'PDF documents'],
  ['application/octet-stream', 'Other files for signed-in respondents'],
] as const
const active = ref('')
const addKind = ref<FormKind>('text')
const dragId = ref('')
const dropId = ref('')

function startDrag(field: FormField, event: DragEvent): void {
  if (props.disabled || !event.dataTransfer) return
  dragId.value = field.id
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('text/plain', field.id)
}

function allowDrop(field: FormField, event: DragEvent): void {
  if (!dragId.value || props.disabled || field.id === dragId.value) return
  event.preventDefault()
  dropId.value = field.id
}

function finishDrag(): void {
  dragId.value = ''
  dropId.value = ''
}

function drop(index: number, event: DragEvent): void {
  if (!dragId.value || props.disabled) return
  event.preventDefault()
  const source = props.modelValue.findIndex(field => field.id === dragId.value)
  finishDrag()
  if (source >= 0) move(source, index - source)
}
watch(() => props.modelValue, (fields) => {
  if (!fields.some(field => field.id === active.value)) active.value = fields[0]?.id ?? ''
}, { immediate: true })

function id(prefix: string): string {
  return `${prefix}_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`
}

function make(kind: FormKind, previous?: FormField): FormField {
  const block: FormField = { id: id('field'), kind, label: previous?.label ?? labels.get(kind) ?? 'Question' }
  if (!contentKinds.has(kind)) {
    block.help = previous?.help ?? ''
    block.required = kind === 'consent' || previous?.required || false
  }
  block.when = previous?.when ?? null
  if (options.has(kind)) block.choices = [{ id: id('option'), label: 'Option 1' }, { id: id('option'), label: 'Option 2' }]
  if (kind === 'matrix') block.rows = [{ id: id('row'), label: 'First statement' }]
  if (kind === 'group') block.fields = [make('text')]
  if (kind === 'reference') block.reference_type = 'engine'
  return block
}

function update(index: number, changes: Partial<FormField>): void {
  emit('update:modelValue', props.modelValue.map((field, position) => position === index ? { ...field, ...changes } : field))
}

function add(): void {
  const field = make(addKind.value)
  active.value = field.id
  emit('update:modelValue', [...props.modelValue, field])
}

function duplicate(index: number): void {
  const field = props.modelValue[index]
  if (!field) return
  const copy = { ...field, id: id('field'), label: `${field.label} (copy)`.slice(0, 300) }
  const fields = [...props.modelValue]
  fields.splice(index + 1, 0, copy)
  active.value = copy.id
  emit('update:modelValue', fields)
}

function remove(index: number): void {
  emit('update:modelValue', props.modelValue.filter((_, position) => position !== index))
}

function move(index: number, direction: number): void {
  const fields = [...props.modelValue]
  const field = fields[index]
  if (!field || index + direction < 0 || index + direction >= fields.length) return
  fields.splice(index, 1)
  fields.splice(index + direction, 0, field)
  emit('update:modelValue', fields)
}

function changeKind(index: number, kind: FormKind): void {
  const field = make(kind, props.modelValue[index])
  active.value = field.id
  emit('update:modelValue', props.modelValue.map((previous, position) => position === index ? field : previous))
}

function updateOptions(index: number, list: 'choices' | 'rows', values: FormChoice[]): void {
  update(index, { [list]: values })
}

function controls(field: FormField): FormField[] {
  return props.modelValue.filter(candidate => candidate.id !== field.id && !candidate.when &&
    (booleanKinds.has(candidate.kind) || ['choice', 'dropdown'].includes(candidate.kind)))
}

function controlFor(field: FormField): FormField | undefined {
  return props.modelValue.find(candidate => candidate.id === field.when?.field)
}

function selectCondition(index: number, controlId: string): void {
  const control = props.modelValue.find(field => field.id === controlId)
  update(index, { when: control ? { field: control.id, equals: booleanKinds.has(control.kind) ? true : control.choices?.[0]?.id ?? '' } : null })
}

function changeExpected(index: number, value: string): void {
  const field = props.modelValue[index]
  if (!field?.when) return
  const control = controlFor(field)
  update(index, { when: { field: field.when.field, equals: control && booleanKinds.has(control.kind) ? value === 'true' : value } })
}

function messages(field: FormField, index: number): string[] {
  const prefixes = [[...props.path, field.id].join('.'), [...props.path, 'fields', String(index)].join('.')]
  return [...new Set(Object.entries(props.errors).filter(([key]) => prefixes.some(prefix => key === prefix || key.startsWith(prefix + '.'))).flatMap(([, values]) => values))]
}

function value(event: Event): string { return (event.target as HTMLInputElement).value }
function number(event: Event, fallback = 0): number { return (event.target as HTMLInputElement).value === '' ? fallback : Number(value(event)) }

function mediaChange(index: number, field: FormField, type: string, enabled: boolean): void {
  const values = new Set(field.accepted_content_types)
  if (enabled) values.add(type)
  else values.delete(type)
  update(index, { accepted_content_types: [...values] })
}
</script>

<template>
  <div class="field-editor">
    <article v-for="(field, index) in modelValue" :key="field.id" class="question-card" :class="{ 'is-active': active === field.id, 'is-drop-target': dropId === field.id }"
      @dragover="allowDrop(field, $event)" @drop="drop(index, $event)">
      <header class="question-heading">
        <span class="drag-handle" :draggable="!disabled" aria-hidden="true" title="Drag to reorder"
          @dragstart="startDrag(field, $event)" @dragend="finishDrag">⠿</span>
        <button type="button" class="question-toggle" :aria-expanded="active === field.id" @click="active = active === field.id ? '' : field.id">
          <span class="question-number">{{ index + 1 }}</span>
          <span><strong>{{ field.label || 'Untitled block' }}</strong><small>{{ labels.get(field.kind) }}<template v-if="field.required"> · Required</template></small></span>
        </button>
        <div class="question-actions">
          <BaseButton type="button" variant="ghost" size="small" :disabled="disabled || index === 0" :aria-label="`Move block ${index + 1} up`" @click="move(index, -1)">↑</BaseButton>
          <BaseButton type="button" variant="ghost" size="small" :disabled="disabled || index === modelValue.length - 1" :aria-label="`Move block ${index + 1} down`" @click="move(index, 1)">↓</BaseButton>
        </div>
      </header>
      <fieldset v-if="active === field.id" class="question-body" :disabled="disabled">
        <div class="question-grid">
          <label>{{ contentKinds.has(field.kind) ? 'Heading' : 'Question' }}<input class="input" :value="field.label" maxlength="300" @input="update(index, { label: value($event) })"></label>
          <label>Block type<select class="input" :value="field.kind" @change="changeKind(index, value($event) as FormKind)">
            <optgroup v-for="group in groups" :key="group.label" :label="group.label">
              <option v-for="[kind, label] in group.fields" :key="kind" :value="kind" :disabled="kind === 'group' && depth >= 3">{{ label }}</option>
            </optgroup>
          </select></label>
        </div>
        <FormMarkdownEditor v-if="contentKinds.has(field.kind)" :model-value="field.content" label="Content" :max-length="16000" :disabled="disabled" @update:model-value="update(index, { content: $event })" />
        <template v-else>
          <label>Help text <textarea class="input" :value="field.help ?? ''" rows="2" maxlength="4000" @input="update(index, { help: value($event) })" /></label>
          <label class="check"><input type="checkbox" :checked="field.required" @change="update(index, { required: ($event.target as HTMLInputElement).checked })">Required</label>
        </template>
        <template v-if="options.has(field.kind)">
          <div v-for="list in (field.kind === 'matrix' ? ['rows', 'choices'] as const : ['choices'] as const)" :key="list" class="options">
            <strong>{{ list === 'rows' ? 'Grid rows' : field.kind === 'matrix' ? 'Grid columns' : 'Options' }}</strong>
            <div v-for="(item, itemIndex) in field[list] ?? []" :key="item.id" class="option">
              <input class="input" :value="item.label" :aria-label="`${list} ${itemIndex + 1}`" maxlength="300" @input="updateOptions(index, list, (field[list] ?? []).map(entry => entry.id === item.id ? { ...entry, label: value($event) } : entry))">
              <BaseButton type="button" variant="ghost" size="small" @click="updateOptions(index, list, (field[list] ?? []).filter(entry => entry.id !== item.id))">Remove</BaseButton>
            </div>
            <BaseButton type="button" variant="secondary" size="small" :disabled="(field[list]?.length ?? 0) >= (list === 'rows' ? 30 : field.kind === 'matrix' || field.kind === 'ranking' ? 20 : 500)"
              @click="updateOptions(index, list, [...(field[list] ?? []), { id: id('item'), label: list === 'rows' ? 'New row' : 'New option' }])">Add {{ list === 'rows' ? 'row' : 'option' }}</BaseButton>
          </div>
        </template>
        <FormFieldEditor v-if="field.kind === 'group'" :model-value="field.fields ?? []" :path="[...path, field.id]" :depth="depth + 1" :errors="errors" :disabled="disabled" @update:model-value="update(index, { fields: $event })" />
        <details class="question-rules">
          <summary>Conditions and limits</summary>
          <div class="rule-fields">
            <label>Show this block<select class="input" :value="field.when?.field ?? ''" @change="selectCondition(index, value($event))"><option value="">Always</option><option v-for="control in controls(field)" :key="control.id" :value="control.id">Depending on “{{ control.label }}”</option></select></label>
            <label v-if="field.when">Expected answer<select class="input" :value="String(field.when.equals)" @change="changeExpected(index, value($event))">
              <template v-if="controlFor(field) && booleanKinds.has(controlFor(field)!.kind)"><option value="true">Yes / checked</option><option value="false">No / unchecked</option></template>
              <option v-for="choice in controlFor(field)?.choices ?? []" v-else :key="choice.id" :value="choice.id">{{ choice.label }}</option>
            </select></label>
            <div v-if="text.has(field.kind)" class="question-grid">
              <label>Minimum characters<input class="input" type="number" min="0" max="65536" :value="field.min_length ?? 0" @input="update(index, { min_length: number($event) })"></label>
              <label>Maximum characters<input class="input" type="number" min="1" max="65536" :value="field.max_length ?? 4096" @input="update(index, { max_length: number($event, 4096) })"></label>
            </div>
            <div v-if="numbers.has(field.kind)" class="question-grid">
              <label>Minimum<input class="input" type="number" :step="field.kind === 'decimal' ? 'any' : 1" :placeholder="field.kind === 'rating' ? '1' : field.kind === 'scale' ? '0' : ''" :value="field.minimum ?? ''" @input="update(index, { minimum: value($event) || null })"></label>
              <label>Maximum<input class="input" type="number" :step="field.kind === 'decimal' ? 'any' : 1" :placeholder="field.kind === 'rating' ? '5' : field.kind === 'scale' ? '10' : ''" :value="field.maximum ?? ''" @input="update(index, { maximum: value($event) || null })"></label>
              <label v-if="field.kind === 'integer' || field.kind === 'decimal'">Unit<input class="input" :value="field.unit ?? ''" maxlength="80" @input="update(index, { unit: value($event) })"></label>
              <label v-if="field.kind === 'decimal'">Decimal places<input class="input" type="number" min="0" max="12" :value="field.precision ?? 6" @input="update(index, { precision: number($event, 6) })"></label>
            </div>
            <div v-if="field.kind === 'group' || field.kind === 'multiple_choice'" class="question-grid">
              <label>Minimum count<input class="input" type="number" min="0" max="100" :value="field.min_items ?? 0" @input="update(index, { min_items: number($event) })"></label>
              <label>Maximum count<input class="input" type="number" min="1" max="100" :value="field.max_items ?? 20" @input="update(index, { max_items: number($event, 20) })"></label>
            </div>
            <label v-if="field.kind === 'datetime'">Timezone<input class="input" :value="field.timezone ?? ''" placeholder="Visitor’s timezone or Europe/London" @input="update(index, { timezone: value($event) || null })"></label>
            <template v-if="field.kind === 'attachment'">
              <label>Maximum upload size (MiB)<input class="input" type="number" min="0.1" max="1024" step="0.1" :value="(field.max_bytes ?? 10485760) / 1048576" @input="update(index, { max_bytes: Math.round(number($event, 10) * 1048576) })"></label>
              <strong>Allowed file types</strong>
              <label v-for="[type, label] in media" :key="type" class="check"><input type="checkbox" :checked="field.accepted_content_types?.includes(type)" @change="mediaChange(index, field, type, ($event.target as HTMLInputElement).checked)">{{ label }}</label>
              <small>Leave all unchecked to use the default file policy. Guest uploads use the allowed image, PDF, and text formats.</small>
            </template>
            <label v-if="field.kind === 'reference'">Internal object type<input class="input" :value="field.reference_type ?? ''" @input="update(index, { reference_type: value($event) })"></label>
          </div>
        </details>
        <div class="block-actions">
          <BaseButton type="button" variant="ghost" size="small" :disabled="disabled || modelValue.length >= 100" @click="duplicate(index)">Duplicate</BaseButton>
          <BaseButton type="button" variant="ghost" size="small" @click="remove(index)">Remove block</BaseButton>
        </div>
      </fieldset>
      <ul v-if="messages(field, index).length" class="editor-errors"><li v-for="message in messages(field, index)" :key="message">{{ message }}</li></ul>
    </article>
    <div class="palette">
      <label>Add a block<select v-model="addKind" class="input" :disabled="disabled">
        <optgroup v-for="group in groups" :key="group.label" :label="group.label">
          <option v-for="[kind, label] in group.fields" :key="kind" :value="kind" :disabled="kind === 'group' && depth >= 3">{{ label }}</option>
        </optgroup>
      </select></label>
      <BaseButton type="button" variant="secondary" :disabled="disabled || modelValue.length >= 100" @click="add">Add block</BaseButton>
    </div>
  </div>
</template>

<style scoped>
.field-editor, .question-body, .rule-fields, .options { display: grid; gap: .85rem; }
.question-card { border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface-raised); min-width: 0; }
.question-card.is-drop-target { outline: 2px solid var(--color-accent); }
.drag-handle { color: var(--color-text-muted); font-size: 1.1rem; cursor: grab; user-select: none; }
.question-card.is-active { border-color: var(--color-border-strong); }
.question-heading, .question-toggle, .question-actions, .option, .block-actions, .palette { display: flex; align-items: center; gap: .55rem; }
.question-heading { padding: .65rem .75rem; }
.question-toggle { flex: 1; min-width: 0; color: inherit; background: none; border: 0; text-align: left; cursor: pointer; }
.question-toggle span:last-child { min-width: 0; display: grid; }
.question-toggle strong { overflow-wrap: anywhere; }
.question-toggle small { color: var(--color-text-muted); font-size: .72rem; }
.question-number { display: grid; place-items: center; flex: 0 0 1.8rem; height: 1.8rem; border-radius: .5rem; background: var(--color-accent-soft); color: var(--color-accent); font-size: .75rem; font-weight: 700; }
.question-body { border: 0; border-top: 1px solid var(--color-border); margin: 0; padding: 1rem; min-width: 0; }
.question-body label, .palette label { display: grid; gap: .35rem; font-size: .85rem; }
.question-grid { display: grid; grid-template-columns: 1fr 1fr; gap: .8rem; }
.check { display: flex !important; align-items: center; gap: .5rem !important; }
.option input, .palette label { flex: 1; min-width: 0; }
.options > button { justify-self: start; }
.question-rules summary { cursor: pointer; font-size: .85rem; }
.rule-fields { padding-block: .85rem 0; }
.block-actions { justify-content: flex-end; border-top: 1px solid var(--color-border); padding-top: .55rem; }
.palette { align-items: end; padding: .9rem; border: 1px dashed var(--color-border-strong); border-radius: var(--radius-lg); }
.editor-errors { color: var(--color-danger); padding-inline-start: 2rem; font-size: .85rem; }
@media (max-width: 42rem) { .question-grid { grid-template-columns: 1fr; } }
</style>
