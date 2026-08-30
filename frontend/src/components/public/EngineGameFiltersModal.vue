<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import OptionPicker from '@/components/ui/OptionPicker.vue'
import type {
  EngineGameFilterOptions,
  EngineGameFilters,
  EngineGameResultFilter,
  EngineGameSideFilter,
  Identifier,
} from './types'

const props = defineProps<{
  open: boolean
  filters: EngineGameFilters
  options: EngineGameFilterOptions
  engines: Record<string, string>
  engineOptions: Array<{
    id: number
    engine_id: number
    name: string
    author?: string
    version: string
    source_kind?: string
    active?: boolean
  }>
  engineId: Identifier
  record: { wins: number; draws: number; losses: number; games: number }
}>()

const emit = defineEmits<{
  close: []
  apply: [filters: EngineGameFilters]
}>()

const dialog = ref<HTMLElement | null>(null)
const draft = ref<EngineGameFilters>(emptyFilters())
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

const ratingListOptions = computed(() => [
  { value: '', label: 'Any rating list' },
  ...props.options.rating_lists.map((option) => ({ value: option.value, label: option.label })),
])

const opponentOptions = computed(() => {
  const opponentIds = draft.value.ratingListId
    ? props.options.opponent_ids_by_rating_list[draft.value.ratingListId] ?? []
    : props.options.opponent_ids
  const allowed = new Set(opponentIds.map(String))
  const grouped = new Map<number, {
    value: string
    label: string
    description: string
    children: Array<{ value: string; label: string; description: string }>
  }>()
  for (const engine of props.engineOptions) {
    if (!allowed.has(String(engine.id)) || String(engine.id) === String(props.engineId)) continue
    const child = {
      value: String(engine.id),
      label: engine.version,
      description: `${engine.source_kind === 'commit' ? 'Commit' : 'Release'}${engine.active === false ? ' · unavailable' : ''}`,
    }
    const existing = grouped.get(engine.engine_id)
    if (existing) existing.children.push(child)
    else grouped.set(engine.engine_id, {
      value: `family-${engine.engine_id}`,
      label: engine.name,
      description: engine.author || 'Unknown author',
      children: [child],
    })
  }
  return [...grouped.values()]
    .map((group) => ({
      ...group,
      children: group.children.sort((left, right) => right.label.localeCompare(left.label, undefined, { numeric: true, sensitivity: 'base' })),
    }))
    .sort((left, right) => left.label.localeCompare(right.label))
})

const draftCount = computed(() => [
  draft.value.result,
  draft.value.ratingListId,
  draft.value.opponentId,
  draft.value.side,
].filter(Boolean).length)

watch(() => props.open, async (open, wasOpen) => {
  if (open) {
    draft.value = { ...props.filters }
    previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', onKeydown)
    await nextTick()
    dialog.value?.querySelector<HTMLElement>('[data-close-button]')?.focus()
  } else if (wasOpen) {
    restorePage()
  }
})

watch(() => draft.value.ratingListId, () => {
  if (!draft.value.opponentId) return
  const allowed = draft.value.ratingListId
    ? props.options.opponent_ids_by_rating_list[draft.value.ratingListId] ?? []
    : props.options.opponent_ids
  if (!allowed.some((engineId) => String(engineId) === draft.value.opponentId)) {
    draft.value = { ...draft.value, opponentId: '' }
  }
})

onBeforeUnmount(restorePage)

function emptyFilters(): EngineGameFilters {
  return { result: '', ratingListId: '', opponentId: '', side: '' }
}

function resultCount(result: EngineGameResultFilter): number {
  if (result === 'win') return props.record.wins
  if (result === 'draw') return props.record.draws
  if (result === 'loss') return props.record.losses
  return props.record.games
}

function focusableElements(): HTMLElement[] {
  if (!dialog.value) return []
  return Array.from(dialog.value.querySelectorAll<HTMLElement>(
    'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
  ))
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

function reset(): void {
  draft.value = emptyFilters()
}

function submit(): void {
  emit('apply', { ...draft.value })
}
</script>

<template>
  <Teleport to="body">
    <Transition name="filter-modal">
      <div v-if="open" class="filter-backdrop" @mousedown.self="emit('close')">
        <section
          ref="dialog"
          class="filter-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="engine-filter-title"
          aria-describedby="engine-filter-description"
        >
          <header class="filter-modal__header">
            <span class="filter-modal__icon"><AppIcon name="filter" :size="21" /></span>
            <div>
              <p>Game history</p>
              <h2 id="engine-filter-title">Filter games</h2>
              <span id="engine-filter-description">Combine criteria to find the games you need.</span>
            </div>
            <button data-close-button class="filter-modal__close" type="button" aria-label="Close filters" @click="emit('close')">
              <AppIcon name="close" :size="19" />
            </button>
          </header>

          <form class="filter-modal__form" @submit.prevent="submit">
            <div class="filter-modal__body">
              <fieldset class="filter-section filter-section--wide">
                <legend>Result</legend>
                <p>Outcome from {{ engines[String(engineId)] || 'this engine' }}’s perspective.</p>
                <div class="outcome-grid">
                  <button
                    v-for="outcome in outcomes"
                    :key="outcome.value"
                    type="button"
                    class="outcome-option"
                    :class="{ 'outcome-option--selected': draft.result === outcome.value }"
                    :aria-pressed="draft.result === outcome.value"
                    @click="draft.result = outcome.value"
                  >
                    <span class="outcome-option__mark" :data-result="outcome.value || 'all'">{{ outcome.mark }}</span>
                    <span><strong>{{ outcome.label }}</strong><small>{{ resultCount(outcome.value) }} game{{ resultCount(outcome.value) === 1 ? '' : 's' }}</small></span>
                    <AppIcon v-if="draft.result === outcome.value" name="check" :size="15" />
                  </button>
                </div>
              </fieldset>

              <fieldset class="filter-section">
                <legend>Rating list</legend>
                <p>Only include games committed to a specific rating list.</p>
                <OptionPicker v-model="draft.ratingListId" :options="ratingListOptions" label="Rating list" icon="trophy" />
              </fieldset>

              <fieldset class="filter-section">
                <legend>Opponent</legend>
                <p>Choose a family, then select the version.</p>
                <OptionPicker
                  v-model="draft.opponentId"
                  :options="[{ value: '', label: 'Any opponent' }, ...opponentOptions]"
                  label="Opponent"
                  icon="engine"
                  searchable
                  search-placeholder="Search engines or versions…"
                />
              </fieldset>

              <fieldset class="filter-section filter-section--wide filter-section--side">
                <legend>Playing side</legend>
                <div class="side-layout">
                  <p>Only include games played as White or Black.</p>
                  <div class="side-options" role="group" aria-label="Playing side">
                    <button
                      v-for="side in sides"
                      :key="side.value"
                      type="button"
                      :class="{ selected: draft.side === side.value }"
                      :aria-pressed="draft.side === side.value"
                      @click="draft.side = side.value"
                    >
                      {{ side.label }}
                    </button>
                  </div>
                </div>
              </fieldset>
            </div>

            <footer class="filter-modal__footer">
              <button class="clear-filters" type="button" :disabled="draftCount === 0" @click="reset">
                Clear all
              </button>
              <span v-if="draftCount">{{ draftCount }} filter{{ draftCount === 1 ? '' : 's' }} selected</span>
              <span v-else>Showing the complete record</span>
              <div>
                <BaseButton type="button" variant="ghost" @click="emit('close')">Cancel</BaseButton>
                <BaseButton type="submit" variant="primary">Show games</BaseButton>
              </div>
            </footer>
          </form>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.filter-backdrop {
  position: fixed;
  z-index: 1150;
  inset: 0;
  display: grid;
  place-items: center;
  overflow-y: auto;
  background: var(--color-overlay);
  padding: clamp(0.75rem, 3vw, 2rem);
}

.filter-modal {
  width: min(100%, 43rem);
  max-height: min(92vh, 52rem);
  overflow: hidden;
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-xl);
  background: var(--color-surface-raised);
  box-shadow: var(--shadow-md);
}

.filter-modal__header {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: start;
  gap: 0.85rem;
  padding: 1.3rem 1.4rem 1.15rem;
  border-block-end: 1px solid var(--color-border);
}

.filter-modal__icon {
  width: 2.45rem;
  height: 2.45rem;
  display: grid;
  place-items: center;
  border-radius: 0.75rem;
  background: var(--color-accent-soft);
  color: var(--color-accent);
}

.filter-modal__header p {
  color: var(--color-accent);
  font-size: 0.67rem;
  font-weight: 760;
  letter-spacing: 0.07em;
  text-transform: uppercase;
}

.filter-modal__header h2 {
  margin-block-start: 0.05rem;
  font-size: 1.25rem;
  letter-spacing: -0.02em;
}

.filter-modal__header div > span {
  display: block;
  margin-block-start: 0.15rem;
  color: var(--color-text-muted);
  font-size: 0.8rem;
}

.filter-modal__close {
  width: 2.2rem;
  height: 2.2rem;
  display: grid;
  place-items: center;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
}

.filter-modal__close:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.filter-modal__form {
  display: flex;
  min-height: 0;
  max-height: calc(min(92vh, 52rem) - 6.2rem);
  flex-direction: column;
}

.filter-modal__body {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.8rem;
  overflow-y: auto;
  padding: 1.15rem 1.4rem 1.3rem;
  background: color-mix(in srgb, var(--color-surface-sunken) 42%, var(--color-surface-raised));
}

.filter-section {
  min-width: 0;
  margin: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface-raised);
  padding: 1rem;
}

.filter-section--wide {
  grid-column: 1 / -1;
}

.filter-section legend {
  padding: 0;
  color: var(--color-text);
  font-size: 0.84rem;
  font-weight: 750;
}

.filter-section > p,
.side-layout > p {
  margin-block: 0.1rem 0.75rem;
  color: var(--color-text-muted);
  font-size: 0.72rem;
}

.outcome-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0.5rem;
}

.outcome-option {
  position: relative;
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 0.55rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
  color: var(--color-text-secondary);
  cursor: pointer;
  padding: 0.65rem;
  text-align: start;
}

.outcome-option:hover {
  border-color: var(--color-border-strong);
  background: var(--color-surface-hover);
}

.outcome-option--selected {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
  color: var(--color-text);
  box-shadow: inset 0 0 0 1px var(--color-accent);
}

.outcome-option > span:nth-child(2) {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.outcome-option strong {
  overflow: hidden;
  font-size: 0.74rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.outcome-option small {
  color: var(--color-text-muted);
  font-size: 0.62rem;
  font-variant-numeric: tabular-nums;
}

.outcome-option > .app-icon {
  position: absolute;
  inset-block-start: 0.35rem;
  inset-inline-end: 0.35rem;
  color: var(--color-accent);
}

.outcome-option__mark {
  width: 1.65rem;
  height: 1.65rem;
  display: grid !important;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 50%;
  background: var(--color-surface-sunken);
  color: var(--color-text-secondary);
  font-size: 0.66rem;
  font-weight: 800;
}

.outcome-option__mark[data-result='win'] { background: var(--color-success-soft); color: var(--color-success); }
.outcome-option__mark[data-result='draw'] { background: var(--color-warning-soft); color: var(--color-warning); }
.outcome-option__mark[data-result='loss'] { background: var(--color-danger-soft); color: var(--color-danger); }

.filter-section--side {
  display: block;
}

.side-layout {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.side-layout > p {
  margin-block-end: 0;
}

.side-options {
  display: flex;
  gap: 0.2rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-sunken);
  padding: 0.2rem;
}

.side-options button {
  min-width: 4.2rem;
  border-radius: 0.45rem;
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 0.45rem 0.65rem;
  font-size: 0.72rem;
  font-weight: 700;
}

.side-options button:hover { color: var(--color-text); }
.side-options button.selected { background: var(--color-surface-raised); color: var(--color-text); box-shadow: var(--shadow-xs); }

.filter-modal__footer {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.8rem;
  padding: 0.9rem 1.4rem;
  border-block-start: 1px solid var(--color-border);
  background: var(--color-surface-raised);
}

.filter-modal__footer > span {
  color: var(--color-text-muted);
  font-size: 0.7rem;
}

.filter-modal__footer > div {
  display: flex;
  gap: 0.45rem;
}

.clear-filters {
  background: transparent;
  color: var(--color-accent);
  cursor: pointer;
  font-size: 0.75rem;
  font-weight: 700;
  padding: 0.4rem 0;
}

.clear-filters:disabled {
  color: var(--color-text-faint);
  cursor: not-allowed;
  opacity: 0.6;
}

.filter-modal-enter-active,
.filter-modal-leave-active { transition: opacity var(--transition-base); }
.filter-modal-enter-active .filter-modal,
.filter-modal-leave-active .filter-modal { transition: transform var(--transition-base); }
.filter-modal-enter-from,
.filter-modal-leave-to { opacity: 0; }
.filter-modal-enter-from .filter-modal,
.filter-modal-leave-to .filter-modal { transform: translateY(0.65rem) scale(0.985); }

@media (max-width: 38rem) {
  .filter-backdrop { align-items: end; padding: 0; }
  .filter-modal { width: 100%; max-height: 94vh; border-block-end: 0; border-radius: var(--radius-xl) var(--radius-xl) 0 0; }
  .filter-modal__header { padding: 1rem; }
  .filter-modal__form { max-height: calc(94vh - 5.6rem); }
  .filter-modal__body { grid-template-columns: 1fr; padding: 0.8rem; }
  .filter-section--wide { grid-column: auto; }
  .outcome-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .side-layout { align-items: stretch; flex-direction: column; }
  .side-options { display: grid; grid-template-columns: repeat(3, 1fr); }
  .side-options button { min-width: 0; }
  .filter-modal__footer { grid-template-columns: auto 1fr; padding: 0.75rem 0.8rem; }
  .filter-modal__footer > span { text-align: end; }
  .filter-modal__footer > div { grid-column: 1 / -1; }
  .filter-modal__footer > div :deep(.base-button) { flex: 1; }
}
</style>
