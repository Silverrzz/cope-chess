<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import api from '@/api/client'
import ContentState from '@/components/public/ContentState.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import QuorumCredential from '@/components/public/QuorumCredential.vue'
import QuorumDisclosure from '@/components/public/QuorumDisclosure.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import QuorumTally from '@/components/public/QuorumTally.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { ballotOptions, ballotScope, verifyBallot, type BallotPackage, type VoteEvidence } from '@/quorum'
import { resultLabels, resultTones, witnessReportUrl, type WitnessStatus } from '@/quorum-results'
import { downloadQuorumFile, generateQuorumProof, quorumReceiptKey, readQuorumReceipt, unlockedCredential } from '@/quorum-voter'

interface Publications {
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
let loadSequence = 0
let publicationSequence = 0
let disposed = false
const receipt = ref('')
const manifest = computed(() => packet.value?.manifest)
const endpoint = computed(() => '/api/quorum/ballots/' + encodeURIComponent(String(route.params.id)))
const signedCount = computed(() => publications.value?.witnesses.filter(witness => witness.signed_at).length ?? 0)
const open = computed(() => packet.value?.status === 'open')
const fullTurnout = computed(() => {
  const result = packet.value?.result
  return Boolean(result && result.total === result.electorate)
})
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
  clearTimeout(refreshTimer)
  const sequence = ++loadSequence
  const path = endpoint.value
  try {
    const next = await api.get<BallotPackage>(path)
    if (disposed || sequence !== loadSequence) return
    const previousStatus = packet.value?.status
    verified.value = false
    packet.value = next
    if (next.status !== 'completed') {
      const boundary = next.status === 'scheduled' ? next.manifest.opens_at : next.manifest.closes_at
      refreshTimer = setTimeout(load, Math.min(15000, Math.max(1000, Date.parse(boundary) - Date.now() + 250)))
    }
    if (previousStatus !== next.status && (next.status === 'completed' || next.manifest.witnesses?.length)) void loadPublications()
  } catch (cause) {
    if (disposed || sequence !== loadSequence) return
    if (packet.value) refreshTimer = setTimeout(load, 15000)
    else loadError.value = (cause as Error).message
  } finally {
    if (!disposed && sequence === loadSequence) loading.value = false
  }
}

async function loadPublications() {
  clearTimeout(publicationTimer)
  const sequence = ++publicationSequence
  const path = endpoint.value
  try {
    const data = await api.get<Publications>(path + '/publications')
    if (disposed || path !== endpoint.value || sequence !== publicationSequence) return
    publications.value = data
    publicationError.value = ''
    if (packet.value?.result && data.witnesses.length && data.witnesses.some(witness => !witness.signed_at)) publicationTimer = setTimeout(loadPublications, 30000)
  } catch (cause) {
    if (!disposed && path === endpoint.value && sequence === publicationSequence) publicationError.value = (cause as Error).message
  }
}

watch(endpoint, () => {
  clearTimeout(refreshTimer)
  clearTimeout(publicationTimer)
  packet.value = undefined
  publications.value = undefined
  publicationError.value = ''
  choice.value = ''
  receipt.value = readQuorumReceipt(String(route.params.id))
  loading.value = true
  void load()
}, { immediate: true })
onBeforeUnmount(() => {
  disposed = true
  clearTimeout(refreshTimer)
  clearTimeout(publicationTimer)
})

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
    localStorage.setItem(quorumReceiptKey(evidence.manifest.id), result.nullifier)
    if (!disposed && path === endpoint.value) {
      receipt.value = result.nullifier
      void load()
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
    <article v-else class="panel ballot-sheet" aria-labelledby="quorum-ballot-title">
      <header class="ballot-sheet__section ballot-sheet__heading">
        <div class="ballot-sheet__toolbar">
          <div class="ballot-sheet__meta">
            <StatusBadge :tone="open ? 'success' : 'neutral'" :dot="open">
              {{ open ? 'Open' : packet.status === 'scheduled' ? 'Scheduled' : 'Completed' }}
            </StatusBadge>
            <span v-if="fullTurnout" class="muted">All voters voted</span>
            <time v-else :datetime="packet.status === 'scheduled' ? manifest.opens_at : manifest.closes_at" class="muted">
              {{ packet.status === 'scheduled' ? 'Opens' : packet.result ? 'Closed' : 'Closes' }}
              {{ dates.format(new Date(packet.status === 'scheduled' ? manifest.opens_at : manifest.closes_at)) }}
            </time>
          </div>
          <BaseButton :to="{ name: 'quorum' }" size="small" variant="ghost">All votes</BaseButton>
        </div>
        <h1 id="quorum-ballot-title">{{ manifest.title }}</h1>
        <p class="ballot-sheet__rationale">{{ manifest.rationale }}</p>
      </header>

      <div v-if="open && packet.live_tally" class="ballot-sheet__section">
        <QuorumTally :result="packet.live_tally" :poll="manifest.rule === 'report_only'" live />
      </div>

      <section v-if="packet.result" class="ballot-sheet__section section-stack" aria-labelledby="quorum-results-title">
        <div class="ballot-sheet__toolbar">
          <h2 id="quorum-results-title">Results</h2>
          <StatusBadge v-if="packet.result.outcome !== 'report_only'" :tone="resultTones[packet.result.outcome]">
            {{ resultLabels[packet.result.outcome] }}
          </StatusBadge>
        </div>
        <QuorumTally :result="packet.result" />
        <p class="muted">{{ packet.result.total }} of {{ packet.result.electorate }} eligible voters participated.</p>
        <div class="button-row">
          <BaseButton size="small" :loading="verifying" @click="audit">Verify vote proofs</BaseButton>
          <BaseButton size="small" variant="ghost" @click="downloadQuorumFile('quorum-' + manifest.id + '.json', JSON.stringify(packet))">Download ballot evidence</BaseButton>
          <BaseButton v-if="myProof" size="small" variant="ghost" @click="downloadVoteProof">Download my vote proof</BaseButton>
        </div>
        <InlineFeedback v-if="verified" tone="info" :message="receiptCovered ? 'The tally matches the vote proofs and includes your vote.' : 'The vote proofs match the published tally.'" />
        <p class="muted">Proof checks cover the votes supplied with the ballot. They cannot establish that the roster was authorized or every submitted vote was included.</p>
        <InlineFeedback :message="error" />
      </section>

      <section v-else-if="receipt" class="ballot-sheet__section ballot-sheet__receipt" role="status">
        <AppIcon name="check-circle" :size="22" />
        <div><strong>Vote submitted</strong><p class="muted">After closing, check whether COPE included your vote in the result.</p></div>
      </section>

      <section v-else-if="open" class="ballot-sheet__section section-stack" aria-labelledby="quorum-cast-title">
        <h2 id="quorum-cast-title">Cast a vote</h2>
        <p v-if="credential" class="muted">This browser uses voting code supplied by COPE. That code can access your unlocked credential.</p>
        <QuorumCredential v-if="!credential" :members="packet.roster.members" />
        <form v-else class="section-stack" @submit.prevent="cast">
          <fieldset class="ballot-choices">
            <legend class="sr-only">Your choice</legend>
            <label v-for="(option, index) in ballotOptions(manifest)" :key="option" class="ballot-choice"
              :class="{ 'ballot-choice--selected': choice === String(index) }">
              <input v-model="choice" name="quorum-choice" type="radio" :value="String(index)" :disabled="busy" required>
              <span>{{ option }}</span>
            </label>
          </fieldset>
          <div class="button-row">
            <BaseButton type="submit" variant="primary" :disabled="choice === ''" :loading="busy">Cast vote</BaseButton>
          </div>
        </form>
        <InlineFeedback :message="error" />
      </section>

      <section v-if="publications?.witnesses.length" class="ballot-sheet__section ballot-sheet__details" aria-labelledby="quorum-witnesses-title">
        <div class="ballot-sheet__toolbar">
          <h2 id="quorum-witnesses-title">Witnesses</h2>
          <StatusBadge tone="neutral">
            {{ packet.result ? signedCount + ' of ' + publications.witnesses.length + ' reports available' : 'Sign after closing' }}
          </StatusBadge>
        </div>
        <ul class="ballot-sheet__list">
          <li v-for="witness in publications.witnesses" :key="witness.public_key">
            <strong>{{ witness.name }}</strong>
            <a v-if="witness.signed_at" :href="witnessReportUrl(manifest.id, witness)" target="_blank" rel="noopener noreferrer">Signed report</a>
            <span v-else class="muted">{{ packet.result ? 'Awaiting signature' : 'Assigned' }}</span>
          </li>
        </ul>
      </section>

      <section v-if="packet.result" class="ballot-sheet__section ballot-sheet__details" aria-labelledby="quorum-public-votes-title">
        <h2 id="quorum-public-votes-title">Public votes</h2>
        <ul v-if="publications?.disclosures.length" class="ballot-sheet__list">
          <li v-for="vote in publications.disclosures" :key="vote.username">
            <span>@{{ vote.username }}</span><strong>{{ vote.option }}</strong>
          </li>
        </ul>
        <QuorumDisclosure :key="manifest.id" :ballot="packet" @published="loadPublications" />
      </section>
      <div v-if="loadError || publicationError" class="ballot-sheet__section">
        <InlineFeedback :message="loadError || publicationError" />
      </div>
    </article>
  </div>
</template>

<style scoped>
.ballot-sheet__section { padding: clamp(var(--space-4), 3vw, var(--space-8)); }
.ballot-sheet__section + .ballot-sheet__section { border-top: 1px solid var(--color-border); }
.ballot-sheet__heading { display: grid; gap: var(--space-4); }
.ballot-sheet__toolbar, .ballot-sheet__meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-3);
}
.ballot-sheet__toolbar { justify-content: space-between; }
.ballot-sheet__meta { font-size: .875rem; }
.ballot-sheet h1, .ballot-sheet h2, .ballot-sheet p { margin: 0; }
.ballot-sheet h1 { font-size: clamp(1.75rem, 3.25vw, 2.5rem); text-wrap: pretty; overflow-wrap: anywhere; }
.ballot-sheet h2 { font-size: 1.1875rem; }
.ballot-sheet__rationale { white-space: pre-wrap; overflow-wrap: anywhere; }
.ballot-sheet__receipt { display: flex; align-items: center; gap: var(--space-3); }
.ballot-sheet__receipt > svg { color: var(--color-success); flex: none; }
.ballot-sheet__details { display: grid; gap: var(--space-3); }
.ballot-sheet__list { margin: 0; padding: 0; list-style: none; }
.ballot-sheet__list li { display: flex; flex-wrap: wrap; justify-content: space-between; gap: var(--space-2) var(--space-3); overflow-wrap: anywhere; }
.ballot-sheet__list li + li { margin-top: var(--space-2); }

.ballot-choices { display: grid; gap: var(--space-2); margin: 0; padding: 0; border: 0; min-width: 0; }
.ballot-choice {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: var(--control-height);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  overflow-wrap: anywhere;
}
.ballot-choice:not(:has(:disabled)):hover { border-color: var(--color-accent); }
.ballot-choice--selected { border-color: var(--color-accent); background: var(--color-accent-soft); font-weight: 650; }
.ballot-choice input { margin: 0; flex: none; accent-color: var(--color-accent); }
.ballot-choice:focus-within { outline: 2px solid var(--color-accent); outline-offset: 2px; }
</style>
