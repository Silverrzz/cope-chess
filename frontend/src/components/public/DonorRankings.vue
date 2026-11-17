<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import ContentState from '@/components/public/ContentState.vue'
import { errorMessage } from '@/components/public/format'

interface DonorRow {
  donor_key: string
  name: string
  cpu_hours: number | null
  games: number
  timed_games: number
}

interface DonorsResponse {
  donors: DonorRow[]
  completed_games: number
  unattributed_games: number
}

defineProps<{ compact?: boolean }>()

const count = new Intl.NumberFormat()
const hours = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 })
const data = ref<DonorsResponse | null>(null)
const error = ref('')
let controller: AbortController | undefined
const untimed = computed(() => data.value?.donors.reduce((total, donor) => total + donor.games - donor.timed_games, 0) ?? 0)

function formatHours(value: number | null): string {
  if (value === null) return '—'
  return value > 0 && value < 0.01 ? '<0.01' : hours.format(value)
}

async function load(): Promise<void> {
  controller?.abort()
  controller = new AbortController()
  error.value = ''
  try {
    data.value = await api.get<DonorsResponse>('/api/donors', { signal: controller.signal })
  } catch (cause) {
    if ((cause as Error).name !== 'AbortError') error.value = errorMessage(cause, 'Donor rankings could not be loaded.')
  }
}

onMounted(load)
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <div class="donor-rankings" :class="{ 'donor-rankings--compact': compact }">
    <ContentState :compact="compact" v-if="error" kind="error" :message="error" action-label="Try again" @action="load" />
    <ContentState :compact="compact" v-else-if="!data" kind="loading" title="Loading donors" />
    <template v-else>
      <div v-if="data.donors.length" class="donor-table" :class="{ panel: !compact }">
        <table aria-label="Hardware donor rankings">
          <thead>
            <tr><th scope="col">Rank</th><th scope="col">Donor</th><th scope="col">CPU hours</th><th scope="col">Games tested</th></tr>
          </thead>
          <tbody>
            <tr v-for="(donor, index) in data.donors" :key="donor.donor_key">
              <td>{{ count.format(index + 1) }}</td>
              <th scope="row">{{ donor.name }}</th>
              <td>{{ formatHours(donor.cpu_hours) }}</td>
              <td>{{ count.format(donor.games) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <ContentState :compact="compact" v-else kind="empty" title="No attributed games yet" />

      <footer v-if="!compact" class="donor-notes muted">
        <p>{{ count.format(data.completed_games) }} completed games in the available history.</p>
        <p>CPU hours use recorded game runtime and allocated threads.</p>
        <p v-if="data.unattributed_games">{{ count.format(data.unattributed_games) }} completed games have no recorded worker.</p>
        <p v-if="untimed">{{ count.format(untimed) }} attributed games have no recorded runtime and are counted under games tested.</p>
      </footer>
    </template>
  </div>
</template>

<style scoped>
.donor-rankings { display: grid; gap: var(--space-4); }

.donor-notes p {
  margin: 0.35rem 0 0;
}

.donor-table {
  min-width: 0;
  overflow-x: auto;
  font-size: 0.875rem;
}

.donor-table table {
  width: 100%;
  border-collapse: collapse;
}

.donor-table th,
.donor-table td {
  padding: var(--space-3) clamp(var(--space-2), 2vw, var(--space-4));
  text-align: start;
  vertical-align: middle;
}

.donor-table thead th {
  background: var(--color-surface-raised);
  color: var(--color-text-muted);
  font-size: 0.75rem;
  font-weight: 650;
}

.donor-table tbody tr {
  border-block-start: 1px solid var(--color-border);
}

.donor-table tbody th {
  overflow-wrap: anywhere;
}

.donor-table tbody td {
  white-space: nowrap;
}

.donor-table :is(th, td):nth-last-child(-n + 2) {
  text-align: end;
  font-variant-numeric: tabular-nums;
}

.donor-notes {
  font-size: 0.82rem;
}

.donor-rankings--compact :deep(.content-state) {
  border: 0;
}

.donor-rankings--compact .donor-table th,
.donor-rankings--compact .donor-table td {
  padding-block: var(--space-2);
}
</style>
