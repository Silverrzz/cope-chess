<script setup lang="ts">
import { ref } from 'vue'
import api from '@/api/client'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { fingerprint, verifyEvidence, type BallotPackage, type VoteEvidence } from '@/quorum'
import { resultLabels } from '@/quorum-results'

const file = ref<File>()
const result = ref<Awaited<ReturnType<typeof verifyEvidence>>>()
const busy = ref(false)
const error = ref('')
const recordCheck = ref('')
const ballotId = ref('')

function selectFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0]
  result.value = undefined
  error.value = ''
  recordCheck.value = ''
  ballotId.value = ''
}

async function verify() {
  if (!file.value || busy.value) return
  busy.value = true
  result.value = undefined
  error.value = ''
  recordCheck.value = ''
  ballotId.value = ''
  try {
    if (file.value.size > 2 * 1024 * 1024) throw new Error('Choose a Quorum JSON file up to 2 MiB.')
    let evidence: unknown
    try {
      evidence = JSON.parse(await file.value.text())
    } catch {
      throw new Error('This file contains invalid JSON.')
    }
    const verified = await verifyEvidence(evidence)
    const fileEvidence = evidence as BallotPackage | VoteEvidence
    result.value = verified
    try {
      const official = await api.get<BallotPackage>('/api/quorum/ballots/' + fileEvidence.manifest.id)
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
    <PageHeader title="Verify Quorum evidence">
      <template #actions><BaseButton :to="{ name: 'quorum' }" size="small" variant="ghost">All votes</BaseButton></template>
    </PageHeader>
    <form class="panel panel__body section-stack" @submit.prevent="verify">
      <label class="field">
        <span>Ballot evidence or vote proof</span>
        <input type="file" accept=".json,application/json" :disabled="busy" required @change="selectFile">
        <small class="muted">Verification runs in this browser. The file stays on your device.</small>
      </label>
      <div class="button-row">
        <BaseButton type="submit" variant="primary" :disabled="!file" :loading="busy">Verify file</BaseButton>
      </div>
      <InlineFeedback :message="error" />
    </form>
    <section v-if="result" class="panel panel__body section-stack" aria-live="polite">
      <h2>{{ result.title }}</h2>
      <template v-if="result.kind === 'ballot'">
        <InlineFeedback tone="info" message="The vote proofs match the tally in this file." />
        <p>{{ resultLabels[result.result.outcome] }} · {{ result.result.total }} of {{ result.result.electorate }} voted</p>
        <ul>
          <li v-for="(option, index) in result.result.options" :key="option">{{ option }} <strong>{{ result.result.counts[index] }}</strong></li>
        </ul>
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
  </div>
</template>
