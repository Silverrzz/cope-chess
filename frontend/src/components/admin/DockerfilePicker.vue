<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import AppIcon from '@/components/ui/AppIcon.vue'

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

const pickerId = useId()
const root = ref<HTMLElement | null>(null)
const searchInput = ref<HTMLInputElement | null>(null)
const open = ref(false)
const search = ref('')
const activeGroupKey = ref('')

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
    if (group) {
      group.versions.push({ ...file, version: parsed.version })
    } else {
      grouped.set(parsed.key, {
        key: parsed.key,
        name: parsed.name,
        versions: [{ ...file, version: parsed.version }],
      })
    }
  }
  return [...grouped.values()]
    .map((group) => ({
      ...group,
      versions: group.versions.sort((left, right) => right.version.localeCompare(left.version, undefined, { numeric: true, sensitivity: 'base' })),
    }))
    .sort((left, right) => left.name.localeCompare(right.name, undefined, { sensitivity: 'base' }))
})

const filteredGroups = computed(() => {
  const query = search.value.trim().toLocaleLowerCase()
  if (!query) return groups.value
  return groups.value.filter((group) =>
    group.name.toLocaleLowerCase().includes(query)
      || group.versions.some((version) => version.version.toLocaleLowerCase().includes(query) || version.path.toLocaleLowerCase().includes(query)),
  )
})

const activeGroup = computed(() => groups.value.find((group) => group.key === activeGroupKey.value) ?? null)
const selected = computed(() => {
  for (const group of groups.value) {
    const version = group.versions.find((item) => item.path === props.modelValue)
    if (version) return { group, version }
  }
  return null
})

async function show(): Promise<void> {
  if (props.disabled || !props.files.length) return
  open.value = true
  search.value = ''
  activeGroupKey.value = selected.value?.group.key ?? ''
  await nextTick()
  searchInput.value?.focus()
}

function close(): void {
  open.value = false
  search.value = ''
  activeGroupKey.value = ''
}

function toggle(): void {
  if (open.value) close()
  else void show()
}

function selectVersion(path: string): void {
  emit('update:modelValue', path)
  emit('change', path)
  close()
}

function handleOutside(event: PointerEvent): void {
  if (open.value && !root.value?.contains(event.target as Node)) close()
}

watch(filteredGroups, (available) => {
  if (activeGroupKey.value && !available.some((group) => group.key === activeGroupKey.value)) activeGroupKey.value = ''
})

onMounted(() => document.addEventListener('pointerdown', handleOutside))
onBeforeUnmount(() => document.removeEventListener('pointerdown', handleOutside))
</script>

<template>
  <div ref="root" class="dockerfile-picker" @keydown.esc.stop="close">
    <button
      class="dockerfile-trigger"
      :class="{ 'dockerfile-trigger--open': open }"
      type="button"
      :disabled="disabled || !files.length"
      aria-haspopup="dialog"
      :aria-expanded="open"
      :aria-controls="`${pickerId}-popover`"
      @click="toggle"
      @keydown.down.prevent="show"
    >
      <span v-if="selected" class="dockerfile-trigger__value">
        <strong>{{ selected.group.name }}</strong>
        <small>{{ selected.version.version }}</small>
      </span>
      <span v-else class="dockerfile-trigger__placeholder">{{ files.length ? placeholder : 'No files found in data/engines' }}</span>
      <AppIcon name="chevron-down" :size="17" />
    </button>

    <div v-if="open" :id="`${pickerId}-popover`" class="dockerfile-popover" role="dialog" aria-label="Choose a Dockerfile">
      <div class="dockerfile-search">
        <AppIcon name="search" :size="16" />
        <input ref="searchInput" v-model="search" type="search" autocomplete="off" placeholder="Search engines…" aria-label="Search engines">
      </div>
      <div class="dockerfile-groups" role="listbox" aria-label="Engines">
        <button
          v-for="group in filteredGroups"
          :key="group.key"
          class="dockerfile-group"
          :class="{ 'dockerfile-group--active': activeGroupKey === group.key }"
          type="button"
          role="option"
          :aria-selected="activeGroupKey === group.key"
          @click="activeGroupKey = group.key"
        >
          <span><strong>{{ group.name }}</strong><small>{{ group.versions.length }} {{ group.versions.length === 1 ? 'version' : 'versions' }}</small></span>
          <AppIcon name="chevron-right" :size="16" />
        </button>
        <p v-if="!filteredGroups.length" class="dockerfile-empty">No engines match “{{ search.trim() }}”</p>
      </div>

      <div v-if="activeGroup" class="dockerfile-versions" role="listbox" :aria-label="`${activeGroup.name} versions`">
        <header><span>{{ activeGroup.name }}</span><small>Choose a version</small></header>
        <button
          v-for="version in activeGroup.versions"
          :key="version.path"
          class="dockerfile-version"
          :class="{ 'dockerfile-version--selected': version.path === modelValue }"
          type="button"
          role="option"
          :aria-selected="version.path === modelValue"
          @click="selectVersion(version.path)"
        >
          <span><strong>{{ version.version }}</strong><small>{{ version.path }}</small></span>
          <AppIcon v-if="version.path === modelValue" name="check" :size="16" />
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dockerfile-picker{position:relative}.dockerfile-trigger{align-items:center;background:var(--color-surface-raised);border:1px solid var(--color-border-strong);border-radius:var(--radius-md);color:var(--color-text);cursor:pointer;display:flex;gap:.65rem;justify-content:space-between;min-height:var(--control-height,2.45rem);padding:.5rem .7rem;text-align:left;width:100%}.dockerfile-trigger:hover:not(:disabled),.dockerfile-trigger--open{border-color:var(--color-focus)}.dockerfile-trigger:focus-visible{border-color:var(--color-focus);box-shadow:0 0 0 3px color-mix(in srgb,var(--color-focus) 20%,transparent);outline:0}.dockerfile-trigger:disabled{background:var(--color-surface-sunken);cursor:not-allowed;opacity:.62}.dockerfile-trigger__value{align-items:baseline;display:flex;gap:.45rem;min-width:0}.dockerfile-trigger__value strong{font-size:.74rem}.dockerfile-trigger__value small,.dockerfile-trigger__placeholder{color:var(--color-text-muted);font-size:.71rem}.dockerfile-popover{background:var(--color-surface-raised);border:1px solid var(--color-border);border-radius:var(--radius-md);box-shadow:0 .85rem 2rem rgb(15 23 42 / 18%);left:0;position:absolute;top:calc(100% + .4rem);width:min(20rem,calc(100vw - 2rem));z-index:40}.dockerfile-search{align-items:center;border-bottom:1px solid var(--color-border);color:var(--color-text-muted);display:flex;gap:.5rem;padding:.6rem}.dockerfile-search input{background:transparent;border:0;color:var(--color-text);font:inherit;font-size:.73rem;min-width:0;outline:0;width:100%}.dockerfile-search input::placeholder{color:var(--color-text-faint)}.dockerfile-groups{max-height:18rem;overflow:auto;padding:.35rem}.dockerfile-group,.dockerfile-version{align-items:center;background:transparent;border:0;border-radius:var(--radius-sm,.35rem);color:var(--color-text);cursor:pointer;display:flex;gap:.6rem;justify-content:space-between;padding:.55rem .6rem;text-align:left;width:100%}.dockerfile-group:hover,.dockerfile-group:focus-visible,.dockerfile-group--active,.dockerfile-version:hover,.dockerfile-version:focus-visible{background:var(--color-accent-soft);outline:0}.dockerfile-group>span,.dockerfile-version>span{display:grid;gap:.12rem;min-width:0}.dockerfile-group strong,.dockerfile-version strong{font-size:.73rem}.dockerfile-group small,.dockerfile-version small,.dockerfile-versions header small{color:var(--color-text-muted);font-size:.63rem}.dockerfile-versions{background:var(--color-surface-raised);border:1px solid var(--color-border);border-radius:var(--radius-md);box-shadow:0 .85rem 2rem rgb(15 23 42 / 18%);left:calc(100% + .4rem);max-height:21.5rem;overflow:auto;padding:.35rem;position:absolute;top:0;width:15rem}.dockerfile-versions header{display:grid;gap:.1rem;padding:.45rem .6rem .55rem}.dockerfile-versions header span{font-size:.74rem;font-weight:700}.dockerfile-version small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.dockerfile-version--selected{color:var(--color-accent)}.dockerfile-empty{color:var(--color-text-muted);font-size:.7rem;margin:0;padding:1.25rem .6rem;text-align:center}@media(max-width:48rem){.dockerfile-popover{width:100%}.dockerfile-versions{border:0;border-radius:0;border-top:1px solid var(--color-border);box-shadow:none;max-height:14rem;position:static;width:auto}}
</style>
