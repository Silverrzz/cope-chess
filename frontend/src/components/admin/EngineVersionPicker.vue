<script setup lang="ts">
import { computed } from 'vue'

import OptionPicker from '@/components/ui/OptionPicker.vue'

interface EngineChoice {
  id: number
  engine_id?: number
  family_id?: number
  name: string
  version: string
  author?: string
  active?: boolean
}

const props = withDefaults(defineProps<{
  modelValue: string | number
  engines: readonly EngineChoice[]
  label?: string
  placeholder?: string
  disabled?: boolean
}>(), {
  label: 'Engine version',
  placeholder: 'Choose an engine version',
  disabled: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: string | number]
  change: [value: string | number]
}>()

const options = computed(() => {
  const groups = new Map<string, { key: string; name: string; author: string; versions: EngineChoice[] }>()
  for (const engine of props.engines) {
    const family = engine.engine_id ?? engine.family_id
    const key = family === undefined ? engine.name.toLocaleLowerCase() : String(family)
    const group = groups.get(key)
    if (group) group.versions.push(engine)
    else groups.set(key, { key, name: engine.name, author: engine.author ?? '', versions: [engine] })
  }
  return [...groups.values()]
    .sort((left, right) => left.name.localeCompare(right.name, undefined, { sensitivity: 'base' }))
    .map((group) => ({
      value: `engine:${group.key}`,
      label: group.name,
      description: group.author || `${group.versions.length} ${group.versions.length === 1 ? 'version' : 'versions'}`,
      children: group.versions
        .sort((left, right) => right.version.localeCompare(left.version, undefined, { numeric: true, sensitivity: 'base' }))
        .map((engine) => ({
          value: engine.id,
          label: engine.version,
          ...(engine.author && engine.author !== group.author ? { description: engine.author } : {}),
          disabled: engine.active === false,
        })),
    }))
})

function update(value: string | number): void {
  emit('update:modelValue', value)
  emit('change', value)
}
</script>

<template>
  <OptionPicker
    :model-value="modelValue"
    :options="options"
    :label="label"
    :placeholder="placeholder"
    :disabled="disabled"
    icon="engine"
    searchable
    search-placeholder="Search engines or versions…"
    @update:model-value="update"
  />
</template>
