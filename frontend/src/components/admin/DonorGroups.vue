<script setup lang="ts">
import { computed, ref, useId } from 'vue'

import { api } from '@/api/client'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import { useToast } from '@/composables/useToast'
import { errorText } from './format'

interface DonorWorker {
  id: number
  label: string
  donor_name: string | null
  retired: boolean
}

const endpoint = '/api/admin/workers/donors'
const workers = ref<DonorWorker[]>([])
const selected = ref<number[]>([])
const name = ref('')
const busy = ref(false)
const error = ref('')
const groupNamesId = useId()
const toast = useToast()
const groupNames = computed(() => [...new Set(workers.value.flatMap(worker => worker.donor_name ? [worker.donor_name] : []))])

async function load(event: Event): Promise<void> {
  if (!(event.currentTarget as HTMLDetailsElement).open || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const response = await api.get<{ workers: DonorWorker[] }>(endpoint)
    workers.value = response.workers
    selected.value = selected.value.filter(id => workers.value.some(worker => worker.id === id))
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}

async function save(group: string | null): Promise<void> {
  if (!selected.value.length || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const response = await api.put<{ workers: DonorWorker[]; message: string }>(endpoint, {
      body: { worker_ids: selected.value, name: group },
    })
    workers.value = response.workers
    selected.value = []
    name.value = ''
    toast.success(response.message)
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <details class="panel donor-groups" @toggle="load">
    <summary>Donor groups</summary>
    <p>Select workers and give them a shared public donor name. Their past and future games will appear together on the Donors page.</p>
    <InlineFeedback :message="error" />
    <p v-if="busy && !workers.length" role="status">Loading workers…</p>
    <form v-else-if="workers.length" @submit.prevent="save(name.trim())">
      <fieldset :disabled="busy">
        <legend>Workers</legend>
        <label v-for="worker in workers" :key="worker.id" class="donor-worker">
          <input v-model="selected" type="checkbox" :value="worker.id" />
          <span>{{ worker.label }} <small>#{{ worker.id }}{{ worker.retired ? ' · Retired' : '' }}</small></span>
          <small>{{ worker.donor_name || 'Ungrouped' }}</small>
        </label>
      </fieldset>
      <label class="donor-name">
        Public donor name
        <input v-model="name" :list="groupNamesId" class="input" maxlength="80" :disabled="busy" />
        <datalist :id="groupNamesId"><option v-for="group in groupNames" :key="group" :value="group" /></datalist>
      </label>
      <div class="button-row">
        <button class="button button--primary button--small" type="submit" :disabled="busy || !selected.length || !name.trim()">Save group</button>
        <button class="button button--ghost button--small" type="button" :disabled="busy || !selected.length" @click="save(null)">Clear grouping</button>
      </div>
    </form>
    <p v-else-if="!error">No workers are available to group.</p>
  </details>
</template>

<style scoped>
.donor-groups summary {
  font-size: 0.9rem;
  font-weight: 700;
  cursor: pointer;
}

.donor-groups p,
.donor-groups small {
  color: var(--color-text-muted);
  font-size: 0.75rem;
}

.donor-groups form {
  display: grid;
  gap: 0.75rem;
  margin-block-start: 0.75rem;
}

.donor-groups fieldset {
  max-height: 15rem;
  margin: 0;
  padding: 0.5rem;
  overflow: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}

.donor-worker {
  display: grid;
  grid-template-columns: auto 1fr minmax(5rem, auto);
  align-items: baseline;
  gap: 0.5rem;
  padding: 0.3rem 0.4rem;
  font-size: 0.8rem;
  overflow-wrap: anywhere;
}

.donor-worker > small {
  text-align: end;
}

.donor-name {
  display: grid;
  gap: 0.3rem;
  max-width: 24rem;
  font-size: 0.8rem;
}
</style>
