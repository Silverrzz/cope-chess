<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import api from '@/api/client'
import ContentState from '@/components/public/ContentState.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import QuorumCredential from '@/components/public/QuorumCredential.vue'
import QuorumDisclosure from '@/components/public/QuorumDisclosure.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { ballotOptions, ballotScope, verifyBallot, type BallotPackage, type VoteEvidence } from '@/quorum'
import { resultLabels, witnessReportUrl, type WitnessStatus } from '@/quorum-results'
import { downloadQuorumFile, generateQuorumProof, unlockedCredential } from '@/quorum-voter'

interface Publications {
  certified: boolean
  witnesses: WitnessStatus[]
  disclosures: { username: string; option: string }[]
}

const route = useRoute()
const packet = ref<BallotPackage>()
const publications = ref<Publications>()
const publicationError = ref('')
const choice = ref('')
const verified = ref(false)
const verifying = ref(false)
const loading = ref(true)
const busy = ref(false)
const loadError = ref('')
const error = ref('')
let refreshTimer: ReturnType<typeof setTimeout> | undefined
let publicationTimer: ReturnType<typeof setTimeout> | undefined
const receipt = ref('')
const manifest = computed(() => packet.value?.manifest)
const endpoint = computed(() => '/api/quorum/ballots/' + encodeURIComponent(String(route.params.id)))
const signedCount = computed(() => publications.value?.witnesses.filter(witness => witness.signed_at).length ?? 0)
const open = computed(() => packet.value?.status === 'open')
const credential = computed(() => {
  const unlocked = unlockedCredential.value
  return unlocked && packet.value?.roster.members.includes(unlocked.commitment) ? unlocked : null
})
const myProof = computed(() => receipt.value && packet.value?.result
  ? packet.value.votes.find(proof => proof.nullifier === receipt.value)
  : undefined)
const receiptCovered = computed(() => verified.value && Boolean(myProof.value))

function downloadVoteProof() {
  if (!packet.value || !myProof.value) return
  const { manifest, roster } = packet.value
  const evidence: VoteEvidence = { version: 'cope-quorum-vote/1', manifest, roster, proof: myProof.value }
  downloadQuorumFile('quorum-vote-' + manifest.id + '.json', JSON.stringify(evidence))
}
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
    if (next.status === 'completed' || next.manifest.witnesses?.length) void loadPublications()
  } catch (cause) {
    if (path === endpoint.value) loadError.value = (cause as Error).message
  } finally {
    if (path === endpoint.value) loading.value = false
  }
}

async function loadPublications() {
  clearTimeout(publicationTimer)
  const path = endpoint.value
  try {
    const data = await api.get<Publications>(path + '/publications')
    if (path !== endpoint.value) return
    publications.value = data
    publicationError.value = ''
    if (packet.value?.result && data.witnesses.length && !data.certified) publicationTimer = setTimeout(loadPublications, 30000)
  } catch (cause) {
    if (path === endpoint.value) publicationError.value = (cause as Error).message
  }
}

watch(endpoint, () => {
  clearTimeout(refreshTimer)
  clearTimeout(publicationTimer)
  packet.value = undefined
  publications.value = undefined
  publicationError.value = ''
  choice.value = ''
  receipt.value = localStorage.getItem('cope-quorum-receipt:' + route.params.id) ?? ''
  loading.value = true
  void load()
}, { immediate: true })
onBeforeUnmount(() => { clearTimeout(refreshTimer); clearTimeout(publicationTimer) })

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
          <BaseButton @click="downloadQuorumFile('quorum-' + manifest.id + '.json', JSON.stringify(packet))">Download ballot evidence</BaseButton>
        </div>
        <InlineFeedback v-if="verified" tone="info" message="The vote proofs match the published tally." />
      </section>

      <section v-if="publications?.witnesses.length" class="panel panel__body section-stack">
        <div class="toolbar toolbar--between">
          <h2>Witness certification</h2>
          <StatusBadge :tone="publications.certified ? 'success' : 'neutral'">
            {{ publications.certified ? 'Certified' : packet.result ? signedCount + ' of ' + publications.witnesses.length + ' signed' : 'Signs after closing' }}
          </StatusBadge>
        </div>
        <ul class="section-stack">
          <li v-for="witness in publications.witnesses" :key="witness.public_key">
            <strong>{{ witness.name }}</strong> ·
            <a v-if="witness.signed_at" :href="witnessReportUrl(manifest.id, witness)" target="_blank" rel="noopener noreferrer">Signed report</a>
            <span v-else class="muted">{{ packet.result ? 'Awaiting signature' : 'Assigned' }}</span>
          </li>
        </ul>
      </section>

      <section v-if="packet.result" class="panel panel__body section-stack">
        <h2>Public votes</h2>
        <ul v-if="publications?.disclosures.length" class="section-stack">
          <li v-for="vote in publications.disclosures" :key="vote.username">@{{ vote.username }} · <strong>{{ vote.option }}</strong></li>
        </ul>
        <QuorumDisclosure :key="manifest.id" :ballot="packet" @published="loadPublications" />
        <InlineFeedback :message="publicationError" />
      </section>

      <section v-if="receipt" class="panel panel__body section-stack">
        <h2>Your vote</h2>
        <p>{{ receiptCovered ? 'Your vote is included in the verified record.' : 'Vote submitted.' }}</p>
        <div v-if="myProof" class="button-row">
          <BaseButton @click="downloadVoteProof">Download my vote proof</BaseButton>
        </div>
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
