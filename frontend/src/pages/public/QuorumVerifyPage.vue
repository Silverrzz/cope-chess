<script setup lang="ts">
import { ref } from 'vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { verifyEvidence } from '@/quorum'
import { resultLabels } from '@/quorum-results'

const file = ref<File>()
const result = ref<Awaited<ReturnType<typeof verifyEvidence>>>()
const busy = ref(false)
const error = ref('')

function selectFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0]
  result.value = undefined
  error.value = ''
}

async function verify() {
  if (!file.value || busy.value) return
  busy.value = true
  result.value = undefined
  error.value = ''
  try {
    if (file.value.size > 2 * 1024 * 1024) throw new Error('Choose a Quorum JSON file up to 2 MiB.')
    let evidence: unknown
    try {
      evidence = JSON.parse(await file.value.text())
    } catch {
      throw new Error('This file contains invalid JSON.')
    }
    result.value = await verifyEvidence(evidence)
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
    </section>
  </div>
</template>
