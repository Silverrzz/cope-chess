<script setup lang="ts">
import { computed } from 'vue'

import OptionPicker from '@/components/ui/OptionPicker.vue'

interface DockerfileEntry {
  path: string
  size: number
}

interface DockerfileVersion extends DockerfileEntry {
  version: string
}

interface DockerfileGroup {
  key: string
  name: string
  versions: DockerfileVersion[]
}

const props = withDefaults(defineProps<{
  modelValue: string
  files: readonly DockerfileEntry[]
  disabled?: boolean
  placeholder?: string
}>(), {
  disabled: false,
  placeholder: 'Choose a Dockerfile',
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
  change: [value: string]
}>()

function displayName(value: string): string {
  return value
    .split(/[-_]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function splitFile(file: DockerfileEntry): { key: string; name: string; version: string } {
  const filename = file.path.split('/').at(-1) ?? file.path
  const stem = filename.replace(/\.Dockerfile$/i, '')
  const numericVersionStart = stem.search(/-(?=\d)/)
  const separator = numericVersionStart >= 0 ? numericVersionStart : stem.lastIndexOf('-')
  const engine = separator > 0 ? stem.slice(0, separator) : stem
  const version = separator > 0 ? stem.slice(separator + 1) : 'Default'
  return { key: engine.toLocaleLowerCase(), name: displayName(engine), version }
}

const groups = computed<DockerfileGroup[]>(() => {
  const grouped = new Map<string, DockerfileGroup>()
  for (const file of props.files) {
    const parsed = splitFile(file)
    const group = grouped.get(parsed.key)
    if (group) group.versions.push({ ...file, version: parsed.version })
    else grouped.set(parsed.key, { key: parsed.key, name: parsed.name, versions: [{ ...file, version: parsed.version }] })
  }
  return [...grouped.values()]
    .map((group) => ({
      ...group,
      versions: group.versions.sort((left, right) => right.version.localeCompare(left.version, undefined, { numeric: true, sensitivity: 'base' })),
    }))
    .sort((left, right) => left.name.localeCompare(right.name, undefined, { sensitivity: 'base' }))
})

const options = computed(() => groups.value.map((group) => ({
  value: group.key,
  label: group.name,
  description: `${group.versions.length} ${group.versions.length === 1 ? 'version' : 'versions'}`,
  children: group.versions.map((version) => ({
    value: version.path,
    label: version.version,
    description: version.path,
  })),
})))

function update(value: string | number): void {
  const path = String(value)
  emit('update:modelValue', path)
  emit('change', path)
}
</script>

<template>
  <OptionPicker
    :model-value="modelValue"
    :options="options"
    :disabled="disabled"
    :placeholder="files.length ? placeholder : 'No files found in data/engines'"
    label="Choose a Dockerfile"
    icon="engine"
    searchable
    search-placeholder="Search engines…"
    @update:model-value="update"
  />
</template>
