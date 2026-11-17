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

interface BallotSummary {
  id: string
  title: string
  opens_at: string
  closes_at: string
  status: 'scheduled' | 'open' | 'completed'
  result: { total: number; electorate: number } | null
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
      <template v-if="enrollmentAvailable" #actions>
        <BaseButton size="small" aria-haspopup="dialog" @click="openCredential">Voting credential</BaseButton>
      </template>
    </PageHeader>
    <ContentState v-if="loading" kind="loading" compact title="Loading ballots" />
    <ContentState v-else-if="error" kind="error" compact :message="error" action-label="Try again" @action="load" />
    <div v-else class="ballot-sections">
      <section v-for="section in sections" :key="section.id" class="ballot-section" :aria-labelledby="'quorum-' + section.id">
        <h2 :id="'quorum-' + section.id">{{ section.title }}</h2>
        <ul v-if="section.ballots.length" class="panel ballot-list">
          <li v-for="ballot in section.ballots" :key="ballot.id" class="ballot-row">
            <div class="ballot-row__title">
              <h3>{{ ballot.title }}</h3>
              <StatusBadge v-if="ballot.status !== 'completed'" :tone="ballot.status === 'open' ? 'success' : 'neutral'" :dot="ballot.status === 'open'">
                {{ ballot.status === 'open' ? 'Open' : 'Scheduled' }}
              </StatusBadge>
            </div>
            <div class="ballot-row__detail muted">
              <time :datetime="ballot.status === 'scheduled' ? ballot.opens_at : ballot.closes_at">
                {{ ballot.status === 'scheduled' ? 'Opens' : ballot.status === 'open' ? 'Closes' : 'Closed' }}
                {{ dates.format(new Date(ballot.status === 'scheduled' ? ballot.opens_at : ballot.closes_at)) }}
              </time>
              <span v-if="ballot.result">{{ ballot.result.total }} of {{ ballot.result.electorate }} voted</span>
            </div>
            <BaseButton size="small" :variant="ballot.status === 'open' ? 'primary' : 'secondary'"
              :to="{ name: 'quorum-ballot', params: { id: ballot.id } }"
              :aria-label="(ballot.status === 'open' ? 'Vote on ' : 'View ') + ballot.title">
              {{ ballot.status === 'open' ? 'Vote' : ballot.status === 'completed' ? 'View results' : 'View ballot' }}
              <template #trailing><AppIcon name="arrow-right" :size="16" /></template>
            </BaseButton>
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

.ballot-list { margin: 0; padding: 0; list-style: none; }
.ballot-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(13rem, 28%) auto;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-5);
}
.ballot-row + .ballot-row { border-top: 1px solid var(--color-border); }
.ballot-row__title { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); }
.ballot-row__title h3 { margin: 0; font-size: 1rem; overflow-wrap: anywhere; }
.ballot-row__detail { display: grid; gap: var(--space-1); font-size: .875rem; }
.ballot-row > .base-button { justify-self: end; }

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

@media (max-width: 38rem) {
  .ballot-row { grid-template-columns: minmax(0, 1fr) auto; gap: var(--space-2) var(--space-3); padding: var(--space-4); }
  .ballot-row__title { grid-column: 1 / -1; }
}
</style>
