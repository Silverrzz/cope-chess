<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/api/client'
import ContentState from '@/components/public/ContentState.vue'
import QuorumCredential from '@/components/public/QuorumCredential.vue'
import QuorumTally from '@/components/public/QuorumTally.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import type { BallotResult, Manifest, VoteTally } from '@/quorum'
import { resultLabels, resultTones, type WitnessStatus } from '@/quorum-results'
import { readQuorumReceipt } from '@/quorum-voter'

interface BallotSummary {
  id: string
  title: string
  opens_at: string
  closes_at: string
  status: 'scheduled' | 'open' | 'completed'
  rule: Manifest['rule']
  vote_history?: Manifest['vote_history']
  result: BallotResult | null
  live_tally: VoteTally | null
  witnesses: WitnessStatus[]
}

const route = useRoute()
const router = useRouter()
const ballots = ref<BallotSummary[]>([])
const submittedBallots = ref(new Set<string>())
const enrollmentAvailable = ref(false)
const loading = ref(true)
const error = ref('')
const dialog = ref<HTMLDialogElement>()
const managingCredential = ref(false)
let refreshTimer: ReturnType<typeof setTimeout> | undefined
let disposed = false
const sections = computed(() => [
  { id: 'active', title: 'Active votes', ballots: ballots.value.filter(ballot => ballot.status !== 'completed'), empty: 'No active votes.' },
  { id: 'completed', title: 'Completed votes', ballots: ballots.value.filter(ballot => ballot.status === 'completed'), empty: 'No completed votes yet.' },
])
const dates = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

function ballotAction(ballot: BallotSummary) {
  if (ballot.status === 'completed') return 'View results'
  if (ballot.status === 'open' && !submittedBallots.value.has(ballot.id)) return 'Vote'
  return 'View ballot'
}

function openCredential() {
  if (!dialog.value || dialog.value.open) return
  managingCredential.value = true
  dialog.value.showModal()
}

async function load() {
  loading.value = ballots.value.length === 0
  error.value = ''
  try {
    const directory = await api.get<{ ballots: BallotSummary[]; eligibility_check_available: boolean }>('/api/quorum')
    if (disposed) return
    submittedBallots.value = new Set(directory.ballots
      .filter(ballot => ballot.status === 'open' && readQuorumReceipt(ballot.id))
      .map(ballot => ballot.id))
    ballots.value = directory.ballots
    enrollmentAvailable.value = directory.eligibility_check_available
    if (enrollmentAvailable.value && route.query.credential === '1') {
      openCredential()
      const query = { ...route.query }
      delete query.credential
      await router.replace({ query })
    }
  } catch (cause) {
    if (!ballots.value.length) error.value = cause instanceof Error ? cause.message : 'Could not load Quorum.'
  } finally {
    loading.value = false
    if (!disposed && ballots.value.some(ballot => ballot.status !== 'completed')) refreshTimer = setTimeout(load, 15000)
  }
}
onMounted(load)
onBeforeUnmount(() => {
  disposed = true
  clearTimeout(refreshTimer)
})
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
              <StatusBadge v-if="ballot.result && ballot.vote_history" tone="neutral">Check witnessed result</StatusBadge>
              <StatusBadge v-else-if="ballot.result" :tone="resultTones[ballot.result.outcome]">
                {{ resultLabels[ballot.result.outcome] }}
              </StatusBadge>
              <StatusBadge v-else-if="submittedBallots.has(ballot.id)" tone="info">Submitted</StatusBadge>
              <StatusBadge v-else :tone="ballot.status === 'open' ? 'success' : 'neutral'" :dot="ballot.status === 'open'">
                {{ ballot.status === 'open' ? 'Open' : ballot.status === 'scheduled' ? 'Scheduled' : 'Completed' }}
              </StatusBadge>
            </header>
            <QuorumTally v-if="ballot.result" :result="ballot.result" />
            <QuorumTally v-else-if="ballot.live_tally" :result="ballot.live_tally" :poll="ballot.rule === 'report_only'" live />
            <footer class="ballot-card__footer">
              <div class="ballot-card__meta muted">
                <time v-if="!ballot.result || ballot.result.total !== ballot.result.electorate"
                  :datetime="ballot.status === 'scheduled' ? ballot.opens_at : ballot.closes_at">
                  {{ ballot.status === 'scheduled' ? 'Opens' : ballot.status === 'open' ? 'Closes' : 'Closed' }}
                  {{ dates.format(new Date(ballot.status === 'scheduled' ? ballot.opens_at : ballot.closes_at)) }}
                </time>
                <div v-if="ballot.result" class="ballot-card__turnout">
                  <span>{{ ballot.result.total }} of {{ ballot.result.electorate }} voted</span>
                  <StatusBadge v-if="ballot.witnesses?.some(witness => witness.signed_at)" tone="neutral">
                    Witness reports available
                  </StatusBadge>
                </div>
              </div>
              <BaseButton size="small" :variant="ballotAction(ballot) === 'Vote' ? 'primary' : 'secondary'"
                :to="{ name: 'quorum-ballot', params: { id: ballot.id } }"
                :aria-label="ballotAction(ballot) + ' · ' + ballot.title">
                {{ ballotAction(ballot) }}
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

.ballot-grid {
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 31rem), 1fr));
  margin: 0;
  padding: 0;
  list-style: none;
}
.ballot-card { display: flex; flex-direction: column; gap: var(--space-4); min-width: 0; }
.ballot-card__header { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-3); }
.ballot-card__header > :last-child { flex-shrink: 0; }
.ballot-card__footer { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--space-2) var(--space-3); }
.ballot-card h3 { margin: 0; min-width: 0; font-size: 1.05rem; overflow-wrap: anywhere; }
.ballot-card__footer { margin-top: auto; }
.ballot-card__meta { display: grid; gap: var(--space-1); font-size: .875rem; }

.ballot-card__turnout { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); }

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
