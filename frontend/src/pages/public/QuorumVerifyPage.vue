<script setup lang="ts">
import { ref } from 'vue'
import api from '@/api/client'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import QuorumTally from '@/components/public/QuorumTally.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { fingerprint, verifyEvidence, type BallotPackage, type VoteEvidence } from '@/quorum'
import { authorizedBallot, verifyHistoryResult, verifyRecordedReceipt, verifyRetentionReceipt,
  type FinalEvidence, type RecordedReceipt, type RetentionReceipt } from '@/quorum-history'
import { extendPinnedPolicy, loadPinnedPolicy } from '@/quorum-pins'
import { resultLabels, resultTones } from '@/quorum-results'

const file = ref<File>()
const result = ref<Awaited<ReturnType<typeof verifyEvidence>>>()
const busy = ref(false)
const policyFile = ref<File>()
const witnessed = ref(false)
const recordedTitle = ref('')
const retained = ref(false)
const error = ref('')
const recordCheck = ref('')
const ballotId = ref('')

function selectFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0]
  result.value = undefined
  witnessed.value = false
  recordedTitle.value = ''
  retained.value = false
  error.value = ''
  recordCheck.value = ''
  ballotId.value = ''
}

async function verify() {
  if (!file.value || busy.value) return
  busy.value = true
  result.value = undefined
  witnessed.value = false
  recordedTitle.value = ''
  retained.value = false
  error.value = ''
  recordCheck.value = ''
  ballotId.value = ''
  try {
    if (file.value.size > 16 * 1024 * 1024) throw new Error('Choose a Quorum JSON file up to 16 MiB.')
    let evidence: unknown
    try {
      evidence = JSON.parse(await file.value.text())
    } catch {
      throw new Error('This file contains invalid JSON.')
    }
    const kind = (evidence as { version?: string } | null)?.version
    if (kind === 'cope-quorum-final-evidence/1' || kind === 'cope-quorum-recorded-vote/1' || kind === 'cope-quorum-relayed-vote/1') {
      const supplied = evidence as FinalEvidence | RecordedReceipt | RetentionReceipt
      if (supplied.origin !== location.origin) throw new Error('This file belongs to another COPE origin. Use the offline auditor with your trusted origin.')
      const chain = policyFile.value ? await extendPinnedPolicy(policyFile.value, location.origin) : await loadPinnedPolicy(location.origin)
      if (!chain) throw new Error('Choose a trusted witness policy file to verify witnessed evidence.')
      const manifest = supplied.version === 'cope-quorum-final-evidence/1' ? supplied.ballot.manifest : supplied.manifest
      const policy = await authorizedBallot(chain, manifest)
      if (supplied.version === 'cope-quorum-relayed-vote/1') {
        await verifyRetentionReceipt(supplied, policy, location.origin)
        retained.value = true
        recordedTitle.value = manifest.title
      } else if (supplied.version === 'cope-quorum-recorded-vote/1') {
        await verifyRecordedReceipt(supplied, policy, location.origin)
        recordedTitle.value = manifest.title
      } else {
        await verifyHistoryResult(supplied.ballot, supplied.history, policy, location.origin)
        witnessed.value = true
        result.value = { kind: 'ballot', title: manifest.title, result: supplied.ballot.result! }
      }
      return
    }
    if (kind === 'cope-quorum-evidence/1' && (evidence as BallotPackage).manifest?.vote_history) {
      throw new Error('Choose the full final evidence file with witness checkpoints.')
    }
    let official: BallotPackage | undefined
    const verified = await verifyEvidence(evidence, async id => {
      official = await api.get<BallotPackage>('/api/quorum/ballots/' + id)
      return official
    })
    const fileEvidence = evidence as BallotPackage | VoteEvidence
    result.value = verified
    try {
      official ??= await api.get<BallotPackage>('/api/quorum/ballots/' + fileEvidence.manifest.id)
      ballotId.value = official.manifest.id
      if (official.status !== 'completed') {
        recordCheck.value = 'The COPE ballot is still open.'
      } else if (fileEvidence.version === 'cope-quorum-vote/1') {
        const recorded = official.votes.find(vote => vote.nullifier === fileEvidence.proof.nullifier)
        recordCheck.value = recorded && await fingerprint(recorded) === await fingerprint(fileEvidence.proof)
          && await fingerprint(official.manifest) === await fingerprint(fileEvidence.manifest)
          && await fingerprint(official.roster) === await fingerprint(fileEvidence.roster)
          ? 'This exact proof is included in the closed COPE ballot.'
          : 'This proof does not match the closed COPE record.'
      } else {
        recordCheck.value = await fingerprint(official) === await fingerprint(fileEvidence)
          ? 'This file matches the closed COPE ballot.'
          : 'This file differs from the closed COPE record.'
      }
    } catch {
      recordCheck.value = 'Could not load the corresponding ballot from COPE.'
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not verify this evidence.'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page-container page-container--narrow page-stack">
    <article class="panel evidence-sheet">
      <div class="evidence-sheet__section section-stack">
        <PageHeader title="Verify Quorum evidence">
          <template #actions><BaseButton :to="{ name: 'quorum' }" size="small" variant="ghost">All votes</BaseButton></template>
        </PageHeader>
        <form class="section-stack" @submit.prevent="verify">
          <label class="field">
            <span>Ballot evidence, vote proof, or witnessed receipt</span>
            <input type="file" accept=".json,application/json" :disabled="busy" required @change="selectFile">
            <small class="muted">Verification runs in this browser. The file stays on your device.</small>
          </label>
          <label class="field">
            <span>Trusted witness policy file (optional if already pinned)</span>
            <input type="file" accept=".json,application/json" :disabled="busy"
              @change="policyFile = ($event.target as HTMLInputElement).files?.[0]">
          </label>
          <div class="button-row">
            <BaseButton type="submit" variant="primary" :disabled="!file" :loading="busy">Verify file</BaseButton>
          </div>
          <InlineFeedback :message="error" />
        </form>
      </div>
      <section v-if="recordedTitle" class="evidence-sheet__section section-stack" aria-live="polite">
        <h2>{{ recordedTitle }}</h2>
        <InlineFeedback tone="info" :message="retained ? 'The pinned quorum verified and retained this exact vote proof.' : 'The pinned witness quorum recorded this exact vote proof before closing.'" />
      </section>
      <section v-if="result" class="evidence-sheet__section section-stack" aria-live="polite">
        <div class="toolbar toolbar--between">
          <h2>{{ result.title }}</h2>
          <StatusBadge v-if="result.kind === 'ballot'" :tone="resultTones[result.result.outcome]">
            {{ resultLabels[result.result.outcome] }}
          </StatusBadge>
        </div>
        <template v-if="result.kind === 'ballot'">
          <InlineFeedback tone="info" :message="witnessed ? 'The tally matches the complete history sealed by the pinned witnesses.' : 'The vote proofs match the tally in this file.'"  />
          <QuorumTally :result="result.result" />
          <p class="muted">{{ result.result.total }} of {{ result.result.electorate }} eligible voters participated.</p>
        </template>
        <template v-else>
          <InlineFeedback tone="info" message="Valid vote proof for the ballot in this file." />
          <p>Choice · <strong>{{ result.option }}</strong></p>
        </template>
        <p>{{ recordCheck }}</p>
        <div v-if="ballotId" class="button-row">
          <BaseButton :to="{ name: 'quorum-ballot', params: { id: ballotId } }" size="small">View ballot</BaseButton>
        </div>
      </section>
    </article>
  </div>
</template>

<style scoped>
.evidence-sheet__section { padding: clamp(var(--space-4), 3vw, var(--space-8)); }
.evidence-sheet__section + .evidence-sheet__section { border-top: 1px solid var(--color-border); }
.evidence-sheet h2, .evidence-sheet p { margin: 0; overflow-wrap: anywhere; }
.evidence-sheet h2 { font-size: 1.1875rem; }
</style>
