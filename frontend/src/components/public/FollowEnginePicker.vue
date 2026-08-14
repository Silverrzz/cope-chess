<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, useId, watch } from 'vue'

import AppIcon from '@/components/ui/AppIcon.vue'

interface EngineOption {
  id: string
  name: string
}

const props = defineProps<{
  modelValue: string
  engines: EngineOption[]
  state: string
  stateLabel: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  resume: []
}>()

const pickerId = useId()
const root = ref<HTMLElement | null>(null)
const panel = ref<HTMLElement | null>(null)
const searchInput = ref<HTMLInputElement | null>(null)
const open = ref(false)
const query = ref('')
const compact = ref(false)
const position = reactive({ top: '0px', left: '0px', width: '20rem', maxHeight: '24rem' })
let compactQuery: MediaQueryList | null = null
let previousBodyOverflow: string | null = null

const selectedEngine = computed(() => props.engines.find((engine) => engine.id === props.modelValue) ?? null)
const filteredEngines = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase()
  return needle
    ? props.engines.filter((engine) => engine.name.toLocaleLowerCase().includes(needle))
    : props.engines
})
const triggerLabel = computed(() => selectedEngine.value
  ? `Following ${selectedEngine.value.name}. Change followed engine`
  : 'Choose an engine to follow')

async function show(): Promise<void> {
  open.value = true
  query.value = ''
  await nextTick()
  updatePosition()
  await nextTick()
  if (!compact.value) searchInput.value?.focus()
  else selectedOption()?.focus()
}

function close(restoreFocus = false): void {
  if (!open.value) return
  open.value = false
  query.value = ''
  if (restoreFocus) root.value?.querySelector<HTMLElement>('.follow-picker__trigger')?.focus()
}

function choose(value: string): void {
  emit('update:modelValue', value)
  close(true)
}

function resume(): void {
  emit('resume')
}

function updateCompact(event?: MediaQueryListEvent): void {
  compact.value = event?.matches ?? compactQuery?.matches ?? false
  if (open.value) void nextTick(updatePosition)
}

function updatePosition(): void {
  if (!root.value || !open.value || compact.value) return
  const rect = root.value.getBoundingClientRect()
  const viewport = window.visualViewport
  const viewportLeft = viewport?.offsetLeft ?? 0
  const viewportTop = viewport?.offsetTop ?? 0
  const viewportWidth = viewport?.width ?? window.innerWidth
  const viewportHeight = viewport?.height ?? window.innerHeight
  const gutter = 8
  const width = Math.min(Math.max(rect.width, 320), viewportWidth - gutter * 2)
  const availableBelow = viewportTop + viewportHeight - rect.bottom - gutter
  const availableAbove = rect.top - viewportTop - gutter
  const desiredHeight = Math.min(panel.value?.scrollHeight || 384, 384)
  const placeBelow = availableBelow >= Math.min(desiredHeight, 220) || availableBelow >= availableAbove
  const availableHeight = Math.max(160, placeBelow ? availableBelow : availableAbove)
  const top = placeBelow
    ? rect.bottom + 6
    : Math.max(viewportTop + gutter, rect.top - Math.min(desiredHeight, availableHeight) - 6)
  const left = Math.min(
    Math.max(viewportLeft + gutter, rect.right - width),
    viewportLeft + viewportWidth - width - gutter,
  )
  position.top = `${Math.round(top)}px`
  position.left = `${Math.round(left)}px`
  position.width = `${Math.round(width)}px`
  position.maxHeight = `${Math.floor(availableHeight)}px`
}

function selectedOption(): HTMLButtonElement | null {
  return panel.value?.querySelector<HTMLButtonElement>('[aria-selected="true"]') ?? null
}

function moveOptionFocus(event: KeyboardEvent): void {
  if (!panel.value || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
  const options = Array.from(panel.value.querySelectorAll<HTMLButtonElement>('.follow-picker__option'))
  if (!options.length) return
  const current = options.indexOf(document.activeElement as HTMLButtonElement)
  let next = current
  if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = options.length - 1
  else if (event.key === 'ArrowDown') next = current < 0 ? 0 : (current + 1) % options.length
  else next = current < 0 ? options.length - 1 : (current - 1 + options.length) % options.length
  event.preventDefault()
  options[next]?.focus()
}

function handleDocumentPointer(event: PointerEvent): void {
  const target = event.target as Node
  if (open.value && !root.value?.contains(target) && !panel.value?.contains(target)) close(compact.value)
}

function handleDocumentKeydown(event: KeyboardEvent): void {
  if (!open.value) return
  if (event.key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    close(true)
    return
  }
  if (event.key !== 'Tab' || !compact.value || !panel.value) return
  const focusable = Array.from(panel.value.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled])'))
  const first = focusable[0]
  const last = focusable.at(-1)
  if (!first || !last) return
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

function handleFocus(event: FocusEvent): void {
  if (!open.value) return
  const target = event.target as Node
  if (panel.value?.contains(target)) return
  if (compact.value) {
    panel.value?.querySelector<HTMLElement>('button, input')?.focus()
  } else if (!root.value?.contains(target)) close()
}

function syncBodyScrollLock(): void {
  const shouldLock = open.value && compact.value
  if (shouldLock && previousBodyOverflow === null) {
    previousBodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  } else if (!shouldLock && previousBodyOverflow !== null) {
    document.body.style.overflow = previousBodyOverflow
    previousBodyOverflow = null
  }
}

watch(() => props.engines, () => {
  if (open.value) void nextTick(updatePosition)
})
watch([open, compact], syncBodyScrollLock)

onMounted(() => {
  compactQuery = window.matchMedia('(max-width: 40rem)')
  updateCompact()
  compactQuery.addEventListener('change', updateCompact)
  document.addEventListener('pointerdown', handleDocumentPointer)
  document.addEventListener('keydown', handleDocumentKeydown)
  document.addEventListener('focusin', handleFocus)
  window.addEventListener('resize', updatePosition)
  window.addEventListener('scroll', updatePosition, true)
  window.visualViewport?.addEventListener('resize', updatePosition)
  window.visualViewport?.addEventListener('scroll', updatePosition)
})

onBeforeUnmount(() => {
  if (previousBodyOverflow !== null) {
    document.body.style.overflow = previousBodyOverflow
    previousBodyOverflow = null
  }
  compactQuery?.removeEventListener('change', updateCompact)
  document.removeEventListener('pointerdown', handleDocumentPointer)
  document.removeEventListener('keydown', handleDocumentKeydown)
  document.removeEventListener('focusin', handleFocus)
  window.removeEventListener('resize', updatePosition)
  window.removeEventListener('scroll', updatePosition, true)
  window.visualViewport?.removeEventListener('resize', updatePosition)
  window.visualViewport?.removeEventListener('scroll', updatePosition)
})
</script>

<template>
  <div ref="root" class="follow-engine-picker">
    <div class="follow-picker__heading">
      <span>Follow engine</span>
      <span v-if="stateLabel" class="follow-picker__status">
        <span class="follow-picker__state" :data-state="state">{{ stateLabel }}</span>
        <button v-if="state === 'paused'" type="button" @click="resume">Resume</button>
      </span>
    </div>
    <button
      class="follow-picker__trigger"
      :class="{ 'follow-picker__trigger--open': open }"
      type="button"
      :aria-label="triggerLabel"
      aria-haspopup="listbox"
      :aria-expanded="open"
      :aria-controls="`${pickerId}-panel`"
      @click="open ? close() : show()"
      @keydown.down.prevent="show"
      @keydown.up.prevent="show"
    >
      <span class="follow-picker__trigger-icon"><AppIcon :name="selectedEngine ? 'engine' : 'eye-off'" :size="16" /></span>
      <span class="follow-picker__selection">
        <strong>{{ selectedEngine?.name || 'Don\'t follow' }}</strong>
        <small>{{ selectedEngine ? 'Switches to this engine’s active game' : 'Stay on the game you choose' }}</small>
      </span>
      <AppIcon class="follow-picker__chevron" name="chevron-down" :size="16" />
    </button>

    <Teleport to="body">
      <Transition name="follow-picker-backdrop">
        <button v-if="open && compact" class="follow-picker__backdrop" type="button" aria-label="Close engine picker" @click="close(true)" />
      </Transition>
      <Transition name="follow-picker-panel">
        <section
          v-if="open"
          :id="`${pickerId}-panel`"
          ref="panel"
          class="follow-picker__panel"
          :class="{ 'follow-picker__panel--compact': compact }"
          :style="compact ? undefined : position"
          role="dialog"
          :aria-modal="compact || undefined"
          aria-label="Choose an engine to follow"
          @keydown="moveOptionFocus"
        >
          <header class="follow-picker__panel-heading">
            <span>
              <strong>Follow an engine</strong>
              <small>Jump to its game automatically</small>
            </span>
            <button type="button" aria-label="Close engine picker" @click="close(true)"><AppIcon name="close" :size="18" /></button>
          </header>
          <label class="follow-picker__search">
            <AppIcon name="search" :size="16" />
            <input ref="searchInput" v-model="query" type="search" inputmode="search" autocomplete="off" placeholder="Search engines…" aria-label="Search engines">
          </label>
          <div class="follow-picker__list" role="listbox" aria-label="Tournament engines">
            <button
              class="follow-picker__option follow-picker__option--off"
              :class="{ 'follow-picker__option--selected': !modelValue }"
              type="button"
              role="option"
              :aria-selected="!modelValue"
              @click="choose('')"
            >
              <span class="follow-picker__option-icon"><AppIcon name="eye-off" :size="16" /></span>
              <span><strong>Don’t follow</strong><small>Keep manual game selection</small></span>
              <AppIcon v-if="!modelValue" name="check" :size="17" />
            </button>
            <div v-if="filteredEngines.length" class="follow-picker__options">
              <button
                v-for="engine in filteredEngines"
                :key="engine.id"
                class="follow-picker__option"
                :class="{ 'follow-picker__option--selected': engine.id === modelValue }"
                type="button"
                role="option"
                :aria-selected="engine.id === modelValue"
                @click="choose(engine.id)"
              >
                <span class="follow-picker__option-icon"><AppIcon name="engine" :size="16" /></span>
                <span><strong>{{ engine.name }}</strong><small>Follow active games</small></span>
                <AppIcon v-if="engine.id === modelValue" name="check" :size="17" />
              </button>
            </div>
            <p v-else class="follow-picker__empty">No engines match “{{ query }}”.</p>
          </div>
        </section>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.follow-engine-picker { color: var(--color-text-muted, #607080); display: grid; font-size: .64rem; font-weight: 700; gap: .25rem; min-width: 0; position: relative; }
.follow-picker__heading, .follow-picker__status { align-items: center; display: flex; min-width: 0; }
.follow-picker__heading { gap: .6rem; justify-content: space-between; }
.follow-picker__status { gap: .35rem; }
.follow-picker__state { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.follow-picker__state[data-state='live'] { color: var(--color-success, #218739); }
.follow-picker__state[data-state='fallback'], .follow-picker__state[data-state='waiting'] { color: var(--color-warning, #a15c00); }
.follow-picker__status button { background: none; border: 0; color: var(--color-accent, #2f78c4); cursor: pointer; font: inherit; font-weight: 750; padding: 0; text-decoration: underline; text-underline-offset: .12em; }
.follow-picker__trigger { align-items: center; background: var(--color-surface, #fff); border: 1px solid var(--color-border, #d5dbe1); border-radius: var(--radius-sm, .35rem); color: var(--color-text, #17202a); cursor: pointer; display: grid; gap: .55rem; grid-template-columns: auto minmax(0, 1fr) auto; min-height: 2.35rem; padding: .34rem .6rem; text-align: left; width: 100%; }
.follow-picker__trigger:hover, .follow-picker__trigger--open { border-color: var(--color-border-strong, #99a8bb); }
.follow-picker__trigger:focus-visible { border-color: var(--color-accent, #2f78c4); box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-accent, #2f78c4) 20%, transparent); outline: 0; }
.follow-picker__trigger-icon { align-items: center; background: color-mix(in srgb, var(--color-accent, #2f78c4) 8%, transparent); border-radius: 50%; color: var(--color-accent, #2f78c4); display: inline-flex; height: 1.7rem; justify-content: center; width: 1.7rem; }
.follow-picker__selection { display: grid; min-width: 0; }
.follow-picker__selection strong, .follow-picker__selection small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.follow-picker__selection strong { font-size: .76rem; font-weight: 700; }
.follow-picker__selection small { color: var(--color-text-muted, #607080); font-size: .58rem; font-weight: 600; margin-top: .02rem; }
.follow-picker__chevron { color: var(--color-text-muted, #607080); transition: transform var(--transition-fast, 120ms ease); }
.follow-picker__trigger--open .follow-picker__chevron { transform: rotate(180deg); }
.follow-picker__panel { background: var(--color-surface-raised, #fff); border: 1px solid var(--color-border-strong, #99a8bb); border-radius: var(--radius-md, .5rem); box-shadow: var(--shadow-md, 0 1rem 2.5rem rgb(0 0 0 / 18%)); display: flex; flex-direction: column; overflow: hidden; position: fixed; z-index: 1400; }
.follow-picker__panel-heading { align-items: center; border-block-end: 1px solid var(--color-border, #d5dbe1); display: flex; justify-content: space-between; padding: .65rem .7rem; }
.follow-picker__panel-heading > span { display: grid; gap: .08rem; }
.follow-picker__panel-heading strong { color: var(--color-text, #17202a); font-size: .78rem; }
.follow-picker__panel-heading small { color: var(--color-text-muted, #607080); font-size: .62rem; }
.follow-picker__panel-heading button { align-items: center; background: transparent; border: 0; border-radius: var(--radius-sm, .35rem); color: var(--color-text-muted, #607080); cursor: pointer; display: inline-flex; height: 2rem; justify-content: center; width: 2rem; }
.follow-picker__panel-heading button:hover, .follow-picker__panel-heading button:focus-visible { background: var(--color-surface-hover, #f2f4f7); color: var(--color-text, #17202a); outline: 0; }
.follow-picker__search { align-items: center; border-block-end: 1px solid var(--color-border, #d5dbe1); color: var(--color-text-muted, #607080); display: flex; gap: .5rem; padding: .6rem .7rem; }
.follow-picker__search:focus-within { color: var(--color-accent, #2f78c4); }
.follow-picker__search input { background: transparent; border: 0; color: var(--color-text, #17202a); font: inherit; font-size: .75rem; min-width: 0; outline: 0; width: 100%; }
.follow-picker__list { min-height: 0; overflow: auto; overscroll-behavior: contain; padding: .35rem; }
.follow-picker__options { border-block-start: 1px solid var(--color-border, #d5dbe1); margin-block-start: .35rem; padding-block-start: .35rem; }
.follow-picker__option { align-items: center; background: transparent; border: 0; border-radius: var(--radius-sm, .35rem); color: var(--color-text, #17202a); cursor: pointer; display: grid; gap: .55rem; grid-template-columns: auto minmax(0, 1fr) auto; min-height: 3rem; padding: .55rem .6rem; text-align: left; width: 100%; }
.follow-picker__option:hover, .follow-picker__option:focus-visible { background: var(--color-surface-hover, #f2f4f7); outline: 0; }
.follow-picker__option--selected { background: color-mix(in srgb, var(--color-accent, #2f78c4) 8%, transparent); color: var(--color-accent, #2f78c4); }
.follow-picker__option-icon { align-items: center; border: 1px solid var(--color-border, #d5dbe1); border-radius: 50%; color: var(--color-text-muted, #607080); display: inline-flex; height: 1.9rem; justify-content: center; width: 1.9rem; }
.follow-picker__option--selected .follow-picker__option-icon { background: var(--color-accent, #2f78c4); border-color: var(--color-accent, #2f78c4); color: #fff; }
.follow-picker__option > span:nth-child(2) { display: grid; min-width: 0; }
.follow-picker__option strong, .follow-picker__option small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.follow-picker__option strong { font-size: .75rem; }
.follow-picker__option small { color: var(--color-text-muted, #607080); font-size: .62rem; margin-top: .08rem; }
.follow-picker__empty { color: var(--color-text-muted, #607080); font-size: .72rem; margin: 0; padding: 1.6rem .6rem; text-align: center; }
.follow-picker__backdrop { background: rgb(7 16 28 / 48%); border: 0; inset: 0; padding: 0; position: fixed; z-index: 1399; }
.follow-picker-panel-enter-active, .follow-picker-panel-leave-active, .follow-picker-backdrop-enter-active, .follow-picker-backdrop-leave-active { transition: opacity var(--transition-fast, 120ms ease), transform var(--transition-fast, 120ms ease); }
.follow-picker-panel-enter-from, .follow-picker-panel-leave-to { opacity: 0; transform: translateY(-.25rem) scale(.985); }
.follow-picker-backdrop-enter-from, .follow-picker-backdrop-leave-to { opacity: 0; }
@media (max-width: 40rem) {
  .follow-picker__trigger { min-height: 3.25rem; padding: .48rem .65rem; }
  .follow-picker__selection strong { font-size: .82rem; }
  .follow-picker__selection small { font-size: .64rem; }
  .follow-picker__panel--compact { border: 0; border-radius: var(--radius-lg, .7rem) var(--radius-lg, .7rem) 0 0; bottom: 0; left: 0; max-height: min(82dvh, 40rem); padding-block-end: env(safe-area-inset-bottom); right: 0; width: 100%; }
  .follow-picker__panel-heading { padding: .8rem .9rem; }
  .follow-picker__panel-heading strong { font-size: .9rem; }
  .follow-picker__panel-heading small { font-size: .68rem; }
  .follow-picker__panel-heading button { height: 2.75rem; width: 2.75rem; }
  .follow-picker__search { margin: .75rem .8rem .35rem; border: 1px solid var(--color-border, #d5dbe1); border-radius: var(--radius-sm, .35rem); padding: .75rem; }
  .follow-picker__search:focus-within { border-color: var(--color-accent, #2f78c4); box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-accent, #2f78c4) 16%, transparent); }
  .follow-picker__search input { font-size: 1rem; }
  .follow-picker__list { padding: .35rem .55rem .7rem; }
  .follow-picker__option { min-height: 3.65rem; padding: .65rem; }
  .follow-picker__option strong { font-size: .82rem; }
  .follow-picker__option small { font-size: .68rem; }
  .follow-picker-panel-enter-from, .follow-picker-panel-leave-to { opacity: 1; transform: translateY(100%); }
}
@media (prefers-reduced-motion: reduce) {
  .follow-picker__chevron, .follow-picker-panel-enter-active, .follow-picker-panel-leave-active, .follow-picker-backdrop-enter-active, .follow-picker-backdrop-leave-active { transition: none; }
}
</style>
