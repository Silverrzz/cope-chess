<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api/client'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { discordHeaders, discordLogin, useDiscordSession } from '@/stores/discord'
import { createCredential, credentialStorage, downloadQuorumFile, generateQuorumProof, rememberCredential, unlockCredential, unlockedCredential } from '@/quorum-voter'

const props = defineProps<{ members?: string[] }>()
const discord = useDiscordSession()
const stored = ref(localStorage.getItem(credentialStorage))
const backup = ref<File>()
const restoring = ref(Boolean(props.members && !stored.value))
const password = ref('')
const passwordEditing = ref(false)
const error = ref('')
const busy = ref(false)
const checking = ref(false)
const eligibility = ref<{ scope: string; commitment: string | null }>()

async function checkEnrollment() {
  if (props.members || unlockedCredential.value) return
  checking.value = true
  error.value = ''
  try {
    await discord.load()
    eligibility.value = discord.account
      ? await api.get<NonNullable<typeof eligibility.value>>('/api/quorum/eligibility', { headers: discordHeaders })
      : undefined
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    checking.value = false
  }
}
onMounted(checkEnrollment)

function lockCredential() {
  rememberCredential(null)
  restoring.value = false
  backup.value = undefined
  void checkEnrollment()
}

async function continueWithCredential() {
  if (busy.value || !password.value) return
  const submittedPassword = password.value
  password.value = ''
  passwordEditing.value = false
  busy.value = true
  error.value = ''
  try {
    if (backup.value && backup.value.size > 4096) throw new Error('This credential backup is too large.')
    const encrypted = backup.value ? await backup.value.text() : stored.value
    const enrollment = props.members ? null : eligibility.value
    if (!encrypted && (!enrollment || enrollment.commitment))
      throw new Error('Restore the encrypted backup for your enrolled credential.')
    const credential = encrypted ? await unlockCredential(submittedPassword, encrypted) : await createCredential(submittedPassword)
    stored.value = localStorage.getItem(credentialStorage)
    if (props.members && !props.members.includes(credential.commitment)) throw new Error('This credential was not enrolled when the ballot was published.')
    if (enrollment?.commitment && enrollment.commitment !== credential.commitment)
      throw new Error('Your Discord account already enrolled a different credential.')
    if (enrollment && !enrollment.commitment) {
      const proof = await generateQuorumProof(credential, [credential.commitment], '0', enrollment.scope)
      await api.post('/api/quorum/enroll', { headers: discordHeaders, body: { commitment: credential.commitment, proof } })
    }
    if (encrypted) {
      localStorage.setItem(credentialStorage, encrypted)
      stored.value = encrypted
    }
    rememberCredential(credential)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not use this credential.'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="section-stack">
    <div v-if="!members && unlockedCredential" class="button-row">
      <span>Credential unlocked</span>
      <BaseButton size="small" variant="ghost" @click="lockCredential">Lock</BaseButton>
    </div>
    <p v-else-if="checking" class="muted" role="status">Checking enrollment...</p>
    <div v-else-if="!members && discord.loaded && !discord.account" class="section-stack">
      <div v-if="discord.enabled" class="button-row">
        <BaseButton :href="discordLogin('/quorum?credential=1')" variant="primary">Connect Discord</BaseButton>
      </div>
      <p v-else class="muted">Discord sign-in is unavailable.</p>
    </div>
    <div v-else-if="!members && eligibility?.commitment && !restoring" class="button-row">
      <span>Enrolled</span>
      <BaseButton size="small" variant="ghost" @click="restoring = true">Restore a backup</BaseButton>
    </div>
    <form v-else-if="members || eligibility" class="section-stack" autocomplete="off" @submit.prevent="continueWithCredential">
      <p v-if="members && !stored" class="muted">Restore the encrypted backup you saved when you enrolled.</p>
      <div v-if="!restoring" class="button-row">
        <BaseButton size="small" variant="ghost" @click="restoring = true">Restore a backup</BaseButton>
      </div>
      <label v-else class="field"><span>Restore encrypted backup</span>
        <input type="file" accept=".json,application/json" :disabled="busy" @change="backup = ($event.target as HTMLInputElement).files?.[0]">
      </label>
      <div v-if="restoring && (!members || stored)" class="button-row">
        <BaseButton size="small" variant="ghost" :disabled="busy" @click="restoring = false; backup = undefined">Cancel restore</BaseButton>
      </div>
      <label class="field"><span>{{ stored || restoring || members ? 'Credential password' : 'Choose a credential password' }}</span>
        <input v-model="password" class="input" type="password" autocomplete="off"
          data-1p-ignore data-lpignore="true" data-bwignore :readonly="!passwordEditing" @focus="passwordEditing = true"
          :minlength="stored || backup || members ? 1 : 12" maxlength="256" required :disabled="busy">
      </label>
      <div class="button-row">
        <BaseButton type="submit" variant="primary" :loading="busy" :disabled="!password || (restoring && !backup) || Boolean(members && !stored && !backup)">
          {{ members ? 'Unlock credential' : eligibility?.commitment ? 'Restore credential' : 'Enroll credential' }}
        </BaseButton>
      </div>
    </form>
    <InlineFeedback :message="error" />
    <div v-if="stored" class="button-row">
      <BaseButton size="small" @click="downloadQuorumFile('quorum-credential.json', stored)">Download encrypted backup</BaseButton>
    </div>
  </div>
</template>
