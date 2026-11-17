<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/api/client'
import ContentState from '@/components/public/ContentState.vue'
import QuorumCredential from '@/components/public/QuorumCredential.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import type { FeedbackTone } from '@/types/ui'
import type { BallotResult } from '@/quorum'
import { resultLabels, witnessReportUrl, type WitnessStatus } from '@/quorum-results'

interface BallotSummary {
  id: string
  title: string
  opens_at: string
  closes_at: string
  status: 'scheduled' | 'open' | 'completed'
  result: BallotResult | null
  witnesses: WitnessStatus[]
}

const route = useRoute()
const router = useRouter()
const ballots = ref<BallotSummary[]>([])
const enrollmentAvailable = ref(false)
const loading = ref(true)
const error = ref('')
const dialog = ref<HTMLDialogElement>()
const managingCredential = ref(false)
const sections = computed(() => [
  { id: 'active', title: 'Active votes', ballots: ballots.value.filter(ballot => ballot.status !== 'completed'), empty: 'No active votes.' },
  { id: 'completed', title: 'Completed votes', ballots: ballots.value.filter(ballot => ballot.status === 'completed'), empty: 'No completed votes yet.' },
])
const dates = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
const resultTones: Record<BallotResult['outcome'], FeedbackTone> = {
  passed: 'success',
  rejected: 'danger',
  quorum_not_met: 'warning',
  report_only: 'info',
}
const motionColors = ['success', 'danger', 'text-muted']
const pollColors = ['accent', 'success', 'warning', 'danger', 'text-muted']

function voteColor(result: BallotResult, index: number) {
  const colors = result.outcome === 'report_only' ? pollColors : motionColors
  return 'var(--color-' + colors[index % colors.length] + ')'
}


function openCredential() {
  if (!dialog.value || dialog.value.open) return
  managingCredential.value = true
  dialog.value.showModal()
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const directory = await api.get<{ ballots: BallotSummary[]; eligibility_check_available: boolean }>('/api/quorum')
    ballots.value = directory.ballots
    enrollmentAvailable.value = directory.eligibility_check_available
    if (enrollmentAvailable.value && route.query.credential === '1') {
      openCredential()
      const query = { ...route.query }
      delete query.credential
      await router.replace({ query })
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not load Quorum.'
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<template>
  <div class="page-container page-stack">
    <PageHeader title="Quorum">
      <template #actions>
        <BaseButton size="small" variant="ghost" :to="{ name: 'quorum-verify' }">Verify evidence</BaseButton>
        <BaseButton v-if="enrollmentAvailable" size="small" aria-haspopup="dialog" @click="openCredential">Voting credential</BaseButton>
      </template>
    </PageHeader>
    <ContentState v-if="loading" kind="loading" compact title="Loading ballots" />
    <ContentState v-else-if="error" kind="error" compact :message="error" action-label="Try again" @action="load" />
    <div v-else class="ballot-sections">
      <section v-for="section in sections" :key="section.id" class="ballot-section" :aria-labelledby="'quorum-' + section.id">
        <h2 :id="'quorum-' + section.id">{{ section.title }}</h2>
        <ul v-if="section.ballots.length" class="wide-card-grid ballot-grid">
          <li v-for="ballot in section.ballots" :key="ballot.id" class="panel panel__body ballot-card">
            <header class="ballot-card__header">
              <h3>{{ ballot.title }}</h3>
              <StatusBadge v-if="ballot.result" :tone="resultTones[ballot.result.outcome]">
                {{ resultLabels[ballot.result.outcome] }}
              </StatusBadge>
              <StatusBadge v-else :tone="ballot.status === 'open' ? 'success' : 'neutral'" :dot="ballot.status === 'open'">
                {{ ballot.status === 'open' ? 'Open' : ballot.status === 'scheduled' ? 'Scheduled' : 'Completed' }}
              </StatusBadge>
            </header>
            <div v-if="ballot.result" class="ballot-result">
              <div class="ballot-result__bar" aria-hidden="true">
                <span v-for="(count, index) in ballot.result.counts" v-show="count > 0" :key="index"
                  :style="{ flexGrow: count, backgroundColor: voteColor(ballot.result, index) }" />
              </div>
              <dl class="ballot-result__counts">
                <div v-for="(option, index) in ballot.result.options" :key="index" :style="{ '--vote-color': voteColor(ballot.result, index) }">
                  <dt>{{ option }}</dt>
                  <dd>{{ ballot.result.counts[index] }}</dd>
                </div>
              </dl>
            </div>
            <div v-if="ballot.witnesses?.length" class="ballot-card__witnesses">
              <div class="toolbar toolbar--between">
                <strong>Witnesses</strong>
                <StatusBadge v-if="ballot.witnesses.every(witness => witness.signed_at)" tone="success">Certified</StatusBadge>
              </div>
              <ul>
                <li v-for="witness in ballot.witnesses" :key="witness.public_key">
                  <a v-if="witness.signed_at" :href="witnessReportUrl(ballot.id, witness)" target="_blank" rel="noopener noreferrer">{{ witness.name }} · Verified</a>
                  <span v-else class="muted">{{ witness.name }} · {{ ballot.result ? 'Awaiting signature' : 'Assigned' }}</span>
                </li>
              </ul>
            </div>
            <footer class="ballot-card__footer">
              <div class="ballot-card__meta muted">
                <time :datetime="ballot.status === 'scheduled' ? ballot.opens_at : ballot.closes_at">
                  {{ ballot.status === 'scheduled' ? 'Opens' : ballot.status === 'open' ? 'Closes' : 'Closed' }}
                  {{ dates.format(new Date(ballot.status === 'scheduled' ? ballot.opens_at : ballot.closes_at)) }}
                </time>
                <span v-if="ballot.result">{{ ballot.result.total }} of {{ ballot.result.electorate }} voted</span>
              </div>
              <BaseButton size="small" :variant="ballot.status === 'open' ? 'primary' : 'secondary'"
                :to="{ name: 'quorum-ballot', params: { id: ballot.id } }"
                :aria-label="(ballot.status === 'open' ? 'Vote on ' : ballot.status === 'completed' ? 'View results for ' : 'View ') + ballot.title">
                {{ ballot.status === 'open' ? 'Vote' : ballot.status === 'completed' ? 'View results' : 'View ballot' }}
                <template #trailing><AppIcon name="arrow-right" :size="16" /></template>
              </BaseButton>
            </footer>
          </li>
        </ul>
        <p v-else class="panel panel__body muted">{{ section.empty }}</p>
      </section>
    </div>

    <Teleport to="body">
      <dialog ref="dialog" class="panel credential-dialog" aria-labelledby="quorum-credential-title" @close="managingCredential = false">
        <header class="panel__header toolbar toolbar--between">
          <h2 id="quorum-credential-title" tabindex="-1" autofocus>Voting credential</h2>
          <BaseButton size="small" variant="ghost" icon-only @click="dialog?.close()">
            <template #icon><AppIcon name="close" :size="18" /></template>
            Close
          </BaseButton>
        </header>
        <div class="panel__body">
          <QuorumCredential v-if="managingCredential" />
        </div>
      </dialog>
    </Teleport>
  </div>
</template>

<style scoped>
.ballot-sections { display: grid; gap: var(--space-8); }
.ballot-section { display: grid; gap: var(--space-3); }
.ballot-section h2 { margin: 0; font-size: 1.25rem; }

.ballot-grid { margin: 0; padding: 0; list-style: none; }
.ballot-card { display: flex; flex-direction: column; gap: var(--space-4); min-width: 0; }
.ballot-card__header,
.ballot-card__footer { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--space-2) var(--space-3); }
.ballot-card h3 { margin: 0; min-width: 0; font-size: 1.05rem; overflow-wrap: anywhere; }
.ballot-card__footer { margin-top: auto; }
.ballot-card__meta { display: grid; gap: var(--space-1); font-size: .875rem; }

.ballot-result { display: grid; gap: var(--space-3); }
.ballot-result__bar {
  display: flex;
  overflow: hidden;
  height: .5rem;
  border-radius: var(--radius-round);
  background: var(--color-surface-sunken);
  box-shadow: inset 0 0 0 1px var(--color-border);
}
.ballot-result__bar span { flex-basis: 0; }
.ballot-result__counts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 7rem), 1fr));
  gap: var(--space-2);
  margin: 0;
}
.ballot-result__counts > div {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  background: var(--color-surface-sunken);
}
.ballot-result__counts dt { font-size: .875rem; overflow-wrap: anywhere; }
.ballot-result__counts dt::before {
  display: inline-block;
  width: .5rem;
  height: .5rem;
  margin-inline-end: var(--space-2);
  border-radius: var(--radius-round);
  background: var(--vote-color);
  content: '';
}
.ballot-result__counts dd { margin: 0; font-weight: 700; font-variant-numeric: tabular-nums; }

.ballot-card__witnesses { display: grid; gap: var(--space-2); font-size: .8125rem; }
.ballot-card__witnesses ul { margin: 0; padding: 0; list-style: none; display: flex; flex-wrap: wrap; gap: var(--space-1) var(--space-4); }

.credential-dialog {
  width: min(28rem, calc(100vw - 2rem));
  max-height: calc(100dvh - 2rem);
  margin: auto;
  padding: 0;
  color: var(--color-text);
  overscroll-behavior: contain;
}
.credential-dialog::backdrop { background: var(--color-overlay); }
.credential-dialog h2 { margin: 0; font-size: 1.125rem; }

</style>
