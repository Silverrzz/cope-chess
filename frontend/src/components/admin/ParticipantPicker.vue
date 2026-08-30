<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AppIcon from '@/components/ui/AppIcon.vue'
interface Engine {
  id: number
  engine_id: number
  name: string
  author?: string
  version: string
  source_kind: 'release' | 'commit'
  distribution?: 'managed' | 'worker_local'
  worker_local_count?: number
}

interface EngineGroup {
  id: number
  name: string
  author: string | undefined
  versions: Engine[]
}

const props = withDefaults(defineProps<{ modelValue: number[]; engines: Engine[]; single?: boolean }>(), { single: false })
const emit = defineEmits<{ 'update:modelValue': [value: number[]] }>()

const query = ref('')
const activeEngineId = ref<number | null>(null)
const selected = computed(() => new Set(props.modelValue))
const groups = computed<EngineGroup[]>(() => {
  const grouped = new Map<number, EngineGroup>()
  for (const engine of props.engines) {
    const group = grouped.get(engine.engine_id)
    if (group) group.versions.push(engine)
    else grouped.set(engine.engine_id, {
      id: engine.engine_id,
      name: engine.name,
      author: engine.author,
      versions: [engine],
    })
  }
  return [...grouped.values()]
    .map((group) => ({
      ...group,
      versions: group.versions.sort((left, right) => right.version.localeCompare(left.version, undefined, { numeric: true, sensitivity: 'base' })),
    }))
    .sort((left, right) => left.name.localeCompare(right.name, undefined, { sensitivity: 'base' }))
})
const visibleVersions = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase()
  if (!needle) return props.engines
  return props.engines.filter((engine) => `${engine.name} ${engine.author ?? ''} ${engine.version ?? ''}`.toLocaleLowerCase().includes(needle))
})
const visibleGroups = computed(() => {
  const visibleIds = new Set(visibleVersions.value.map((engine) => engine.engine_id))
  return groups.value.filter((group) => visibleIds.has(group.id))
})
const activeGroup = computed(() => groups.value.find((group) => group.id === activeEngineId.value) ?? null)

function selectedVersions(group: EngineGroup): Engine[] {
  return group.versions.filter((version) => selected.value.has(version.id))
}

function selectionLabel(group: EngineGroup): string {
  const versions = selectedVersions(group)
  if (!versions.length) return `${group.versions.length} ${group.versions.length === 1 ? 'version' : 'versions'}`
  if (versions.length === 1) return versions[0]?.version ?? ''
  return `${versions.length} versions selected`
}

function toggle(engineId: number): void {
  if (props.single) {
    emit('update:modelValue', selected.value.has(engineId) ? [] : [engineId])
    return
  }
  const next = new Set(props.modelValue)
  if (next.has(engineId)) next.delete(engineId)
  else next.add(engineId)
  emit('update:modelValue', [...next])
}

function selectVisible(): void {
  emit('update:modelValue', [...new Set([...props.modelValue, ...visibleVersions.value.map((engine) => engine.id)])])
}

function selectGroup(group: EngineGroup): void {
  emit('update:modelValue', [...new Set([...props.modelValue, ...group.versions.map((engine) => engine.id)])])
}

function clearGroup(group: EngineGroup): void {
  const groupIds = new Set(group.versions.map((engine) => engine.id))
  emit('update:modelValue', props.modelValue.filter((engineId) => !groupIds.has(engineId)))
}

function clear(): void {
  emit('update:modelValue', [])
}

watch(visibleGroups, (available) => {
  if (activeEngineId.value !== null && !available.some((group) => group.id === activeEngineId.value)) activeEngineId.value = null
})
</script>

<template>
  <div class="participant-picker">
    <div class="participant-picker__toolbar">
      <label class="participant-search">
        <span class="sr-only">Search engines</span>
        <AppIcon name="search" :size="16" />
        <input v-model="query" class="input" type="search" placeholder="Search by engine, author, or version">
      </label>
      <span class="participant-picker__count" aria-live="polite">{{ modelValue.length ? (single ? '1 selected' : `${modelValue.length} selected`) : 'None selected' }}</span>
      <button v-if="visibleVersions.length && !single" class="button button--ghost button--small" type="button" @click="selectVisible">Select visible</button>
      <button v-if="modelValue.length" class="button button--ghost button--small" type="button" @click="clear">Clear</button>
    </div>

    <div v-if="visibleGroups.length" class="participant-browser" :class="{ 'participant-browser--open': activeGroup }">
      <div class="participant-engines" role="listbox" aria-label="Tournament engines">
        <button
          v-for="group in visibleGroups"
          :key="group.id"
          class="participant-engine"
          :class="{
            'participant-engine--active': activeEngineId === group.id,
            'participant-engine--selected': selectedVersions(group).length,
          }"
          type="button"
          role="option"
          :aria-selected="activeEngineId === group.id"
          @click="activeEngineId = group.id"
        >
          <span class="participant-engine__identity">
            <strong>{{ group.name }}</strong>
            <small v-if="group.author">{{ group.author }}</small>
          </span>
          <span class="participant-engine__selection" :class="{ selected: selectedVersions(group).length }">
            {{ selectionLabel(group) }}
          </span>
          <AppIcon name="chevron-right" :size="16" />
        </button>
      </div>

      <aside v-if="activeGroup" class="participant-versions" :aria-label="`${activeGroup.name} versions`">
        <header class="participant-versions__heading">
          <div>
            <strong>{{ activeGroup.name }}</strong>
            <small>{{ single ? 'Choose a version' : 'Choose participating versions' }}</small>
          </div>
          <button class="participant-versions__close" type="button" :aria-label="`Close ${activeGroup.name} versions`" @click="activeEngineId = null">
            <AppIcon name="close" :size="16" />
          </button>
        </header>
        <div class="participant-versions__actions">
          <span>{{ single ? `${activeGroup.versions.length} available` : `${selectedVersions(activeGroup).length} of ${activeGroup.versions.length} selected` }}</span>
          <div v-if="!single">
            <button type="button" @click="selectGroup(activeGroup)">Select all</button>
            <button v-if="selectedVersions(activeGroup).length" type="button" @click="clearGroup(activeGroup)">Clear</button>
          </div>
        </div>
        <div class="participant-version-list" role="group" :aria-label="`${activeGroup.name} version selection`">
          <label
            v-for="version in activeGroup.versions"
            :key="version.id"
            class="participant-version"
            :class="{ 'participant-version--selected': selected.has(version.id) }"
          >
            <input :type="single ? 'radio' : 'checkbox'" :checked="selected.has(version.id)" @change="toggle(version.id)">
            <span>
              <strong>{{ version.version }}</strong>
              <small :class="{ 'participant-version__local-offline': version.distribution === 'worker_local' && !(version.worker_local_count ?? 0) }">{{ version.distribution === 'worker_local' ? `Worker-local · ${version.worker_local_count ?? 0} connected` : version.source_kind === 'release' ? 'Release' : 'Commit' }}</small>
            </span>
            <AppIcon v-if="selected.has(version.id)" name="check" :size="16" />
          </label>
        </div>
      </aside>

      <div v-else class="participant-versions participant-versions--empty">
        <AppIcon name="engine" :size="24" />
        <strong>Select an engine</strong>
        <span>Its available versions will appear here.</span>
      </div>
    </div>
    <p v-else class="participant-picker__empty">No engines match “{{ query }}”.</p>
  </div>
</template>

<style scoped>
.participant-picker { display: grid; gap: .8rem; }
.participant-picker__toolbar { align-items: center; display: flex; flex-wrap: wrap; gap: .55rem; }
.participant-search { flex: 1 1 18rem; position: relative; }
.participant-search > :first-child { color: var(--color-text-muted, #64748b); left: .75rem; pointer-events: none; position: absolute; top: 50%; transform: translateY(-50%); }
.participant-search .input { padding-left: 2.25rem; width: 100%; }
.participant-picker__count { color: var(--color-text-muted, #64748b); font-size: .82rem; font-weight: 600; white-space: nowrap; }
.participant-browser { border: 1px solid var(--color-border, #d9e0ea); border-radius: var(--radius-md, .6rem); display: grid; grid-template-columns: minmax(13rem, .9fr) minmax(14rem, 1.1fr); min-height: 18rem; overflow: hidden; }
.participant-engines { background: var(--color-surface, #fff); max-height: 23rem; overflow: auto; padding: .35rem; }
.participant-engine { align-items: center; background: transparent; border: 0; border-radius: var(--radius-sm, .35rem); color: var(--color-text, #17202a); cursor: pointer; display: grid; gap: .6rem; grid-template-columns: minmax(0, 1fr) auto auto; padding: .7rem; text-align: left; width: 100%; }
.participant-engine:hover, .participant-engine:focus-visible, .participant-engine--active { background: var(--color-surface-subtle, #f6f8fb); outline: 0; }
.participant-engine--active { box-shadow: inset 3px 0 var(--color-accent, #315fcc); }
.participant-engine__identity { display: grid; min-width: 0; }
.participant-engine__identity strong, .participant-engine__identity small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.participant-engine__identity strong { font-size: .8rem; }
.participant-engine__identity small { color: var(--color-text-muted, #64748b); font-size: .68rem; margin-top: .15rem; }
.participant-engine__selection { color: var(--color-text-muted, #64748b); font-size: .68rem; max-width: 8rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.participant-engine__selection.selected { color: var(--color-accent, #315fcc); font-weight: 700; }
.participant-engine > :last-child { color: var(--color-text-muted, #64748b); }
.participant-engine--selected > :last-child, .participant-engine--active > :last-child { color: var(--color-accent, #315fcc); }
.participant-versions { background: color-mix(in srgb, var(--color-accent, #315fcc) 2.5%, var(--color-surface, #fff)); border-left: 1px solid var(--color-border, #d9e0ea); display: grid; grid-template-rows: auto auto minmax(0, 1fr); min-width: 0; }
.participant-versions__heading { align-items: center; border-bottom: 1px solid var(--color-border, #d9e0ea); display: flex; justify-content: space-between; padding: .75rem .85rem; }
.participant-versions__heading > div { display: grid; gap: .12rem; min-width: 0; }
.participant-versions__heading strong { font-size: .82rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.participant-versions__heading small { color: var(--color-text-muted, #64748b); font-size: .66rem; }
.participant-versions__close { align-items: center; background: transparent; border: 0; border-radius: var(--radius-sm, .35rem); color: var(--color-text-muted, #64748b); cursor: pointer; display: inline-flex; justify-content: center; padding: .3rem; }
.participant-versions__close:hover, .participant-versions__close:focus-visible { background: var(--color-surface-subtle, #f6f8fb); color: var(--color-text, #17202a); outline: 0; }
.participant-versions__actions { align-items: center; display: flex; gap: .7rem; justify-content: space-between; padding: .55rem .85rem .25rem; }
.participant-versions__actions > span { color: var(--color-text-muted, #64748b); font-size: .65rem; }
.participant-versions__actions > div { display: flex; gap: .6rem; }
.participant-versions__actions button { background: none; border: 0; color: var(--color-accent, #315fcc); cursor: pointer; font-size: .65rem; padding: 0; }
.participant-version-list { align-content: start; display: grid; gap: .35rem; max-height: 19rem; overflow: auto; padding: .45rem; }
.participant-version { align-items: center; background: var(--color-surface, #fff); border: 1px solid var(--color-border, #d9e0ea); border-radius: var(--radius-sm, .35rem); cursor: pointer; display: flex; gap: .6rem; min-width: 0; padding: .65rem .7rem; }
.participant-version:hover { border-color: color-mix(in srgb, var(--color-accent, #315fcc) 45%, var(--color-border, #d9e0ea)); }
.participant-version:focus-within { box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-accent, #315fcc) 18%, transparent); }
.participant-version--selected { background: color-mix(in srgb, var(--color-accent, #315fcc) 7%, var(--color-surface, #fff)); border-color: var(--color-accent, #315fcc); }
.participant-version input { flex: 0 0 auto; height: 1rem; margin: 0; width: 1rem; }
.participant-version > span { display: grid; flex: 1 1 auto; min-width: 0; }
.participant-version strong, .participant-version small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.participant-version strong { font-size: .76rem; }
.participant-version small { color: var(--color-text-muted, #64748b); font-size: .64rem; margin-top: .12rem; }
.participant-version small.participant-version__local-offline { color: var(--color-warning, #92400e); }
.participant-version > .app-icon { color: var(--color-accent, #315fcc); }
.participant-versions--empty { align-content: center; color: var(--color-text-muted, #64748b); gap: .3rem; justify-items: center; padding: 2rem; text-align: center; }
.participant-versions--empty strong { color: var(--color-text, #17202a); font-size: .8rem; margin-top: .25rem; }
.participant-versions--empty span { font-size: .7rem; }
.participant-picker__empty { color: var(--color-text-muted, #64748b); margin: .5rem 0; text-align: center; }
@media (max-width: 42rem) {
  .participant-browser, .participant-browser--open { grid-template-columns: 1fr; }
  .participant-browser--open .participant-engines { display: none; }
  .participant-engines, .participant-version-list { max-height: 19rem; }
  .participant-versions { border-left: 0; min-height: 18rem; }
  .participant-versions--empty { display: none; }
  .participant-engine__selection { max-width: 7rem; }
}
</style>
