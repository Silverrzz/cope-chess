<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import api from '@/api/client'
import ContentState from '@/components/public/ContentState.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import QuorumCredential from '@/components/public/QuorumCredential.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { ballotOptions, ballotScope, certifiedSnapshot, type BallotPackage } from '@/quorum'
import { downloadQuorumFile, generateQuorumProof, genesisStorage, type Credential } from '@/quorum-voter'

const route = useRoute()
const packet = ref<BallotPackage>()
const credential = ref<Credential>()
const choice = ref('')
const pin = ref(localStorage.getItem(genesisStorage) ?? '')
const verified = ref<Awaited<ReturnType<typeof certifiedSnapshot>>>()
const loading = ref(true)
const busy = ref(false)
const loadError = ref('')
const error = ref('')
const now = ref(Date.now())
const receipt = ref('')
const manifest = computed(() => packet.value?.candidate.manifest)
const endpoint = computed(() => '/api/quorum/ballots/' + encodeURIComponent(String(route.params.id)))
const certified = computed(() => packet.value?.checkpoints.find(checkpoint =>
  Object.keys(checkpoint.signatures).length >= (packet.value?.genesis.threshold ?? 3)))
const open = computed(() => manifest.value && now.value >= Date.parse(manifest.value.opens_at)
  && now.value < Date.parse(manifest.value.closes_at) && certified.value && !certified.value.final)
const status = computed(() => {
  if (certified.value?.final) return 'Completed'
  if (!certified.value) return 'Awaiting opening signatures'
  if (packet.value?.candidate.final) return 'Awaiting result signatures'
  return open.value ? 'Open for voting' : 'Scheduled'
})
const receiptCovered = computed(() => Boolean(receipt.value && verified.value && certified.value && packet.value?.candidate.votes
  .slice(0, certified.value.size).some(proof => proof.nullifier === receipt.value)))
const publicCounts = computed(() => {
  if (!certified.value?.final || !manifest.value) return []
  const counts = ballotOptions(manifest.value).map(() => 0)
  for (const proof of packet.value?.candidate.votes.slice(0, certified.value.size) ?? []) {
    const index = Number(proof.message)
    if (index >= 0 && index < counts.length) counts[index] = (counts[index] ?? 0) + 1
  }
  return counts
})
const dates = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

async function load() {
  loadError.value = ''
  const path = endpoint.value
  try {
    const next = await api.get<BallotPackage>(path)
    if (path !== endpoint.value) return
    if (JSON.stringify(packet.value) !== JSON.stringify(next)) verified.value = undefined
    packet.value = next
    now.value = Date.now()
  } catch (cause) {
    if (path === endpoint.value) loadError.value = (cause as Error).message
  } finally {
    if (path === endpoint.value) loading.value = false
  }
}

watch(endpoint, () => {
  packet.value = undefined
  credential.value = undefined
  choice.value = ''
  receipt.value = localStorage.getItem('cope-quorum-receipt:' + route.params.id) ?? ''
  loading.value = true
  void load()
}, { immediate: true })

async function audit() {
  if (!packet.value || !certified.value) return
  busy.value = true
  error.value = ''
  verified.value = undefined
  try {
    const trusted = pin.value.trim().toLowerCase()
    verified.value = await certifiedSnapshot(packet.value, certified.value, trusted)
    localStorage.setItem(genesisStorage, trusted)
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    busy.value = false
  }
}

async function cast() {
  if (!packet.value || !manifest.value || !credential.value || !open.value || !verified.value || choice.value === '') return
  busy.value = true
  error.value = ''
  try {
    const proof = await generateQuorumProof(credential.value, packet.value.genesis.members, choice.value, await ballotScope(manifest.value))
    const response = await fetch(endpoint.value + '/votes', {
      method: 'POST', credentials: 'omit', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(proof),
    })
    const result = await response.json()
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : result.detail?.message ?? 'Could not submit the vote.')
    localStorage.setItem('cope-quorum-receipt:' + manifest.value.id, result.nullifier)
    receipt.value = result.nullifier
    credential.value = undefined
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
        <div class="toolbar toolbar--between"><strong>{{ status }}</strong><span class="muted">Closes {{ dates.format(new Date(manifest.closes_at)) }}</span></div>
        <p class="quorum-rationale">{{ manifest.rationale }}</p>
        <p v-if="manifest.target" class="muted">{{ manifest.action === 'include' ? 'Add' : 'Remove' }} {{ manifest.target }}</p>
      </section>

      <section v-if="certified?.final" class="panel panel__body section-stack">
        <h2>Results</h2>
        <p v-if="verified">{{ verified.outcome }}</p>
        <ul class="section-stack">
          <li v-for="(option, index) in ballotOptions(manifest)" :key="option">{{ option }} <strong>{{ publicCounts[index] }}</strong></li>
        </ul>
        <p class="muted">{{ certified.size }} votes from {{ packet.genesis.members.length }} eligible credentials</p>
      </section>

      <section class="panel panel__body section-stack">
        <h2>Public verification</h2>
        <div class="button-row">
          <BaseButton @click="downloadQuorumFile('quorum-' + manifest.id + '.json', JSON.stringify(packet))">Download ballot evidence</BaseButton>
          <BaseButton size="small" variant="ghost" :disabled="busy" @click="load">Refresh evidence</BaseButton>
        </div>
        <form v-if="certified" class="section-stack" @submit.prevent="audit">
          <label class="field"><span>Trusted founding fingerprint</span>
            <input v-model="pin" class="input input--mono" maxlength="64" autocomplete="off" spellcheck="false" required :disabled="busy" @input="verified = undefined">
          </label>
          <p class="muted">Get the fingerprint from your independent witnesses before verifying the roster, signatures, and vote proofs.</p>
          <div class="button-row"><BaseButton type="submit" :loading="busy && !verified">Verify ballot</BaseButton></div>
        </form>
        <InlineFeedback v-if="verified" tone="info" :message="certified?.final ? 'Final vote proofs and witness signatures verified.' : 'Roster and opening signatures verified.'" />
      </section>

      <section v-if="receipt" class="panel panel__body section-stack">
        <h2>Your vote</h2>
        <p>{{ receiptCovered ? 'Your vote is included in the verified record.' : 'Vote received. Refresh the evidence and verify the checkpoint to confirm inclusion.' }}</p>
      </section>
      <section v-else-if="open" class="panel panel__body section-stack">
        <h2>Cast a vote</h2>
        <p v-if="!verified" class="muted">Verify the ballot first.</p>
        <QuorumCredential v-else-if="!credential" :members="packet.genesis.members" @unlocked="credential = $event" />
        <form v-else class="section-stack" @submit.prevent="cast">
          <label class="field"><span>Your choice</span><select v-model="choice" class="select" required :disabled="busy">
            <option value="" disabled>Select an option</option>
            <option v-for="(option, index) in ballotOptions(manifest)" :key="option" :value="String(index)">{{ option }}</option>
          </select></label>
            <p class="muted">Your choice becomes part of the public ballot record with an anonymous proof. Each credential can cast one vote.</p>
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
