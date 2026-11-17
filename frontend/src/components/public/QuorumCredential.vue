<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api/client'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { discordHeaders, discordLogin, useDiscordSession } from '@/stores/discord'
import { createCredential, credentialStorage, downloadQuorumFile, generateQuorumProof, unlockCredential, type Credential } from '@/quorum-voter'

const props = defineProps<{ members?: string[] }>()
const emit = defineEmits<{ unlocked: [credential: Credential] }>()
const discord = useDiscordSession()
const stored = ref(localStorage.getItem(credentialStorage))
const backup = ref<File>()
const restoring = ref(Boolean(props.members && !stored.value))
const password = ref('')
const error = ref('')
const notice = ref('')
const busy = ref(false)
const finished = ref(false)

onMounted(async () => {
  if (props.members) return
  try { await discord.load() } catch (cause) { error.value = (cause as Error).message }
})

async function continueWithCredential() {
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    if (backup.value && backup.value.size > 4096) throw new Error('This credential backup is too large.')
    const encrypted = backup.value ? await backup.value.text() : stored.value
    const eligibility = props.members ? null : await api.get<{ scope: string; commitment: string | null }>(
      '/api/quorum/eligibility', { headers: discordHeaders })
    if (!encrypted && (!eligibility || eligibility.commitment))
      throw new Error('Restore the encrypted backup for your enrolled credential.')
    const credential = encrypted ? await unlockCredential(password.value, encrypted) : await createCredential(password.value)
    stored.value = localStorage.getItem(credentialStorage)
    if (props.members && !props.members.includes(credential.commitment)) throw new Error('This credential was not enrolled when the ballot was published.')
    if (eligibility?.commitment && eligibility.commitment !== credential.commitment)
      throw new Error('Your Discord account already enrolled a different credential.')
    if (eligibility && !eligibility.commitment) {
      const proof = await generateQuorumProof(credential, [credential.commitment], '0', eligibility.scope)
      await api.post('/api/quorum/enroll', { headers: discordHeaders, body: { commitment: credential.commitment, proof } })
    }
    if (encrypted) {
      localStorage.setItem(credentialStorage, encrypted)
      stored.value = encrypted
    }
    password.value = ''
    notice.value = props.members ? 'Credential unlocked.' : 'Credential enrolled. Save your encrypted backup for use on another device.'
    finished.value = true
    emit('unlocked', credential)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not use this credential.'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="section-stack">
    <div v-if="!members && discord.loaded && !discord.account" class="section-stack">
      <p class="muted">Connect Discord so COPE can confirm you hold the enrollment role.</p>
      <div v-if="discord.enabled" class="button-row">
        <BaseButton :href="discordLogin()" variant="primary">Connect Discord</BaseButton>
      </div>
      <p v-else class="muted">Discord sign-in is unavailable.</p>
    </div>
    <form v-else-if="!finished && (members || discord.account)" class="section-stack" @submit.prevent="continueWithCredential">
      <p v-if="members && !stored" class="muted">Restore the encrypted backup you saved when you enrolled.</p>
      <div v-if="!restoring" class="button-row">
        <BaseButton size="small" variant="ghost" @click="restoring = true">Restore a backup</BaseButton>
      </div>
      <label v-else class="field"><span>Restore encrypted backup</span>
        <input type="file" accept=".json,application/json" :disabled="busy" @change="backup = ($event.target as HTMLInputElement).files?.[0]">
      </label>
      <label class="field"><span>{{ stored || backup || members ? 'Credential password' : 'Choose a credential password' }}</span>
        <input v-model="password" class="input" type="password" :autocomplete="stored || backup || members ? 'current-password' : 'new-password'"
          :minlength="stored || backup || members ? 1 : 12" maxlength="256" required :disabled="busy">
      </label>
      <div class="button-row">
        <BaseButton type="submit" variant="primary" :loading="busy" :disabled="Boolean(members && !stored && !backup)">
          {{ members ? 'Unlock credential' : 'Enroll credential' }}
        </BaseButton>
      </div>
    </form>
    <InlineFeedback :message="error" />
    <InlineFeedback :message="notice" tone="info" />
    <div v-if="stored" class="button-row">
      <BaseButton size="small" @click="downloadQuorumFile('quorum-credential.json', stored)">Download encrypted backup</BaseButton>
    </div>
  </div>
</template>
