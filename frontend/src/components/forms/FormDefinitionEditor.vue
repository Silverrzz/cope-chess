<script setup lang="ts">
import { computed } from 'vue'
import FormFieldEditor from './FormFieldEditor.vue'
import { useSessionStore } from '@/stores/session'
import type { FormDefinition, FormErrors, FormField } from '@/types/forms'

export interface BindingDescription { key: string; label: string; permits_anonymous: boolean; arguments: { name: string; label: string; kinds: string[]; required: boolean }[] }
const props = withDefaults(defineProps<{ modelValue: FormDefinition; bindings: BindingDescription[]; errors?: FormErrors; disabled?: boolean }>(), { errors: () => ({}), disabled: false })
const emit = defineEmits<{ 'update:modelValue': [document: FormDefinition] }>()
const session = useSessionStore()
const binding = computed(() => props.bindings.find((item) => item.key === props.modelValue.binding))
function change(changes: Partial<FormDefinition>): void { emit('update:modelValue', { ...props.modelValue, ...changes }) }
function map(name: string, id: string): void { const mapping = { ...props.modelValue.mapping }; if (id) mapping[name] = id; else delete mapping[name]; change({ mapping }) }
function compatible(kinds: string[]): FormField[] { return props.modelValue.fields.filter((field) => kinds.includes(field.kind)) }
</script>

<template>
  <fieldset class="definition-editor" :disabled="disabled">
    <legend class="visually-hidden">Form definition</legend>
    <label>Title<input class="input" :value="modelValue.title" maxlength="300" @input="change({ title: ($event.target as HTMLInputElement).value })"></label>
    <label>Introduction<textarea class="input" :value="modelValue.description ?? ''" rows="3" maxlength="12000" @input="change({ description: ($event.target as HTMLTextAreaElement).value })" /></label>
    <div class="settings-grid"><label>Who can respond?<select class="input" :value="modelValue.audience" @change="change({ audience: ($event.target as HTMLSelectElement).value as FormDefinition['audience'] })"><option value="authenticated">Signed-in users</option><option value="public" :disabled="!binding?.permits_anonymous">Anyone with the link</option><option value="invited">Invited users</option></select></label><label>Response purpose<select class="input" :value="modelValue.binding" @change="change({ binding: ($event.target as HTMLSelectElement).value, mapping: {} })"><option v-for="item in bindings" :key="item.key" :value="item.key">{{ item.label }}</option></select></label></div>
    <label class="check"><input type="checkbox" :checked="modelValue.allow_amendments !== false" @change="change({ allow_amendments: ($event.target as HTMLInputElement).checked })">Allow submitters to amend their responses</label>
    <p v-if="session.session?.privatise_platform && modelValue.audience === 'public'">This platform currently requires sign-in. Anonymous responses become available when the platform is public.</p>
    <FormFieldEditor :model-value="modelValue.fields" :errors="errors" :disabled="disabled" @update:model-value="change({ fields: $event })" />
    <section class="mapping"><h3>Connect answers to the response purpose</h3><p>Choose which question supplies each allowed input.</p><label v-for="argument in binding?.arguments ?? []" :key="argument.name">{{ argument.label }}{{ argument.required ? ' (required)' : '' }}<select class="input" :value="modelValue.mapping?.[argument.name] ?? ''" @change="map(argument.name, ($event.target as HTMLSelectElement).value)"><option value="">No question selected</option><option v-for="field in compatible(argument.kinds)" :key="field.id" :value="field.id">{{ field.label }}</option></select><span v-for="message in errors[`mapping.${argument.name}`] ?? []" :key="message" class="error">{{ message }}</span></label></section>
    <details><summary>Response limits</summary><label>Submissions per person per hour<input class="input" type="number" min="1" max="1000" :value="modelValue.submissions_per_hour ?? 20" @input="change({ submissions_per_hour: ($event.target as HTMLInputElement).valueAsNumber })"></label></details>
  </fieldset>
</template>

<style scoped>
.definition-editor{border:0;display:grid;gap:1.1rem;margin:0;min-width:0;padding:0}.definition-editor label{display:grid;gap:.35rem;font-size:.85rem}.settings-grid{display:grid;gap:1rem;grid-template-columns:1fr 1fr}.check{align-items:center;display:flex!important;gap:.6rem!important}.mapping{background:var(--color-bg-subtle,transparent);border:1px solid var(--color-border);border-radius:.6rem;display:grid;gap:.75rem;padding:1rem}.mapping h3,.mapping p{margin:0}.mapping h3{font-size:.9rem}.mapping p{font-size:.8rem}summary{cursor:pointer}.error{color:var(--color-danger,#b91c1c)}.visually-hidden{clip-path:inset(50%);height:1px;overflow:hidden;position:absolute;white-space:nowrap;width:1px}@media(max-width:42rem){.settings-grid{grid-template-columns:1fr}}
</style>
