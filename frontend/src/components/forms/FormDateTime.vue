<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '@/api/client'

const props = defineProps<{ modelValue?: unknown; timezone?: string | null | undefined; id: string; disabled?: boolean; invalid?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const day = ref('')
const clock = ref('')
const choices = ref<{ value: string; label: string; utc: string }[]>([])
const busy = ref(false)
const error = ref('')
const zone = computed(() => props.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC')
let generation = 0
let controller: AbortController | null = null
let timer: ReturnType<typeof setTimeout> | undefined

watch(() => props.modelValue, (value) => {
  if (typeof value !== 'string') return
  const parts = value.split('T')
  day.value = /^\d{4}-\d{2}-\d{2}$/.test(parts[0] ?? '') ? parts[0]! : ''
  clock.value = /^(\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)/.exec(parts[1] ?? '')?.[1] ?? ''
}, { immediate: true })

function edit(part: 'day' | 'clock', value: string): void {
  if (part === 'day') day.value = value
  else clock.value = value
  emit('update:modelValue', day.value || clock.value ? `${day.value}T${clock.value}` : '')
}

watch([day, clock, zone], () => {
  const current = ++generation
  controller?.abort()
  clearTimeout(timer)
  choices.value = []
  error.value = ''
  busy.value = false
  if (!day.value || !clock.value) return
  const local = `${day.value}T${clock.value}`
  timer = setTimeout(async () => {
    busy.value = true
    controller = new AbortController()
    try {
      const response = await api.get<{ data: { options: typeof choices.value } }>('/api/forms/datetime-options', {
        query: { local_date: day.value, local_time: clock.value, timezone: zone.value }, signal: controller.signal,
      })
      if (current !== generation) return
      choices.value = response.data.options
      if (!choices.value.length) { error.value = 'This local time has no supported occurrence. Choose another date or time.'; emit('update:modelValue', local) }
      else if (choices.value.some((choice) => choice.value === props.modelValue)) return
      else if (choices.value.length === 1) emit('update:modelValue', choices.value[0]!.value)
      else emit('update:modelValue', local)
    } catch (cause) {
      if (current !== generation || (cause instanceof DOMException && cause.name === 'AbortError')) return
      error.value = cause instanceof Error ? cause.message : 'The timezone could not be checked.'
    } finally { if (current === generation) busy.value = false }
  }, 180)
}, { immediate: true })

onBeforeUnmount(() => { generation++; controller?.abort(); clearTimeout(timer) })
</script>

<template>
  <div class="datetime-field" role="group" :aria-labelledby="`${id}-label`">
    <div class="datetime-field__inputs">
      <input :id="id" :value="day" type="date" class="input" :disabled="disabled" :aria-invalid="invalid || undefined" aria-label="Date" @input="edit('day', ($event.target as HTMLInputElement).value)">
      <input :value="clock" type="time" step="1" class="input" :disabled="disabled" :aria-invalid="invalid || undefined" aria-label="Local time" @input="edit('clock', ($event.target as HTMLInputElement).value)">
    </div>
    <small>{{ zone }}<span v-if="busy" role="status"> · Checking time…</span></small>
    <label v-if="choices.length > 1" class="datetime-field__occurrence"><span>This local time occurs twice. Choose its UTC offset.</span><select class="input" :value="typeof modelValue === 'string' ? modelValue : ''" :disabled="disabled" @change="emit('update:modelValue', ($event.target as HTMLSelectElement).value)"><option value="">Choose an occurrence</option><option v-for="choice in choices" :key="choice.value" :value="choice.value">{{ choice.label }}</option></select></label>
    <p v-if="error" class="field-error" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.datetime-field{display:grid;gap:.45rem}.datetime-field__inputs{display:grid;gap:.5rem;grid-template-columns:1fr 1fr}.datetime-field small{color:var(--color-text-muted)}.datetime-field__occurrence{display:grid;gap:.4rem;font-size:.8rem}.field-error{color:var(--color-danger,#b91c1c);font-size:.8rem;margin:0}@media(max-width:30rem){.datetime-field__inputs{grid-template-columns:1fr}}
</style>
