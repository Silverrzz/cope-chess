<script setup lang="ts">
import { computed, nextTick, onMounted, ref, useId } from 'vue'
import { api } from '@/api/client'
import { useToast } from '@/composables/useToast'
import InlineFeedback from './InlineFeedback.vue'
import { errorText } from './format'
import type { DonorWorker } from './types'

const props = defineProps<{ workerId: number }>()
const endpoint = '/api/admin/workers/donors'
const toast = useToast()
const hintId = useId()
const workers = ref<DonorWorker[] | null>(null)
const group = ref<string | null>('')
const newName = ref('')
const nameInput = ref<HTMLInputElement | null>(null)
const busy = ref(false)
const error = ref('')

const currentGroup = computed(() => workers.value?.find(worker => worker.id === props.workerId)?.donor_name ?? '')
const groupNames = computed(() => {
  const names = workers.value?.flatMap(worker => worker.donor_name ? [worker.donor_name] : [])
  return [...new Set(names)].sort((a, b) => a.localeCompare(b))
})
const requestedGroup = computed(() => {
  if (group.value !== null) return group.value
  const name = newName.value.trim()
  return groupNames.value.find(existing => existing.toLowerCase() === name.toLowerCase()) ?? name
})
const canSave = computed(() => !busy.value && requestedGroup.value !== currentGroup.value && (group.value !== null || !!requestedGroup.value))

function updateGroups(result: DonorWorker[]): void {
  workers.value = result
  group.value = currentGroup.value
  newName.value = ''
}

async function load(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    const result = await api.get<{ workers: DonorWorker[] }>(endpoint)
    updateGroups(result.workers)
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}

async function focusName(): Promise<void> {
  if (group.value === null) {
    await nextTick()
    nameInput.value?.focus()
  }
}

async function save(): Promise<void> {
  if (!canSave.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await api.put<{ workers: DonorWorker[]; message: string }>(endpoint, {
      body: { worker_ids: [props.workerId], name: requestedGroup.value || null },
    })
    updateGroups(result.workers)
    toast.success(result.message)
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <form class="donor-group" @submit.prevent="save">
    <label>
      <span>Donor group</span>
      <select v-model="group" class="input" :aria-describedby="hintId" :disabled="busy || !workers" @change="focusName">
        <option value="">{{ busy && !workers ? 'Loading groups…' : 'Ungrouped' }}</option>
        <option v-for="name in groupNames" :key="name" :value="name">{{ name }}</option>
        <option :value="null">Create new group…</option>
      </select>
    </label>
    <label v-if="group === null">
      <span>Public donor name</span>
      <input ref="nameInput" v-model="newName" class="input" required maxlength="80" :disabled="busy">
    </label>
    <button v-if="workers" class="button button--primary button--small" type="submit" :disabled="!canSave">
      {{ busy ? 'Saving…' : 'Save group' }}
    </button>
    <button v-else-if="error" class="button button--secondary button--small" type="button" @click="load">Retry</button>
    <p :id="hintId">Sets the public donor name for this worker’s past and future games.</p>
    <InlineFeedback :message="error" />
  </form>
</template>

<style scoped>
.donor-group {
  align-items: end;
  border-top: 1px solid var(--color-border);
  display: flex;
  flex-wrap: wrap;
  gap: .55rem;
  padding: .85rem 1rem;
}

.donor-group label {
  display: grid;
  flex: 1 1 10rem;
  font-size: .64rem;
  font-weight: 700;
  gap: .3rem;
  min-width: 0;
}

.donor-group p,
.donor-group :deep(.inline-feedback) {
  flex-basis: 100%;
  margin: 0;
}

.donor-group p {
  color: var(--color-text-muted);
  font-size: .64rem;
}
</style>
