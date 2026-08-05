<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import StatusBadge from '@/components/admin/StatusBadge.vue'
import { errorText, formatDate, formatDuration, humanize } from '@/components/admin/format'
import { buildTournamentSummaryMarkdown } from '@/components/public/tournamentSummary'
import AppIcon from '@/components/ui/AppIcon.vue'
import type { Tournament, TournamentEstimate } from '@/components/admin/types'
import type { TournamentDetailResponse } from '@/components/public/types'

interface TournamentListItem {
  record: Tournament
  format?: string
  time_control?: string
  summary?: { finished: number; total: number }
  estimate?: TournamentEstimate
}
interface Response { tournaments: TournamentListItem[]; status_filter?: string | null; statuses: string[] }

const tournamentStatusOrder: Record<string, number> = {
  running: 0,
  paused: 1,
  scheduled: 2,
  draft: 2,
  finished: 3,
  aborted: 3,
}

const route = useRoute()
const router = useRouter()
const toast = useToast()
const { confirm } = useConfirm()
const data = ref<Response | null>(null)
const loading = ref(true)
const error = ref('')
const query = ref('')
const deleting = ref<number | null>(null)
const copying = ref<number | null>(null)
const status = computed(() => typeof route.query.status === 'string' ? route.query.status : '')
const filtered = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase()
  const tournaments = [...(data.value?.tournaments ?? [])].sort((left, right) => {
    const statusDifference = (tournamentStatusOrder[left.record.status] ?? Number.MAX_SAFE_INTEGER)
      - (tournamentStatusOrder[right.record.status] ?? Number.MAX_SAFE_INTEGER)
    if (statusDifference !== 0) return statusDifference
    if (left.record.status === 'scheduled' && right.record.status === 'scheduled') {
      return new Date(left.record.scheduled_start_at || 0).getTime() - new Date(right.record.scheduled_start_at || 0).getTime()
    }
    return 0
  })
  if (!needle) return tournaments
  return tournaments.filter(({ record }) => record.name.toLocaleLowerCase().includes(needle))
})

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.get<Response>('/api/admin/tournaments', status.value ? { query: { status: status.value } } : {})
  } catch (cause) { error.value = errorText(cause) }
  finally { loading.value = false }
}

async function setStatus(next: string): Promise<void> {
  await router.push({ query: next ? { status: next } : {} })
}

async function remove(item: TournamentListItem): Promise<void> {
  const accepted = await confirm({
    title: 'Delete tournament?',
    message: `Delete “${item.record.name}” and all of its games? This cannot be undone.`,
    confirmLabel: 'Delete tournament',
    tone: 'danger',
  })
  if (!accepted) return
  deleting.value = item.record.id
  try {
    const response = await api.delete<{ message: string }>(`/api/admin/tournaments/${item.record.id}`)
    toast.success(response.message)
    await load()
  } catch (cause) { toast.error(cause); error.value = errorText(cause) }
  finally { deleting.value = null }
}

async function copySummary(item: TournamentListItem): Promise<void> {
  copying.value = item.record.id
  try {
    const detail = await api.get<TournamentDetailResponse>(`/api/tournaments/${item.record.id}`)
    const summary = buildTournamentSummaryMarkdown(detail, window.location.origin)
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(summary)
    else fallbackCopy(summary)
    toast.success('Tournament results copied as Markdown.')
  } catch (cause) {
    toast.error(cause)
  } finally {
    copying.value = null
  }
}

function fallbackCopy(value: string): void {
  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.append(textarea)
  textarea.select()
  const copied = document.execCommand('copy')
  textarea.remove()
  if (!copied) throw new Error('Copy failed')
}

function scheduleText(item: TournamentListItem): string {
  if (item.record.status === 'scheduled') return `Starts ${formatDate(item.record.scheduled_start_at)}`
  if (item.record.status === 'running' && item.estimate?.estimated_finish_at) return `Est. ${formatDate(item.estimate.estimated_finish_at)}`
  if (item.record.status === 'paused') return `${formatDuration(item.estimate?.estimated_remaining_seconds)} after resume`
  if (['finished', 'aborted'].includes(item.record.status)) return formatDate(item.record.finished_at)
  return 'Not scheduled'
}

watch(status, load)
onMounted(load)
</script>

<template>
  <div class="admin-page page-stack">
    <AdminPageHeader title="Tournaments">
      <template #actions><RouterLink class="button button--primary" to="/admin/tournaments/new">New tournament</RouterLink></template>
    </AdminPageHeader>
    <InlineFeedback :message="error" />

    <section class="panel tournament-index">
      <div class="tournament-index__toolbar">
        <label class="status-filter">
          <span>Status</span>
          <select class="input" :value="status" @change="setStatus(($event.target as HTMLSelectElement).value)">
            <option value="">All tournaments</option>
            <option v-for="option in data?.statuses ?? []" :key="option" :value="option">{{ humanize(option) }}</option>
          </select>
        </label>
        <label class="table-search">
          <span class="sr-only">Search tournaments</span>
          <svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></svg>
          <input v-model="query" class="input" type="search" placeholder="Search tournaments">
        </label>
      </div>

      <div v-if="loading" class="table-loading" role="status">Loading tournaments…</div>
      <div v-else-if="filtered.length" class="table-scroll">
        <table class="data-table">
          <thead><tr><th>Tournament</th><th>Status</th><th>Format</th><th>Time control</th><th>Schedule / finish</th><th>Progress</th><th><span class="sr-only">Actions</span></th></tr></thead>
          <tbody>
            <tr v-for="item in filtered" :key="item.record.id">
              <td><RouterLink :to="`/admin/tournaments/${item.record.id}`"><strong>{{ item.record.name }}</strong><small>Created {{ item.record.created_at ?? 'recently' }}</small></RouterLink></td>
              <td><StatusBadge :status="item.record.status" /></td>
              <td>{{ item.format ?? humanize(item.record.config.format) }}</td>
              <td>{{ item.time_control ?? humanize(item.record.config.time_control.category) }}</td>
              <td class="schedule-cell">{{ scheduleText(item) }}</td>
              <td>
                <div class="progress-cell"><span><strong>{{ item.summary?.finished ?? 0 }}</strong> / {{ item.summary?.total ?? 0 }}</span><progress :value="item.summary?.finished ?? 0" :max="Math.max(item.summary?.total ?? 0, 1)" /></div>
              </td>
              <td class="row-actions">
                <RouterLink class="button button--ghost button--small" :to="`/admin/tournaments/${item.record.id}`">Open</RouterLink>
                <button v-if="item.record.status === 'finished'" class="icon-button" type="button" :disabled="copying === item.record.id" :aria-label="`Copy ${item.record.name} results as Markdown`" title="Copy results as Markdown" @click="copySummary(item)">
                  <AppIcon name="copy" :size="16" />
                </button>
                <button v-if="!['scheduled', 'running'].includes(item.record.status)" class="icon-button icon-button--danger" type="button" :disabled="deleting === item.record.id" :aria-label="`Delete ${item.record.name}`" @click="remove(item)">
                  <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M5 7h14M9 7V4h6v3m2 0-1 13H8L7 7m3 4v5m4-5v5" /></svg>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <AdminEmptyState v-else :title="query ? 'No matching tournaments' : 'No tournaments yet'">
        <RouterLink v-if="!query" class="button button--primary button--small" to="/admin/tournaments/new">New tournament</RouterLink>
      </AdminEmptyState>
    </section>
  </div>
</template>

<style scoped>
.tournament-index { overflow: hidden; padding: 0; }
.tournament-index__toolbar { align-items: center; border-bottom: 1px solid var(--color-border, #d9e0ea); display: flex; gap: .8rem; justify-content: space-between; padding: .75rem; }
.status-filter { align-items: center; display: flex; gap: .55rem; }
.status-filter > span { color: var(--color-text-muted, #64748b); font-size: .7rem; font-weight: 700; }
.status-filter select { min-width: 10.5rem; }
.table-search { flex: 0 1 16rem; position: relative; }
.table-search svg { fill: none; height: 1rem; left: .65rem; position: absolute; stroke: var(--color-text-muted, #64748b); stroke-linecap: round; stroke-width: 1.8; top: 50%; transform: translateY(-50%); width: 1rem; }
.table-search input { padding-left: 2rem; width: 100%; }
.table-scroll { overflow-x: auto; }
.data-table { border-collapse: collapse; min-width: 66rem; width: 100%; }
.data-table th { color: var(--color-text-muted, #64748b); font-size: .67rem; font-weight: 700; letter-spacing: .04em; padding: .65rem .8rem; text-align: left; text-transform: uppercase; }
.data-table td { border-top: 1px solid var(--color-border, #d9e0ea); font-size: .78rem; padding: .72rem .8rem; vertical-align: middle; }
.data-table tbody tr:hover { background: var(--color-surface-subtle, #f6f8fb); }
.data-table td:first-child a { color: inherit; display: grid; text-decoration: none; }
.data-table td:first-child small { color: var(--color-text-muted, #64748b); font-size: .66rem; margin-top: .18rem; }
.progress-cell { display: grid; gap: .3rem; min-width: 7rem; }
.progress-cell span { color: var(--color-text-muted, #64748b); font-size: .7rem; }
.progress-cell progress { accent-color: var(--color-accent, #315fcc); height: .35rem; width: 100%; }
.schedule-cell { color: var(--color-text-muted, #64748b); min-width: 10rem; }
.row-actions { align-items: center; display: flex; gap: .3rem; justify-content: flex-end; }
.row-actions svg { fill: none; height: 1rem; stroke: currentColor; stroke-linecap: round; stroke-linejoin: round; stroke-width: 1.7; width: 1rem; }
.table-loading { color: var(--color-text-muted, #64748b); min-height: 14rem; padding: 2rem; }
@media (max-width: 44rem) { .tournament-index__toolbar { align-items: stretch; flex-direction: column; } .table-search { flex-basis: auto; } .status-filter { align-items: stretch; flex-direction: column; gap: .3rem; } .status-filter select { width: 100%; } }
</style>
