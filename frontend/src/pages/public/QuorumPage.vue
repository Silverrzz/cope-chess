<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api/client'
import ContentState from '@/components/public/ContentState.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import QuorumCredential from '@/components/public/QuorumCredential.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { verifyFounding, type Founding } from '@/quorum'
import { downloadQuorumFile, genesisStorage } from '@/quorum-voter'

interface BallotSummary {
  id: string
  title: string
  closes_at: string
  status: 'scheduled' | 'open' | 'awaiting_witnesses' | 'completed'
  result: { total: number; electorate: number; outcome: string } | null
}
type FoundingState = { genesis: Founding['genesis'] | null; signatures: Founding['signatures'] }

const ballots = ref<BallotSummary[]>([])
const founding = ref<FoundingState>()
const enrollmentAvailable = ref(false)
const fingerprint = ref(localStorage.getItem(genesisStorage) ?? '')
const rosterError = ref('')
const rosterNotice = ref('')
const verifying = ref(false)
const loading = ref(true)
const error = ref('')
const dates = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
async function load() {
  loading.value = true
  error.value = ''
  try {
    const [directory, state] = await Promise.all([
      api.get<{ ballots: BallotSummary[]; eligibility_check_available: boolean }>('/api/quorum'),
      api.get<FoundingState>('/api/quorum/founding'),
    ])
    ballots.value = directory.ballots
    enrollmentAvailable.value = directory.eligibility_check_available && !state.genesis
    founding.value = state
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not load Quorum.'
  } finally {
    loading.value = false
  }
}
async function verifyRoster() {
  const state = founding.value
  if (!state?.genesis) return
  verifying.value = true
  rosterError.value = ''
  rosterNotice.value = ''
  try {
    const trusted = fingerprint.value.trim().toLowerCase()
    await verifyFounding({ genesis: state.genesis, signatures: state.signatures }, trusted)
    localStorage.setItem(genesisStorage, trusted)
    rosterNotice.value = 'Verified the founding roster of ' + state.genesis.members.length + ' credentials.'
  } catch (cause) {
    rosterError.value = cause instanceof Error ? cause.message : 'Could not verify the roster.'
  } finally {
    verifying.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-container page-stack">
    <PageHeader title="Quorum" description="Community decisions on engines and voting membership." />
    <section v-if="enrollmentAvailable && !loading && !error" class="panel panel__body section-stack">
      <h2>Enroll to vote</h2>
      <p class="muted">Enroll once before the voter list is finalized. Discord checks eligibility, and your browser keeps your private voting credential.</p>
      <QuorumCredential />
    </section>
    <ContentState v-if="loading" kind="loading" compact title="Loading ballots" />
    <ContentState v-else-if="error" kind="error" compact :message="error" action-label="Try again" @action="load" />
    <div v-else class="content-grid">
      <section v-for="section in ['open', 'completed'] as const" :key="section" class="panel panel__body section-stack">
        <h2>{{ section === 'open' ? 'Active votes' : 'Completed votes' }}</h2>
        <template v-if="ballots.some(ballot => (ballot.status === 'completed') === (section === 'completed'))">
          <article v-for="ballot in ballots.filter(item => (item.status === 'completed') === (section === 'completed'))" :key="ballot.id">
            <h3><RouterLink :to="{ name: 'quorum-ballot', params: { id: ballot.id } }">{{ ballot.title }}</RouterLink></h3>
            <p class="muted">
              {{ ballot.status === 'scheduled' ? 'Scheduled · Closes' : ballot.status === 'open' ? 'Closes' : 'Closed' }}
              {{ dates.format(new Date(ballot.closes_at)) }}
              <template v-if="ballot.result"> · {{ ballot.result.total }} of {{ ballot.result.electorate }} voted</template>
              <template v-if="ballot.status === 'awaiting_witnesses'"> · Awaiting result signatures</template>
            </p>
          </article>
        </template>
        <p v-else class="muted">{{ section === 'open' ? 'No active votes.' : 'No completed votes yet.' }}</p>
      </section>
    </div>
    <details v-if="founding?.genesis && !loading && !error" class="panel panel__body">
      <summary>Verify the founding roster</summary>
      <form class="section-stack" @submit.prevent="verifyRoster">
        <p class="muted">Use the fingerprint shared by the independent witnesses.</p>
        <label class="field"><span>Trusted founding fingerprint</span>
          <input v-model="fingerprint" class="input input--mono" maxlength="64" autocomplete="off" spellcheck="false" required
            @input="rosterNotice = ''; rosterError = ''">
        </label>
        <div class="button-row">
          <BaseButton type="submit" :loading="verifying">Verify roster</BaseButton>
          <BaseButton @click="downloadQuorumFile('quorum-founding.json', JSON.stringify(founding))">Download founding evidence</BaseButton>
        </div>
        <InlineFeedback :message="rosterError" />
        <InlineFeedback :message="rosterNotice" tone="info" />
      </form>
    </details>
  </div>
</template>

<style scoped>
summary { cursor: pointer; }
details[open] > summary { margin-bottom: var(--space-4); }
</style>
