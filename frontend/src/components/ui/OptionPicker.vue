<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, useId, watch } from 'vue'

import type { IconName } from '@/types/icons'
import AppIcon from './AppIcon.vue'

interface PickerOption {
  value: string | number
  label: string
  description?: string
  meta?: string
  disabled?: boolean
  children?: PickerOption[]
}

const props = withDefaults(defineProps<{
  modelValue: string | number
  options: PickerOption[]
  placeholder?: string
  label: string
  icon?: IconName
  searchable?: boolean
  searchPlaceholder?: string
  disabled?: boolean
}>(), {
  placeholder: 'Choose an option',
  searchable: false,
  searchPlaceholder: 'Search…',
  disabled: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: string | number]
  change: [value: string | number]
}>()

const pickerId = useId()
const root = ref<HTMLElement | null>(null)
const menu = ref<HTMLElement | null>(null)
const searchInput = ref<HTMLInputElement | null>(null)
const open = ref(false)
const query = ref('')
const activeGroupValue = ref<string | number | null>(null)
const position = reactive({ top: '0px', left: '0px', width: '18rem' })

function sameValue(left: string | number | null | undefined, right: string | number | null | undefined): boolean {
  return String(left ?? '') === String(right ?? '')
}

const selected = computed(() => {
  for (const option of props.options) {
    if (sameValue(option.value, props.modelValue) && !option.children?.length) return { option, parent: null as PickerOption | null }
    const child = option.children?.find((item) => sameValue(item.value, props.modelValue))
    if (child) return { option: child, parent: option }
  }
  return null
})

const filteredOptions = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase()
  if (!needle) return props.options
  return props.options.flatMap((option) => {
    const groupMatch = `${option.label} ${option.description ?? ''} ${option.meta ?? ''}`.toLocaleLowerCase().includes(needle)
    if (!option.children?.length) return groupMatch ? [option] : []
    const children = groupMatch
      ? option.children
      : option.children.filter((child) => `${child.label} ${child.description ?? ''} ${child.meta ?? ''}`.toLocaleLowerCase().includes(needle))
    return children.length ? [{ ...option, children }] : []
  })
})

const activeGroup = computed(() => filteredOptions.value.find((option) => sameValue(option.value, activeGroupValue.value) && option.children?.length) ?? null)
const selectedSupportingText = computed(() => selected.value?.parent?.label || selected.value?.option.meta || selected.value?.option.description || '')

async function show(): Promise<void> {
  if (props.disabled || !props.options.length) return
  open.value = true
  query.value = ''
  activeGroupValue.value = selected.value?.parent?.value ?? null
  await nextTick()
  updatePosition()
  await nextTick()
  if (props.searchable) searchInput.value?.focus()
  else menu.value?.querySelector<HTMLElement>('[data-picker-option]')?.focus()
}

function close(restoreFocus = false): void {
  if (!open.value) return
  open.value = false
  query.value = ''
  activeGroupValue.value = null
  if (restoreFocus) root.value?.querySelector<HTMLElement>('button')?.focus()
}

function toggle(): void {
  if (open.value) close()
  else void show()
}

function choose(option: PickerOption): void {
  if (option.disabled) return
  if (option.children?.length) {
    activeGroupValue.value = option.value
    void nextTick(updatePosition)
    return
  }
  emit('update:modelValue', option.value)
  emit('change', option.value)
  close(true)
}

function updatePosition(): void {
  if (!root.value || !open.value) return
  const rect = root.value.getBoundingClientRect()
  const compact = window.innerWidth <= 720
  const hasPanel = Boolean(activeGroup.value)
  const desiredWidth = compact ? Math.min(rect.width, window.innerWidth - 16) : hasPanel ? 530 : Math.max(rect.width, 288)
  const width = Math.min(desiredWidth, window.innerWidth - 16)
  const estimatedHeight = Math.min(384, menu.value?.offsetHeight || 340)
  const below = window.innerHeight - rect.bottom - 8
  const top = below >= estimatedHeight || below >= rect.top ? rect.bottom + 6 : Math.max(8, rect.top - estimatedHeight - 6)
  position.top = `${Math.round(top)}px`
  position.left = `${Math.round(Math.min(Math.max(8, rect.left), window.innerWidth - width - 8))}px`
  position.width = `${Math.round(width)}px`
}

function handleDocumentPointer(event: PointerEvent): void {
  const target = event.target as Node
  if (open.value && !root.value?.contains(target) && !menu.value?.contains(target)) close()
}

function handleKeydown(event: KeyboardEvent): void {
  if (!open.value) return
  if (event.key === 'Escape') {
    event.preventDefault()
    event.stopImmediatePropagation()
    close(true)
    return
  }
  if (event.key !== 'Tab' || !menu.value) return
  const elements = Array.from(menu.value.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'))
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

watch(filteredOptions, (available) => {
  if (activeGroupValue.value !== null && !available.some((option) => sameValue(option.value, activeGroupValue.value))) activeGroupValue.value = null
})
watch(activeGroup, () => void nextTick(updatePosition))

onMounted(() => {
  document.addEventListener('pointerdown', handleDocumentPointer)
  document.addEventListener('keydown', handleKeydown)
  window.addEventListener('resize', updatePosition)
  window.addEventListener('scroll', updatePosition, true)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleDocumentPointer)
  document.removeEventListener('keydown', handleKeydown)
  window.removeEventListener('resize', updatePosition)
  window.removeEventListener('scroll', updatePosition, true)
})
</script>

<template>
  <div ref="root" class="option-picker">
    <button
      class="option-picker__trigger"
      :class="{ 'option-picker__trigger--open': open, 'option-picker__trigger--icon': icon }"
      type="button"
      :disabled="disabled || !options.length"
      :aria-label="label"
      aria-haspopup="listbox"
      :aria-expanded="open"
      :aria-controls="`${pickerId}-menu`"
      @click="toggle"
      @keydown.down.prevent="show"
    >
      <AppIcon v-if="icon" :name="icon" :size="17" />
      <span v-if="selected" class="option-picker__selection">
        <strong>{{ selected.option.label }}</strong>
        <small v-if="selectedSupportingText">{{ selectedSupportingText }}</small>
      </span>
      <span v-else class="option-picker__placeholder">{{ placeholder }}</span>
      <AppIcon name="chevron-down" :size="16" />
    </button>

    <Teleport to="body">
      <Transition name="option-picker-menu">
        <div
          v-if="open"
          :id="`${pickerId}-menu`"
          ref="menu"
          data-option-picker-menu
          class="option-picker__menu"
          :class="{ 'option-picker__menu--split': activeGroup }"
          :style="position"
          role="dialog"
          :aria-label="label"
        >
          <div class="option-picker__primary">
            <label v-if="searchable" class="option-picker__search">
              <AppIcon name="search" :size="16" />
              <input ref="searchInput" v-model="query" type="search" autocomplete="off" :placeholder="searchPlaceholder" :aria-label="searchPlaceholder">
            </label>
            <div class="option-picker__list" role="listbox" :aria-label="label">
              <button
                v-for="option in filteredOptions"
                :key="option.value"
                data-picker-option
                class="option-picker__option"
                :class="{
                  'option-picker__option--active': sameValue(option.value, activeGroupValue),
                  'option-picker__option--selected': sameValue(option.value, modelValue),
                }"
                type="button"
                role="option"
                :disabled="option.disabled"
                :aria-selected="sameValue(option.value, activeGroupValue) || sameValue(option.value, modelValue)"
                @click="choose(option)"
              >
                <span>
                  <strong>{{ option.label }}</strong>
                  <small v-if="option.description || option.meta">{{ option.description || option.meta }}</small>
                </span>
                <span v-if="option.children?.length" class="option-picker__option-meta">
                  <small>{{ option.children.length }}</small>
                  <AppIcon name="chevron-right" :size="16" />
                </span>
                <AppIcon v-else-if="sameValue(option.value, modelValue)" name="check" :size="16" />
              </button>
              <p v-if="!filteredOptions.length" class="option-picker__empty">No matching options</p>
            </div>
          </div>

          <aside v-if="activeGroup" class="option-picker__secondary" :aria-label="`${activeGroup.label} options`">
            <header>
              <span>{{ activeGroup.label }}</span>
              <small>Choose a version</small>
            </header>
            <div class="option-picker__list" role="listbox" :aria-label="`${activeGroup.label} versions`">
              <button
                v-for="option in activeGroup.children"
                :key="option.value"
                data-picker-option
                class="option-picker__option"
                :class="{ 'option-picker__option--selected': sameValue(option.value, modelValue) }"
                type="button"
                role="option"
                :disabled="option.disabled"
                :aria-selected="sameValue(option.value, modelValue)"
                @click="choose(option)"
              >
                <span>
                  <strong>{{ option.label }}</strong>
                  <small v-if="option.description || option.meta">{{ option.description || option.meta }}</small>
                </span>
                <AppIcon v-if="sameValue(option.value, modelValue)" name="check" :size="16" />
              </button>
            </div>
          </aside>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.option-picker { min-width: 0; position: relative; }
.option-picker__trigger { align-items: center; background: var(--color-surface-raised); border: 1px solid var(--color-border-strong); border-radius: var(--radius-md); color: var(--color-text); cursor: pointer; display: grid; gap: .55rem; grid-template-columns: minmax(0, 1fr) auto; min-height: 2.7rem; padding: .5rem .7rem; text-align: left; width: 100%; }
.option-picker__trigger--icon { grid-template-columns: auto minmax(0, 1fr) auto; }
.option-picker__trigger:hover:not(:disabled), .option-picker__trigger--open { border-color: var(--color-focus); }
.option-picker__trigger:focus-visible { border-color: var(--color-focus); box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-focus) 20%, transparent); outline: 0; }
.option-picker__trigger:disabled { background: var(--color-surface-sunken); cursor: not-allowed; opacity: .62; }
.option-picker__trigger > .app-icon { color: var(--color-text-muted); }
.option-picker__selection { display: grid; min-width: 0; }
.option-picker__selection strong, .option-picker__selection small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.option-picker__selection strong, .option-picker__placeholder { font-size: .76rem; font-weight: 650; }
.option-picker__selection small { color: var(--color-text-muted); font-size: .63rem; margin-top: .05rem; }
.option-picker__placeholder { color: var(--color-text-muted); }
.option-picker__menu { background: var(--color-surface-raised); border: 1px solid var(--color-border-strong); border-radius: var(--radius-lg); box-shadow: var(--shadow-md); display: grid; max-height: min(24rem, calc(100vh - 1rem)); overflow: hidden; position: fixed; z-index: 1400; }
.option-picker__menu--split { grid-template-columns: minmax(0, 1.1fr) minmax(13rem, .9fr); }
.option-picker__primary, .option-picker__secondary { display: flex; min-height: 0; flex-direction: column; }
.option-picker__secondary { border-inline-start: 1px solid var(--color-border); background: color-mix(in srgb, var(--color-surface-sunken) 34%, var(--color-surface-raised)); }
.option-picker__secondary header { display: grid; gap: .08rem; padding: .7rem .75rem .55rem; }
.option-picker__secondary header span { font-size: .76rem; font-weight: 750; }
.option-picker__secondary header small { color: var(--color-text-muted); font-size: .63rem; }
.option-picker__search { align-items: center; border-block-end: 1px solid var(--color-border); color: var(--color-text-muted); display: flex; gap: .5rem; padding: .62rem .7rem; }
.option-picker__search input { background: transparent; border: 0; color: var(--color-text); font: inherit; font-size: .74rem; min-width: 0; outline: 0; width: 100%; }
.option-picker__search input::placeholder { color: var(--color-text-faint); }
.option-picker__list { min-height: 0; overflow: auto; padding: .35rem; }
.option-picker__option { align-items: center; background: transparent; border: 0; border-radius: var(--radius-sm); color: var(--color-text); cursor: pointer; display: flex; gap: .6rem; justify-content: space-between; min-height: 2.8rem; padding: .55rem .6rem; text-align: left; width: 100%; }
.option-picker__option:hover:not(:disabled), .option-picker__option:focus-visible, .option-picker__option--active { background: var(--color-surface-hover); outline: 0; }
.option-picker__option--active { box-shadow: inset 3px 0 var(--color-accent); }
.option-picker__option--selected { color: var(--color-accent); }
.option-picker__option:disabled { cursor: not-allowed; opacity: .5; }
.option-picker__option > span:first-child { display: grid; min-width: 0; }
.option-picker__option strong, .option-picker__option small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.option-picker__option strong { font-size: .73rem; }
.option-picker__option small { color: var(--color-text-muted); font-size: .63rem; margin-top: .08rem; }
.option-picker__option-meta { align-items: center; color: var(--color-text-muted); display: flex; gap: .25rem; }
.option-picker__option-meta small { margin: 0; }
.option-picker__empty { color: var(--color-text-muted); font-size: .72rem; margin: 0; padding: 1.5rem .6rem; text-align: center; }
.option-picker-menu-enter-active, .option-picker-menu-leave-active { transition: opacity var(--transition-fast), transform var(--transition-fast); transform-origin: top left; }
.option-picker-menu-enter-from, .option-picker-menu-leave-to { opacity: 0; transform: translateY(-.25rem) scale(.985); }
@media (max-width: 45rem) { .option-picker__menu--split { grid-template-columns: 1fr; } .option-picker__secondary { border-block-start: 1px solid var(--color-border); border-inline-start: 0; max-height: 12rem; } .option-picker__primary { max-height: 12rem; } }
</style>
