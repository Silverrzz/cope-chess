<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import api from '@/api/client'
import ContentState from '@/components/public/ContentState.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import QuorumCredential from '@/components/public/QuorumCredential.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { ballotOptions, ballotScope, verifyBallot, type BallotPackage } from '@/quorum'
import { resultLabels } from '@/quorum-results'
import { downloadQuorumFile, generateQuorumProof, unlockedCredential } from '@/quorum-voter'

const route = useRoute()
const packet = ref<BallotPackage>()
const choice = ref('')
const verified = ref(false)
const verifying = ref(false)
const loading = ref(true)
const busy = ref(false)
const loadError = ref('')
const error = ref('')
let refreshTimer: ReturnType<typeof setTimeout> | undefined
const receipt = ref('')
const manifest = computed(() => packet.value?.manifest)
const endpoint = computed(() => '/api/quorum/ballots/' + encodeURIComponent(String(route.params.id)))
const open = computed(() => packet.value?.status === 'open')
const credential = computed(() => {
  const unlocked = unlockedCredential.value
  return unlocked && packet.value?.roster.members.includes(unlocked.commitment) ? unlocked : null
})
const receiptCovered = computed(() => Boolean(receipt.value && verified.value && packet.value?.votes
  .some(proof => proof.nullifier === receipt.value)))
const dates = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

async function load() {
  loadError.value = ''
  const path = endpoint.value
  try {
    const next = await api.get<BallotPackage>(path)
    if (path !== endpoint.value) return
    verified.value = false
    packet.value = next
    clearTimeout(refreshTimer)
    if (next.status !== 'completed') {
      const boundary = next.status === 'scheduled' ? next.manifest.opens_at : next.manifest.closes_at
      refreshTimer = setTimeout(load, Math.min(2147483647, Math.max(1000, Date.parse(boundary) - Date.now() + 250)))
    }
  } catch (cause) {
    if (path === endpoint.value) loadError.value = (cause as Error).message
  } finally {
    if (path === endpoint.value) loading.value = false
  }
}

watch(endpoint, () => {
  clearTimeout(refreshTimer)
  packet.value = undefined
  choice.value = ''
  receipt.value = localStorage.getItem('cope-quorum-receipt:' + route.params.id) ?? ''
  loading.value = true
  void load()
}, { immediate: true })
onBeforeUnmount(() => clearTimeout(refreshTimer))

async function audit() {
  const evidence = packet.value
  if (!evidence?.result) return
  verifying.value = true
  error.value = ''
  verified.value = false
  try {
    await verifyBallot(evidence)
    if (packet.value === evidence) verified.value = true
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    verifying.value = false
  }
}

async function cast() {
  const evidence = packet.value
  const path = endpoint.value
  if (busy.value || !evidence || !credential.value || !open.value || choice.value === '') return
  busy.value = true
  error.value = ''
  try {
    const proof = await generateQuorumProof(credential.value, evidence.roster.members, choice.value, await ballotScope(evidence.manifest))
    const response = await fetch(path + '/votes', {
      method: 'POST', credentials: 'omit', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(proof),
    })
    const result = await response.json()
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : result.detail?.message ?? 'Could not submit the vote.')
    localStorage.setItem('cope-quorum-receipt:' + evidence.manifest.id, result.nullifier)
    if (path === endpoint.value) {
      receipt.value = result.nullifier
    }
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page-container page-container--narrow page-stack">
    <ContentState v-if="loading" kind="loading" compact title="Loading ballot" />
    <ContentState v-else-if="!manifest || !packet" kind="error" compact :message="loadError" action-label="Try again" @action="load" />
    <template v-else>
      <PageHeader :title="manifest.title">
        <template #actions><BaseButton :to="{ name: 'quorum' }" size="small" variant="ghost">All votes</BaseButton></template>
      </PageHeader>
      <section class="panel panel__body section-stack">
        <div class="toolbar toolbar--between">
          <StatusBadge :tone="open ? 'success' : 'neutral'" :dot="open">{{ open ? 'Open' : packet.status === 'scheduled' ? 'Scheduled' : 'Completed' }}</StatusBadge>
          <span class="muted">{{ packet.status === 'scheduled' ? 'Opens' : 'Closes' }} {{ dates.format(new Date(packet.status === 'scheduled' ? manifest.opens_at : manifest.closes_at)) }}</span></div>
        <p class="quorum-rationale">{{ manifest.rationale }}</p>
      </section>

      <section v-if="packet.result" class="panel panel__body section-stack">
        <h2>Results</h2>
        <p v-if="packet.result.outcome !== 'report_only'">{{ resultLabels[packet.result.outcome] }}</p>
        <ul class="section-stack">
          <li v-for="(option, index) in packet.result.options" :key="option">{{ option }} <strong>{{ packet.result.counts[index] }}</strong></li>
        </ul>
        <p class="muted">{{ packet.result.total }} of {{ packet.result.electorate }} eligible voters participated.</p>
        <div class="button-row">
          <BaseButton :loading="verifying" @click="audit">Verify vote proofs</BaseButton>
          <BaseButton @click="downloadQuorumFile('quorum-' + manifest.id + '.json', JSON.stringify(packet))">Download evidence</BaseButton>
        </div>
        <InlineFeedback v-if="verified" tone="info" message="The vote proofs match the published tally." />
      </section>

      <section v-if="receipt" class="panel panel__body section-stack">
        <h2>Your vote</h2>
        <p>{{ receiptCovered ? 'Your vote is included in the verified record.' : 'Vote submitted. After voting closes, verify the vote proofs to confirm inclusion.' }}</p>
      </section>
      <section v-else-if="open" class="panel panel__body section-stack">
        <h2>Cast a vote</h2>
        <QuorumCredential v-if="!credential" :members="packet.roster.members" />
        <form v-else class="section-stack" @submit.prevent="cast">
          <label class="field"><span>Your choice</span><select v-model="choice" class="select" required :disabled="busy">
            <option value="" disabled>Select an option</option>
            <option v-for="(option, index) in ballotOptions(manifest)" :key="option" :value="String(index)">{{ option }}</option>
          </select></label>
          <div class="button-row"><BaseButton type="submit" variant="primary" :loading="busy">Cast vote</BaseButton></div>
        </form>
      </section>
      <InlineFeedback :message="error || loadError" />
    </template>
  </div>
</template>

<style scoped>
.quorum-rationale { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
