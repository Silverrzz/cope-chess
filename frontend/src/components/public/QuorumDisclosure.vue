<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import type { SemaphoreProof } from '@semaphore-protocol/proof'
import api from '@/api/client'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { ballotScope, type BallotPackage } from '@/quorum'
import { generateQuorumProof, unlockedCredential } from '@/quorum-voter'
import { discordHeaders, discordLogin, useDiscordSession } from '@/stores/discord'
import QuorumCredential from './QuorumCredential.vue'

const props = defineProps<{ ballot: BallotPackage }>()
const emit = defineEmits<{ published: [] }>()
const discord = useDiscordSession()
const active = ref(false)
const checkingDiscord = ref(false)
const checkedDiscord = ref(false)
const preparing = ref(false)
const publishing = ref(false)
const busy = computed(() => checkingDiscord.value || preparing.value || publishing.value)
const eligibleCredential = computed(() => {
  const credential = unlockedCredential.value
  return credential && props.ballot.roster.members.includes(credential.commitment) ? credential : null
})
const published = ref(false)
const error = ref('')
const preview = ref<{ commitment: string; proof: SemaphoreProof; username: string; option: string }>()
const path = '/api/quorum/ballots/' + props.ballot.manifest.id + '/disclosure'
const returnTo = '/quorum/ballots/' + props.ballot.manifest.id + '?publish-vote=1'

async function begin() {
  if (busy.value) return
  active.value = true
  error.value = ''
  checkingDiscord.value = true
  checkedDiscord.value = false
  try {
    await discord.load()
    checkedDiscord.value = true
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    checkingDiscord.value = false
  }
}

onMounted(() => {
  if (new URLSearchParams(location.search).get('publish-vote') === '1') void begin()
})

watch([active, checkedDiscord, () => discord.account, eligibleCredential], async ([show, checked, account, credential], _, onCleanup) => {
  preview.value = undefined
  preparing.value = false
  if (!show || !checked || !account || !credential || published.value) return

  let current = true
  onCleanup(() => { current = false })
  preparing.value = true
  error.value = ''
  try {
    const challenge = await api.get<{ message: string; username: string }>(path, { headers: discordHeaders })
    if (!current) return
    const proof = await generateQuorumProof(credential, [credential.commitment], challenge.message, await ballotScope(props.ballot.manifest))
    if (!current) return
    const recorded = props.ballot.votes.find(vote => vote.nullifier === proof.nullifier)
    const option = recorded && props.ballot.result?.options[Number(recorded.message)]
    if (!option) throw new Error('This credential has no recorded vote on this ballot.')
    preview.value = { commitment: credential.commitment, proof, username: challenge.username, option }
  } catch (cause) {
    if (current) error.value = (cause as Error).message
  } finally {
    if (current) preparing.value = false
  }
})

async function publishVote() {
  if (!preview.value || busy.value) return
  publishing.value = true
  error.value = ''
  try {
    const { commitment, proof } = preview.value
    await api.post(path, { body: { commitment, proof }, headers: discordHeaders })
    published.value = true
    preview.value = undefined
    emit('published')
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    publishing.value = false
  }
}

function cancel() {
  active.value = false
  preview.value = undefined
  error.value = ''
}
</script>

<template>
  <div class="section-stack">
    <InlineFeedback v-if="published" tone="info" message="Your vote is public." />
    <div v-else-if="!active" class="button-row">
      <BaseButton size="small" @click="begin">Publish my vote</BaseButton>
    </div>
    <template v-else>
      <p v-if="checkingDiscord || preparing" class="muted" role="status">Preparing confirmation…</p>
      <template v-else-if="preview">
        <p>Anyone will be able to see that <strong>@{{ preview.username }}</strong> voted <strong>{{ preview.option }}</strong>. Publishing is permanent.</p>
        <div class="button-row">
          <BaseButton variant="danger" :loading="publishing" @click="publishVote">Confirm public vote</BaseButton>
          <BaseButton variant="ghost" :disabled="busy" @click="cancel">Cancel</BaseButton>
        </div>
      </template>
      <template v-else>
        <div v-if="error" class="button-row"><BaseButton size="small" @click="begin">Try again</BaseButton></div>
        <div v-else-if="checkedDiscord && !discord.account && discord.enabled" class="button-row">
          <BaseButton :href="discordLogin(returnTo)">Connect Discord</BaseButton>
        </div>
        <p v-else-if="checkedDiscord && !discord.account" class="muted">Discord sign-in is unavailable.</p>
        <QuorumCredential v-else-if="!eligibleCredential" :members="ballot.roster.members" />
        <div class="button-row"><BaseButton size="small" variant="ghost" :disabled="busy" @click="cancel">Cancel</BaseButton></div>
      </template>
      <InlineFeedback :message="error" />
    </template>
  </div>
</template>
