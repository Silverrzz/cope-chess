<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api/client'
import ContentState from '@/components/public/ContentState.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import PageHeader from '@/components/ui/PageHeader.vue'

interface BallotSummary {
  id: string
  title: string
  closes_at: string
  status: 'scheduled' | 'open' | 'awaiting_witnesses' | 'completed'
  result: { total: number; electorate: number; outcome: string } | null
}
const ballots = ref<BallotSummary[]>([])
const loading = ref(true)
const error = ref('')
const dates = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
async function load() {
  loading.value = true
  error.value = ''
  try {
    ballots.value = (await api.get<{ ballots: BallotSummary[] }>('/api/quorum')).ballots
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
    <PageHeader title="Quorum" description="Community decisions on engines and voting membership.">
      <template #actions>
        <BaseButton :to="{ name: 'quorum-tools' }" size="small" variant="ghost">Voter tools</BaseButton>
      </template>
    </PageHeader>
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
  </div>
</template>
