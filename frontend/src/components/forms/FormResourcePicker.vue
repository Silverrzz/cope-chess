<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { gameResource, ratingsResource, resourceChoices, type ResourceKind } from '@/api/form-resources'
import type { FormChoice } from '@/types/forms'

const props = defineProps<{ kind: ResourceKind; modelValue?: string; id?: string; disabled?: boolean | undefined; describedBy?: string | undefined }>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; selected: [choice: FormChoice] }>()
const input = ref('')
const tournament = ref<FormChoice | null>(null)
const options = ref<FormChoice[]>([])
const current = ref<FormChoice | null>(null)
const failure = ref('')
const pending = ref(false)
const open = ref(false)
const searchKind = computed(() => props.kind === 'game' && !tournament.value ? 'tournament' : props.kind)
const searchLabel = computed(() => searchKind.value === 'tournament' ? 'Find a tournament' : searchKind.value === 'game' ? 'Find a game by ID or player' : 'Find a rating list')

watch(() => props.modelValue, async (value, _, cleanup) => {
  if (!value || current.value?.id === value) return
  const controller = new AbortController()
  cleanup(() => controller.abort())
  current.value = { id: value, label: props.kind === 'game' ? `Game #${value.split('/')[1] ?? value}` : `Rating list #${value}` }
  try {
    const item = await (props.kind === 'game' ? gameResource(value, controller.signal) : ratingsResource(value, controller.signal))
    if (!controller.signal.aborted) current.value = { id: value, label: item.title }
  } catch { }
}, { immediate: true })

watch([input, searchKind, tournament, open], ([query, kind, parent, expanded], _, cleanup) => {
  if (!expanded) return
  const controller = new AbortController()
  pending.value = true
  failure.value = ''
  options.value = []
  const timer = window.setTimeout(async () => {
    try {
      const result = await resourceChoices(kind, query.trim(), parent?.id, controller.signal)
      if (!controller.signal.aborted) options.value = result.items
    } catch {
      if (!controller.signal.aborted) failure.value = 'Could not load matching resources. Try another search.'
    } finally {
      if (!controller.signal.aborted) pending.value = false
    }
  }, 180)
  cleanup(() => { window.clearTimeout(timer); controller.abort() })
})

function choose(choice: FormChoice): void {
  if (searchKind.value === 'tournament') {
    tournament.value = choice
    input.value = ''
    return
  }
  current.value = choice
  emit('update:modelValue', choice.id)
  emit('selected', choice)
  open.value = false
  tournament.value = null
  input.value = ''
}

function clear(): void {
  emit('update:modelValue', '')
  current.value = null
  open.value = false
}
</script>

<template>
  <div class="resource-picker">
    <div v-if="modelValue && !open" class="resource-selection">
      <span>{{ current?.label ?? modelValue }}</span>
      <button type="button" :disabled="disabled" @click="open = true">Change</button>
      <button type="button" :disabled="disabled" @click="clear">Clear</button>
    </div>
    <template v-else>
      <button v-if="tournament" type="button" class="tournament" :disabled="disabled" @click="tournament = null; input = ''">← {{ tournament.label }}</button>
      <input :id="id" v-model="input" class="input" type="search" :placeholder="searchLabel" :aria-label="searchLabel" :aria-describedby="describedBy" :disabled="disabled" maxlength="100" @focus="open = true" @input="open = true" @keydown.esc="open = false">
      <div v-if="open" class="resource-results" aria-live="polite">
        <p v-if="pending">Looking up resources…</p>
        <p v-else-if="failure">{{ failure }}</p>
        <p v-else-if="!options.length">No matches.</p>
        <ul v-else>
          <li v-for="option in options" :key="option.id"><button type="button" :disabled="disabled" @click="choose(option)">{{ option.label }}</button></li>
        </ul>
      </div>
    </template>
  </div>
</template>

<style scoped>
.resource-picker { display: grid; gap: .4rem; min-width: 0; }
.resource-selection { display: flex; flex-wrap: wrap; align-items: center; gap: .7rem; padding: .65rem .8rem; border: 1px solid var(--color-border); border-radius: var(--radius-md); }
.resource-selection span { flex: 1; min-width: 10rem; }
.resource-picker button { color: var(--color-accent); background: none; border: 0; text-align: left; cursor: pointer; }
.resource-results { max-height: 13rem; overflow: auto; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface-raised); }
.resource-results p { margin: .55rem .75rem; font-size: .85rem; color: var(--color-text-muted); }
.resource-results ul { padding: 0; margin: 0; list-style: none; }
.resource-results button { width: 100%; padding: .55rem .75rem; }
.resource-results button:hover, .resource-results button:focus-visible { background: var(--color-surface-hover); }
.tournament { justify-self: start; }
</style>
