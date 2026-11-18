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
import { ballotOptions, ballotScope, fingerprint, verifyBallot, type BallotPackage, type VoteEvidence } from '@/quorum'
import { authorizedBallot, verifyHistoryResult, verifyRecordedReceipt, type PolicyChain, type FinalEvidence, type RecordedReceipt, type VoteHistory } from '@/quorum-history'
import { extendPinnedPolicy, loadPinnedPolicy, onPinChange } from '@/quorum-pins'
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
const savedProof = ref<VoteEvidence['proof']>()
const recordedReceipt = ref<RecordedReceipt>()
const history = ref<VoteHistory>()
const historyVerified = ref(false)
const historyError = ref('')
const pinnedChain = ref<PolicyChain>()
const pinnedPolicy = computed(() => {
  const manifest = packet.value?.manifest
  return manifest && pinnedChain.value?.ballots.has(manifest.id)
    ? pinnedChain.value.epochs.get(manifest.vote_history?.epoch ?? '') : undefined
})
let historySequence = 0
let historyTimer: ReturnType<typeof setTimeout> | undefined
const witnessed = computed(() => Boolean(packet.value?.manifest.vote_history))
const pendingKey = (id: string) => 'cope-quorum-submitted-proof/1/' + id
const recordedKey = (id: string) => 'cope-quorum-recorded-receipt/1/' + id
async function refreshPin() {
  try {
    const chain = await loadPinnedPolicy(location.origin)
    if (!disposed && chain && (!pinnedChain.value || chain.heads.includes(pinnedChain.value.head))) {
      pinnedChain.value = chain
      historyVerified.value = false
      void loadHistory()
    }
  } catch { /* Select an authenticated policy file to enable checks. */ }
}
const stopWatchingPin = onPinChange(location.origin, () => { void refreshPin() })
void refreshPin()

async function importPolicy(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    await extendPinnedPolicy(file, location.origin, true)
    recordedReceipt.value = undefined
    historyVerified.value = false
    await refreshPin()
  } catch (cause) {
    historyError.value = (cause as Error).message
  } finally {
    input.value = ''
  }
}

async function loadHistory() {
  clearTimeout(historyTimer)
  const evidence = packet.value
  const path = endpoint.value
  if (!evidence?.manifest.vote_history) return
  const sequence = ++historySequence
  const current = () => !disposed && sequence === historySequence && path === endpoint.value
  const id = evidence.manifest.id
  historyError.value = ''
  try {
    const received = await api.get<VoteHistory>(path + '/history')
    if (!current()) return
    history.value = received
    const policy = pinnedChain.value ? await authorizedBallot(pinnedChain.value, evidence.manifest) : undefined
    if (policy) {
      const cached = localStorage.getItem(recordedKey(id))
      if (cached && !recordedReceipt.value) {
        const saved = JSON.parse(cached) as RecordedReceipt
        if (await fingerprint(saved.manifest) !== await fingerprint(evidence.manifest)) throw new Error('The ballot changed after this vote was recorded.')
        await verifyRecordedReceipt(saved, policy, location.origin)
        if (!current()) return
        recordedReceipt.value = saved
      }
      if (!recordedReceipt.value && savedProof.value && received.open) {
        const proof = savedProof.value
        const voteHash = await fingerprint(proof)
        const hashes = received.vote_hashes.slice(0, received.open.size)
        if (hashes.includes(voteHash)) {
          const record: RecordedReceipt = {
            version: 'cope-quorum-recorded-vote/1', origin: location.origin, manifest: evidence.manifest, roster: evidence.roster,
            vote_hash: voteHash, proof, vote_hashes: hashes, checkpoint: received.open,
          }
          await verifyRecordedReceipt(record, policy, location.origin)
          if (!current()) return
          localStorage.setItem(recordedKey(id), JSON.stringify(record))
          recordedReceipt.value = record
          localStorage.removeItem(pendingKey(id))
          savedProof.value = undefined
        }
      }
    }
  } catch (cause) {
    if (current()) historyError.value = (cause as Error).message
  } finally {
    if (current() && !historyVerified.value) historyTimer = setTimeout(loadHistory, 8000)
  }
}

function downloadRecordedReceipt() {
  const record = recordedReceipt.value
  if (record) downloadQuorumFile('quorum-recorded-' + record.manifest.id + '.json', JSON.stringify(record))
}

function downloadFinalEvidence() {
  const ballot = packet.value
  if (!ballot) return
  const evidence: BallotPackage | FinalEvidence = ballot.manifest.vote_history && history.value ? {
    version: 'cope-quorum-final-evidence/1', origin: location.origin, ballot, history: history.value,
  } : ballot
  downloadQuorumFile('quorum-' + ballot.manifest.id + '.json', JSON.stringify(evidence))
}
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
    const wasSettling = packet.value?.settling
    verified.value = false
    packet.value = next
    if (next.status !== 'completed' || next.settling) {
      const boundary = next.status === 'scheduled' ? next.manifest.opens_at : next.manifest.closes_at
      refreshTimer = setTimeout(load, Math.min(15000, Math.max(1000, Date.parse(boundary) - Date.now() + 250)))
    }
    if (next.manifest.vote_history && previousStatus !== next.status) void loadHistory()
    if ((previousStatus !== next.status || wasSettling !== next.settling)
      && (next.status === 'completed' || next.manifest.witnesses?.length)) void loadPublications()
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
  clearTimeout(historyTimer)
  historySequence++
  history.value = undefined
  historyVerified.value = false
  recordedReceipt.value = undefined
  savedProof.value = undefined
  try {
    const raw = localStorage.getItem(pendingKey(String(route.params.id)))
    if (raw) savedProof.value = JSON.parse(raw) as VoteEvidence['proof']
  } catch { /* Ignore invalid local data. */ }
  packet.value = undefined
  publications.value = undefined
  publicationError.value = ''
  choice.value = ''
  receipt.value = readQuorumReceipt(String(route.params.id))
  loading.value = true
  void load()
}, { immediate: true })
onBeforeUnmount(() => {
  stopWatchingPin()
  disposed = true
  clearTimeout(historyTimer)
  clearTimeout(refreshTimer)
  clearTimeout(publicationTimer)
})

async function audit() {
  const evidence = packet.value
  if (!evidence?.result) return
  verifying.value = true
  error.value = ''
  verified.value = false
  historyVerified.value = false
  try {
    if (evidence.manifest.vote_history && pinnedPolicy.value) {
      const latest = await api.get<VoteHistory>(endpoint.value + '/history')
      await verifyHistoryResult(evidence, latest, await authorizedBallot(pinnedChain.value, evidence.manifest),
        location.origin, recordedReceipt.value)
      if (packet.value === evidence) {
        history.value = latest
        historyVerified.value = true
      }
    } else {
      await verifyBallot(evidence)
    }
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
  if (busy.value || !evidence || evidence.manifest.vote_history || !open.value
    || (!savedProof.value && (!credential.value || choice.value === ''))) return
  busy.value = true
  error.value = ''
  try {
    const unlocked = credential.value
    const proof = savedProof.value ?? (unlocked && await generateQuorumProof(
      unlocked, evidence.roster.members, choice.value, await ballotScope(evidence.manifest)))
    if (!proof) return
    const response = await fetch(path + '/votes', {
      method: 'POST', credentials: 'omit', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(proof),
    })
    const result = await response.json()
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : result.detail?.message ?? 'Could not submit the vote.')
    localStorage.setItem(quorumReceiptKey(evidence.manifest.id), proof.nullifier)
    if (!disposed && path === endpoint.value) {
      receipt.value = proof.nullifier
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
              {{ open ? 'Open' : packet.status === 'scheduled' ? 'Scheduled' : witnessed ? 'Voting closed' : 'Completed' }}
            </StatusBadge>
            <span v-if="fullTurnout && !witnessed" class="muted">All voters voted</span>
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

      <section v-if="witnessed" class="ballot-sheet__section section-stack" aria-labelledby="quorum-history-title">
        <h2 id="quorum-history-title">Witnessed vote history</h2>
        <p class="muted">Use a witness policy file obtained independently of this website. It pins the public keys and threshold your device trusts.</p>
        <label class="field">
          <span>{{ pinnedChain ? 'Import an approved witness policy update' : 'Pin a witness policy' }}</span>
          <input type="file" accept=".json,application/json" @change="importPolicy">
        </label>
        <p v-if="pinnedChain && !pinnedPolicy" class="muted">The pinned policy history does not yet cover this ballot.</p>
        <p v-if="recordedReceipt" role="status">Your vote was recorded by the required witnesses.</p>
        <p v-else-if="receipt || savedProof" role="status">Waiting for a verifiable witness receipt for your vote.</p>
        <div v-if="recordedReceipt" class="button-row">
          <BaseButton variant="ghost" size="small" @click="downloadRecordedReceipt">Download recorded vote receipt</BaseButton>
        </div>
        <InlineFeedback :message="historyError" />
      </section>

      <section v-if="packet.result" class="ballot-sheet__section section-stack" aria-labelledby="quorum-results-title">
        <div class="ballot-sheet__toolbar">
          <h2 id="quorum-results-title">{{ witnessed && !historyVerified ? 'Provisional tally' : 'Results' }}</h2>
          <StatusBadge v-if="packet.result.outcome !== 'report_only'" :tone="resultTones[packet.result.outcome]">
            {{ resultLabels[packet.result.outcome] }}
          </StatusBadge>
        </div>
        <QuorumTally :result="packet.result" />
        <p class="muted">{{ packet.result.total }} of {{ packet.result.electorate }} eligible voters participated.</p>
        <div class="button-row">
          <BaseButton size="small" :loading="verifying" @click="audit">{{ witnessed && pinnedPolicy ? 'Verify witnessed result' : 'Verify vote proofs' }}</BaseButton>
          <BaseButton size="small" variant="ghost" @click="downloadFinalEvidence">Download ballot evidence</BaseButton>
          <BaseButton v-if="myProof" size="small" variant="ghost" @click="downloadVoteProof">Download my vote proof</BaseButton>
        </div>
        <InlineFeedback v-if="historyVerified" tone="info" message="The tally includes all votes in the pinned witnesses’ final history." />
        <InlineFeedback v-else-if="verified" tone="info" :message="receiptCovered ? 'The tally matches the vote proofs and includes your vote.' : 'The vote proofs match the published tally.'" />
        <p v-if="witnessed" class="muted">The result remains provisional until the roster, ballot approval, intake certificates, and vote history are verified against your pinned policy.</p>
        <p v-else class="muted">Proof checks cover the votes supplied with the ballot. They cannot establish that the roster was authorized or every submitted vote was included.</p>
        <InlineFeedback :message="error" />
      </section>

      <section v-else-if="witnessed && open" class="ballot-sheet__section section-stack">
        <h2>Vote through an independent verifier</h2>
        <p class="muted">Open an address you recognize. Its voting page checks the approved ballot and runs without a COPE login. Bring your encrypted voting backup.</p>
        <ul class="ballot-sheet__list">
          <li v-for="witness in manifest.witnesses?.filter(item => item.report_origin)" :key="witness.public_key">
            <a :href="witness.report_origin + '/vote/' + manifest.id" target="_blank" rel="noopener noreferrer">{{ witness.name }}</a>
            <span class="muted">{{ witness.report_origin }}</span>
          </li>
        </ul>
      </section>
      <section v-else-if="packet.settling" class="ballot-sheet__section" role="status">
        <h2>Verifiers are settling the ballot</h2>
        <p class="muted">Voting has closed. The verifiers are forwarding and checking the final submissions.</p>
      </section>
      <section v-else-if="receipt || savedProof || recordedReceipt" class="ballot-sheet__section ballot-sheet__receipt" role="status">
        <AppIcon name="check-circle" :size="22" />
        <div>
          <strong>{{ recordedReceipt ? 'Vote recorded' : receipt ? 'Vote submitted' : 'Submission not confirmed' }}</strong>
          <p v-if="!witnessed" class="muted">After closing, check whether COPE included your vote in the result.</p>
          <InlineFeedback :message="error" />
        </div>
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
            {{ packet.result ? signedCount + ' of ' + publications.witnesses.length + ' reports available' : witnessed ? 'Witnessing vote history' : 'Sign after closing' }}
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

      <section v-if="witnessed" class="ballot-sheet__section section-stack" aria-labelledby="quorum-history-title">
        <h2 id="quorum-history-title">Witnessed vote history</h2>
        <p class="muted">Use a witness policy file obtained independently of this website. It pins the public keys and threshold your device trusts.</p>
        <label class="field">
          <span>{{ pinnedChain ? 'Import an approved witness policy update' : 'Pin a witness policy' }}</span>
          <input type="file" accept=".json,application/json" @change="importPolicy">
        </label>
        <p v-if="pinnedChain && !pinnedPolicy" class="muted">The pinned policy history does not yet cover this ballot.</p>
        <p v-if="recordedReceipt" role="status">Your vote was recorded by the required witnesses.</p>
        <p v-else-if="receipt || savedProof" role="status">Waiting for a verifiable witness receipt for your vote.</p>
        <div v-if="recordedReceipt" class="button-row">
          <BaseButton variant="ghost" size="small" @click="downloadRecordedReceipt">Download recorded vote receipt</BaseButton>
        </div>
        <InlineFeedback :message="historyError" />
      </section>

      <section v-if="packet.result && !witnessed" class="ballot-sheet__section ballot-sheet__details" aria-labelledby="quorum-public-votes-title">
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
