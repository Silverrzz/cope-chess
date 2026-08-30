<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import OptionPicker from '@/components/ui/OptionPicker.vue'
import type { EngineGameResultFilter, EngineGameSideFilter, Identifier } from './types'

interface EngineOption {
  id: Identifier
  name: string
  version: string
  games: number
}

interface Filters {
  engineId: string
  result: EngineGameResultFilter
  opponentId: string
  side: EngineGameSideFilter
}

const props = defineProps<{
  open: boolean
  ratingList: { id: Identifier; name: string }
  engines: EngineOption[]
  opponentIdsByEngine: Record<string, number[]>
}>()

const emit = defineEmits<{
  close: []
}>()

const dialog = ref<HTMLElement | null>(null)
const draft = ref<Filters>(emptyFilters())
let previousFocus: HTMLElement | null = null

const outcomes: Array<{ value: EngineGameResultFilter; label: string; mark: string }> = [
  { value: '', label: 'Any result', mark: 'A' },
  { value: 'win', label: 'Wins', mark: 'W' },
  { value: 'draw', label: 'Draws', mark: 'D' },
  { value: 'loss', label: 'Losses', mark: 'L' },
]

const sides: Array<{ value: EngineGameSideFilter; label: string }> = [
  { value: '', label: 'Either' },
  { value: 'white', label: 'White' },
  { value: 'black', label: 'Black' },
]

const selectedEngine = computed(() => props.engines.find((engine) => String(engine.id) === draft.value.engineId) ?? null)
const engineOptions = computed(() => groupedEngineOptions(props.engines, 'All engines in this list'))
const opponentOptions = computed(() => {
  const allowed = new Set((props.opponentIdsByEngine[draft.value.engineId] ?? []).map(String))
  return groupedEngineOptions(
    props.engines.filter((engine) => allowed.has(String(engine.id))),
    'Any opponent',
  )
})
const draftCount = computed(() => [draft.value.engineId, draft.value.result, draft.value.opponentId, draft.value.side].filter(Boolean).length)
const downloadUrl = computed(() => {
  const parameters = new URLSearchParams({ rating_list_id: String(props.ratingList.id) })
  if (draft.value.engineId) parameters.set('engine_id', draft.value.engineId)
  if (draft.value.result) parameters.set('result', draft.value.result)
  if (draft.value.opponentId) parameters.set('opponent_id', draft.value.opponentId)
  if (draft.value.side) parameters.set('side', draft.value.side)
  return `/api/pgn?${parameters.toString()}`
})

watch(() => props.open, async (open, wasOpen) => {
  if (open) {
    draft.value = emptyFilters()
    previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', onKeydown)
    await nextTick()
    dialog.value?.querySelector<HTMLElement>('[data-close-button]')?.focus()
  } else if (wasOpen) {
    restorePage()
  }
})

watch(() => draft.value.engineId, (engineId, previousEngineId) => {
  if (engineId === previousEngineId) return
  draft.value = { ...draft.value, result: '', opponentId: '', side: '' }
})

onBeforeUnmount(restorePage)

function emptyFilters(): Filters {
  return { engineId: '', result: '', opponentId: '', side: '' }
}

function groupedEngineOptions(engines: EngineOption[], allLabel: string) {
  const grouped = new Map<string, {
    value: string
    label: string
    description: string
    children: Array<{ value: string; label: string; description: string }>
  }>()
  for (const engine of engines) {
    const child = {
      value: String(engine.id),
      label: engine.version || `Version ${engine.id}`,
      description: `${engine.games.toLocaleString()} rated game${engine.games === 1 ? '' : 's'}`,
    }
    const existing = grouped.get(engine.name)
    if (existing) existing.children.push(child)
    else grouped.set(engine.name, {
      value: `family-${engine.name}`,
      label: engine.name,
      description: child.description,
      children: [child],
    })
  }
  return [
    { value: '', label: allLabel },
    ...[...grouped.values()]
      .map((group) => ({
        ...group,
        children: group.children.sort((left, right) => right.label.localeCompare(left.label, undefined, { numeric: true, sensitivity: 'base' })),
      }))
      .sort((left, right) => left.label.localeCompare(right.label)),
  ]
}

function focusableElements(): HTMLElement[] {
  if (!dialog.value) return []
  return Array.from(dialog.value.querySelectorAll<HTMLElement>('button:not([disabled]), [href]:not([aria-disabled="true"]), [tabindex]:not([tabindex="-1"])'))
}

function onKeydown(event: KeyboardEvent): void {
  if (!props.open) return
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
    return
  }
  if (event.key !== 'Tab') return
  const elements = focusableElements()
  const first = elements[0]
  const last = elements[elements.length - 1]
  if (!first || !last) return
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

function restorePage(): void {
  document.body.style.overflow = ''
  document.removeEventListener('keydown', onKeydown)
  previousFocus?.focus()
  previousFocus = null
}
</script>

<template>
  <Teleport to="body">
    <Transition name="pgn-modal">
      <div v-if="open" class="pgn-backdrop" @mousedown.self="emit('close')">
        <section ref="dialog" class="pgn-modal" role="dialog" aria-modal="true" aria-labelledby="rating-pgn-title" aria-describedby="rating-pgn-description">
          <header class="pgn-modal__header">
            <span class="pgn-modal__icon"><AppIcon name="download" :size="21" /></span>
            <div>
              <p>{{ ratingList.name }}</p>
              <h2 id="rating-pgn-title">Download rating-list PGNs</h2>
              <span id="rating-pgn-description">Export every committed game, or narrow the file by engine and result.</span>
            </div>
            <button data-close-button class="pgn-modal__close" type="button" aria-label="Close PGN download" @click="emit('close')"><AppIcon name="close" :size="19" /></button>
          </header>

          <div class="pgn-modal__body">
            <fieldset class="filter-section filter-section--wide">
              <legend>Engine</legend>
              <p>Selecting an engine unlocks perspective-aware result, opponent, and side filters.</p>
              <OptionPicker v-model="draft.engineId" :options="engineOptions" label="Engine" icon="engine" searchable search-placeholder="Search engines or versions…" />
            </fieldset>

            <fieldset class="filter-section filter-section--wide" :disabled="!selectedEngine">
              <legend>Result</legend>
              <p>{{ selectedEngine ? `Outcome from ${selectedEngine.name} ${selectedEngine.version}’s perspective.` : 'Choose an engine to filter by its result.' }}</p>
              <div class="outcome-grid">
                <button v-for="outcome in outcomes" :key="outcome.value" type="button" class="outcome-option" :class="{ 'outcome-option--selected': draft.result === outcome.value }" :aria-pressed="draft.result === outcome.value" @click="draft.result = outcome.value">
                  <span class="outcome-option__mark" :data-result="outcome.value || 'all'">{{ outcome.mark }}</span>
                  <strong>{{ outcome.label }}</strong>
                  <AppIcon v-if="draft.result === outcome.value" name="check" :size="15" />
                </button>
              </div>
            </fieldset>

            <fieldset class="filter-section" :disabled="!selectedEngine">
              <legend>Opponent</legend>
              <p>Only include games against a particular version.</p>
              <OptionPicker v-model="draft.opponentId" :options="opponentOptions" label="Opponent" icon="engine" searchable search-placeholder="Search opponents…" :disabled="!selectedEngine" />
            </fieldset>

            <fieldset class="filter-section" :disabled="!selectedEngine">
              <legend>Playing side</legend>
              <p>Only include games played as White or Black.</p>
              <div class="side-options" role="group" aria-label="Playing side">
                <button v-for="side in sides" :key="side.value" type="button" :class="{ selected: draft.side === side.value }" :aria-pressed="draft.side === side.value" @click="draft.side = side.value">{{ side.label }}</button>
              </div>
            </fieldset>
          </div>

          <footer class="pgn-modal__footer">
            <button class="clear-filters" type="button" :disabled="draftCount === 0" @click="draft = emptyFilters()">Clear all</button>
            <span v-if="draftCount">{{ draftCount }} filter{{ draftCount === 1 ? '' : 's' }} selected</span>
            <span v-else>All committed games will be included</span>
            <div>
              <BaseButton variant="ghost" @click="emit('close')">Cancel</BaseButton>
              <BaseButton :href="downloadUrl" variant="primary" download @click="emit('close')"><template #icon><AppIcon name="download" :size="16" /></template>Download PGN</BaseButton>
            </div>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.pgn-backdrop { position: fixed; z-index: 1150; inset: 0; display: grid; place-items: center; overflow-y: auto; background: var(--color-overlay); padding: clamp(.75rem, 3vw, 2rem); }
.pgn-modal { width: min(100%, 43rem); max-height: min(92vh, 52rem); overflow: hidden; border: 1px solid var(--color-border-strong); border-radius: var(--radius-xl); background: var(--color-surface-raised); box-shadow: var(--shadow-md); }
.pgn-modal__header { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: start; gap: .85rem; padding: 1.3rem 1.4rem 1.15rem; border-block-end: 1px solid var(--color-border); }
.pgn-modal__icon { width: 2.45rem; height: 2.45rem; display: grid; place-items: center; border-radius: .75rem; background: var(--color-accent-soft); color: var(--color-accent); }
.pgn-modal__header p, .pgn-modal__header h2 { margin: 0; }
.pgn-modal__header p { color: var(--color-accent); font-size: .67rem; font-weight: 760; letter-spacing: .07em; text-transform: uppercase; }
.pgn-modal__header h2 { margin-block-start: .05rem; font-size: 1.25rem; letter-spacing: -.02em; }
.pgn-modal__header div > span { display: block; margin-block-start: .15rem; color: var(--color-text-muted); font-size: .8rem; }
.pgn-modal__close { width: 2.2rem; height: 2.2rem; display: grid; place-items: center; border-radius: var(--radius-md); background: transparent; color: var(--color-text-muted); cursor: pointer; }
.pgn-modal__close:hover { background: var(--color-surface-hover); color: var(--color-text); }
.pgn-modal__body { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .8rem; max-height: calc(min(92vh, 52rem) - 12rem); overflow-y: auto; padding: 1.15rem 1.4rem 1.3rem; background: color-mix(in srgb, var(--color-surface-sunken) 42%, var(--color-surface-raised)); }
.filter-section { min-width: 0; margin: 0; border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface-raised); padding: 1rem; }
.filter-section--wide { grid-column: 1 / -1; }
.filter-section:disabled { opacity: .56; }
.filter-section legend { padding: 0; color: var(--color-text); font-size: .84rem; font-weight: 750; }
.filter-section > p { margin: .1rem 0 .75rem; color: var(--color-text-muted); font-size: .72rem; }
.outcome-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .5rem; }
.outcome-option { position: relative; display: flex; min-width: 0; align-items: center; gap: .5rem; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface-raised); color: var(--color-text-secondary); cursor: pointer; padding: .65rem; }
.outcome-option:hover { border-color: var(--color-border-strong); background: var(--color-surface-hover); }
.outcome-option--selected { border-color: var(--color-accent); background: var(--color-accent-soft); color: var(--color-text); box-shadow: inset 0 0 0 1px var(--color-accent); }
.outcome-option strong { overflow: hidden; font-size: .74rem; text-overflow: ellipsis; white-space: nowrap; }
.outcome-option > .app-icon { position: absolute; inset-block-start: .35rem; inset-inline-end: .35rem; color: var(--color-accent); }
.outcome-option__mark { width: 1.65rem; height: 1.65rem; display: grid; flex: 0 0 auto; place-items: center; border-radius: 50%; background: var(--color-surface-sunken); color: var(--color-text-secondary); font-size: .66rem; font-weight: 800; }
.outcome-option__mark[data-result='win'] { background: var(--color-success-soft); color: var(--color-success); }
.outcome-option__mark[data-result='draw'] { background: var(--color-warning-soft); color: var(--color-warning); }
.outcome-option__mark[data-result='loss'] { background: var(--color-danger-soft); color: var(--color-danger); }
.side-options { display: grid; grid-template-columns: repeat(3, 1fr); gap: .2rem; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface-sunken); padding: .2rem; }
.side-options button { min-width: 0; border-radius: .45rem; background: transparent; color: var(--color-text-muted); cursor: pointer; padding: .55rem .65rem; font-size: .72rem; font-weight: 700; }
.side-options button:hover { color: var(--color-text); }
.side-options button.selected { background: var(--color-surface-raised); color: var(--color-text); box-shadow: var(--shadow-xs); }
.pgn-modal__footer { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: .8rem; padding: .9rem 1.4rem; border-block-start: 1px solid var(--color-border); background: var(--color-surface-raised); }
.pgn-modal__footer > span { color: var(--color-text-muted); font-size: .7rem; }
.pgn-modal__footer > div { display: flex; gap: .45rem; }
.clear-filters { background: transparent; color: var(--color-accent); cursor: pointer; font-size: .75rem; font-weight: 700; padding: .4rem 0; }
.clear-filters:disabled { color: var(--color-text-faint); cursor: not-allowed; opacity: .6; }
.pgn-modal-enter-active, .pgn-modal-leave-active { transition: opacity var(--transition-base); }
.pgn-modal-enter-active .pgn-modal, .pgn-modal-leave-active .pgn-modal { transition: transform var(--transition-base); }
.pgn-modal-enter-from, .pgn-modal-leave-to { opacity: 0; }
.pgn-modal-enter-from .pgn-modal, .pgn-modal-leave-to .pgn-modal { transform: translateY(.65rem) scale(.985); }
@media (max-width: 38rem) {
  .pgn-backdrop { align-items: end; padding: 0; }
  .pgn-modal { width: 100%; max-height: 94vh; border-block-end: 0; border-radius: var(--radius-xl) var(--radius-xl) 0 0; }
  .pgn-modal__header { padding: 1rem; }
  .pgn-modal__body { grid-template-columns: 1fr; max-height: calc(94vh - 11.4rem); padding: .8rem; }
  .filter-section--wide { grid-column: auto; }
  .outcome-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .pgn-modal__footer { grid-template-columns: auto 1fr; padding: .75rem .8rem; }
  .pgn-modal__footer > span { text-align: end; }
  .pgn-modal__footer > div { grid-column: 1 / -1; }
  .pgn-modal__footer > div :deep(.base-button) { flex: 1; }
}
</style>
