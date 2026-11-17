<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api/client'
import ContentState from '@/components/public/ContentState.vue'
import QuorumCredential from '@/components/public/QuorumCredential.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

interface BallotSummary {
  id: string
  title: string
  closes_at: string
  status: 'scheduled' | 'open' | 'completed'
  result: { total: number; electorate: number; outcome: string } | null
}

const ballots = ref<BallotSummary[]>([])
const enrollmentAvailable = ref(false)
const loading = ref(true)
const error = ref('')
const dates = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
async function load() {
  loading.value = true
  error.value = ''
  try {
    const directory = await api.get<{ ballots: BallotSummary[]; eligibility_check_available: boolean }>('/api/quorum')
    ballots.value = directory.ballots
    enrollmentAvailable.value = directory.eligibility_check_available
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
    <PageHeader title="Quorum" description="Community decisions on engines and voting membership." />
    <section v-if="enrollmentAvailable && !loading && !error" class="panel panel__body section-stack">
      <h2>Enroll to vote</h2>
      <p class="muted">Enroll once to vote in future ballots. Discord checks eligibility, and your browser keeps your private voting credential.</p>
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
            </p>
          </article>
        </template>
        <p v-else class="muted">{{ section === 'open' ? 'No active votes.' : 'No completed votes yet.' }}</p>
      </section>
    </div>
  </div>
</template>
