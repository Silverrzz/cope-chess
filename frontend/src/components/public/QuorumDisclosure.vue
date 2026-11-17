<script setup lang="ts">
import { onMounted, ref } from 'vue'
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
const busy = ref(false)
const published = ref(false)
const error = ref('')
const preview = ref<{ commitment: string; proof: SemaphoreProof; username: string; option: string }>()
const path = '/api/quorum/ballots/' + props.ballot.manifest.id + '/disclosure'
const returnTo = '/quorum/ballots/' + props.ballot.manifest.id + '?publish-vote=1'

async function begin() {
  active.value = true
  error.value = ''
  busy.value = true
  try {
    await discord.load()
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  if (new URLSearchParams(location.search).get('publish-vote') === '1') void begin()
})

async function previewVote() {
  const credential = unlockedCredential.value
  if (!credential || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const challenge = await api.get<{ message: string; username: string }>(path, { headers: discordHeaders })
    const proof = await generateQuorumProof(credential, [credential.commitment], challenge.message, await ballotScope(props.ballot.manifest))
    const recorded = props.ballot.votes.find(vote => vote.nullifier === proof.nullifier)
    const option = recorded && props.ballot.result?.options[Number(recorded.message)]
    if (!option) throw new Error('This credential has no recorded vote on this ballot.')
    preview.value = { commitment: credential.commitment, proof, username: challenge.username, option }
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    busy.value = false
  }
}

async function publishVote() {
  if (!preview.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const { commitment, proof } = preview.value
    await api.post(path, { body: { commitment, proof }, headers: discordHeaders })
    published.value = true
    preview.value = undefined
    emit('published')
  } catch (cause) {
    error.value = (cause as Error).message
    preview.value = undefined
  } finally {
    busy.value = false
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
      <template v-if="preview">
        <p>Anyone will be able to see that <strong>@{{ preview.username }}</strong> voted <strong>{{ preview.option }}</strong>. Publishing is permanent.</p>
        <div class="button-row">
          <BaseButton variant="danger" :loading="busy" @click="publishVote">Confirm public vote</BaseButton>
          <BaseButton variant="ghost" :disabled="busy" @click="cancel">Cancel</BaseButton>
        </div>
      </template>
      <template v-else>
        <div v-if="!discord.account" class="button-row">
          <BaseButton v-if="discord.enabled" :href="discordLogin(returnTo)">Connect Discord</BaseButton>
          <BaseButton v-else-if="error" @click="begin">Try again</BaseButton>
        </div>
        <QuorumCredential v-else-if="!unlockedCredential || !ballot.roster.members.includes(unlockedCredential.commitment)"
          :members="ballot.roster.members" />
        <div v-else class="button-row"><BaseButton :loading="busy" @click="previewVote">Preview public vote</BaseButton></div>
        <div class="button-row"><BaseButton size="small" variant="ghost" :disabled="busy" @click="cancel">Cancel</BaseButton></div>
      </template>
      <InlineFeedback :message="error" />
    </template>
  </div>
</template>
