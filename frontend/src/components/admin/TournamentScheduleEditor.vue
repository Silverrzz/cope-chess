<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { formatDate, formatRelativeDate } from './format'

const props = withDefaults(defineProps<{
  initialValue?: string | null | undefined
  pending?: boolean
}>(), {
  initialValue: null,
  pending: false,
})

const emit = defineEmits<{
  submit: [scheduledStartAt: string]
  cancel: []
}>()

const date = ref('')
const time = ref('')
const error = ref('')

const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Local time'
const selected = computed(() => localDate(date.value, time.value))
const timezoneLabel = computed(() => {
  const offset = new Intl.DateTimeFormat(undefined, { timeZoneName: 'shortOffset' })
    .formatToParts(selected.value ?? new Date())
    .find((part) => part.type === 'timeZoneName')?.value
  return offset ? `${timezone} · ${offset}` : timezone
})
const preview = computed(() => selected.value
  ? `${formatDate(selected.value.toISOString())} · ${formatRelativeDate(selected.value.toISOString())}`
  : 'Choose a date and exact time')

watch(() => props.initialValue, reset, { immediate: true })

function reset(value: string | null | undefined): void {
  const parsed = value ? new Date(value) : nextQuarterHour()
  const selectedDate = Number.isNaN(parsed.getTime()) ? nextQuarterHour() : parsed
  date.value = localDateValue(selectedDate)
  time.value = localTimeValue(selectedDate)
  error.value = ''
}

function chooseDay(offset: number): void {
  const selectedDate = selected.value ?? nextQuarterHour()
  const target = new Date()
  target.setHours(selectedDate.getHours(), selectedDate.getMinutes(), 0, 0)
  target.setDate(target.getDate() + offset)
  date.value = localDateValue(target)
  time.value = localTimeValue(target)
  error.value = ''
}

function startNow(): void {
  emit('submit', new Date().toISOString())
}

function submit(): void {
  const value = selected.value
  if (!value) {
    error.value = 'Choose a valid local date and time.'
    return
  }
  error.value = ''
  emit('submit', value.toISOString())
}

function nextQuarterHour(): Date {
  const value = new Date()
  value.setSeconds(0, 0)
  value.setMinutes(Math.ceil((value.getMinutes() + 1) / 15) * 15)
  return value
}

function localDate(value: string, clock: string): Date | null {
  if (!value || !clock) return null
  const parts = value.split('-').map(Number)
  const clockParts = clock.split(':').map(Number)
  if (parts.length !== 3 || clockParts.length < 2) return null
  const year = Number(parts[0])
  const month = Number(parts[1])
  const day = Number(parts[2])
  const hour = Number(clockParts[0])
  const minute = Number(clockParts[1])
  const parsed = new Date(year, month - 1, day, hour, minute, 0, 0)
  if (
    parsed.getFullYear() !== year
    || parsed.getMonth() !== month - 1
    || parsed.getDate() !== day
    || parsed.getHours() !== hour
    || parsed.getMinutes() !== minute
  ) return null
  return parsed
}

function localDateValue(value: Date): string {
  return [value.getFullYear(), pad(value.getMonth() + 1), pad(value.getDate())].join('-')
}

function localTimeValue(value: Date): string {
  return `${pad(value.getHours())}:${pad(value.getMinutes())}`
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}
</script>

<template>
  <section class="panel schedule-editor">
    <div class="schedule-editor__heading">
      <div><span>Start time</span><h2>{{ initialValue ? 'Reschedule tournament' : 'Schedule tournament' }}</h2></div>
      <button class="button button--ghost button--small" type="button" :disabled="pending" @click="emit('cancel')">Close</button>
    </div>

    <div class="schedule-editor__shortcuts" aria-label="Date shortcuts">
      <button class="button button--secondary button--small" type="button" :disabled="pending" @click="startNow">Start now</button>
      <button class="button button--ghost button--small" type="button" :disabled="pending" @click="chooseDay(0)">Today</button>
      <button class="button button--ghost button--small" type="button" :disabled="pending" @click="chooseDay(1)">Tomorrow</button>
      <button class="button button--ghost button--small" type="button" :disabled="pending" @click="chooseDay(7)">In one week</button>
    </div>

    <form class="schedule-editor__form" @submit.prevent="submit">
      <label><span>Date</span><input v-model="date" class="input" type="date" required></label>
      <label><span>Exact time</span><input v-model="time" class="input" type="time" step="60" required></label>
      <button class="button button--primary" type="submit" :disabled="pending">{{ pending ? 'Scheduling…' : initialValue ? 'Save new time' : 'Schedule' }}</button>
    </form>

    <div class="schedule-editor__preview">
      <strong>{{ preview }}</strong>
      <span>{{ timezoneLabel }}</span>
    </div>
    <p v-if="error" class="schedule-editor__error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.schedule-editor { display: grid; gap: 1rem; padding: 1rem; }
.schedule-editor__heading { align-items: start; display: flex; gap: 1rem; justify-content: space-between; }
.schedule-editor__heading span { color: var(--color-text-muted, #64748b); font-size: .64rem; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; }
.schedule-editor__heading h2 { font-size: 1rem; margin: .18rem 0 0; }
.schedule-editor__shortcuts { display: flex; flex-wrap: wrap; gap: .4rem; }
.schedule-editor__form { align-items: end; display: grid; gap: .7rem; grid-template-columns: minmax(9rem, 1fr) minmax(8rem, .8fr) auto; }
.schedule-editor__form label { display: grid; gap: .35rem; }
.schedule-editor__form label span { font-size: .72rem; font-weight: 650; }
.schedule-editor__preview { background: var(--color-surface-subtle, #f6f8fb); border: 1px solid var(--color-border, #d9e0ea); border-radius: .55rem; display: grid; gap: .18rem; padding: .7rem .8rem; }
.schedule-editor__preview strong { font-size: .82rem; }
.schedule-editor__preview span { color: var(--color-text-muted, #64748b); font-size: .68rem; }
.schedule-editor__error { color: var(--color-danger, #b42318); font-size: .75rem; margin: 0; }
@media (max-width: 42rem) { .schedule-editor__form { grid-template-columns: 1fr; } .schedule-editor__form .button { width: 100%; } }
</style>
