<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api/client'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { discordHeaders, discordLogin, useDiscordSession } from '@/stores/discord'
import { createCredential, credentialStorage, downloadQuorumFile, encryptCredential, generateQuorumProof, rememberCredential, unlockCredential, unlockedCredential } from '@/quorum-voter'

type CredentialAction = 'default' | 'restore' | 'change-password' | 'replace'
interface Eligibility {
  scope: string
  commitment: string | null
  replacement_scope: string | null
}
interface PendingReplacement {
  replaces: string | null
  commitment: string
  encrypted: string
}

const props = defineProps<{ members?: string[] }>()
const discord = useDiscordSession()
const replacementKey = 'cope-quorum-replacement-v1'
const stored = ref(localStorage.getItem(credentialStorage))
const backup = ref<File>()
const action = ref<CredentialAction>(props.members && !stored.value ? 'restore' : 'default')
const password = ref('')
const passwordEditing = ref(false)
const error = ref('')
const notice = ref('')
const busy = ref(false)
const checking = ref(false)
const eligibility = ref<Eligibility>()

function saveBackup(encrypted: string) {
  localStorage.setItem(credentialStorage, encrypted)
  stored.value = encrypted
}

function pendingReplacement(): PendingReplacement | null {
  return JSON.parse(localStorage.getItem(replacementKey) ?? 'null')
}

async function checkEnrollment() {
  if (props.members || unlockedCredential.value) return
  checking.value = true
  error.value = ''
  eligibility.value = undefined
  try {
    await discord.load()
    if (!discord.account) return
    eligibility.value = await api.get<Eligibility>('/api/quorum/eligibility', { headers: discordHeaders })
    const pending = pendingReplacement()
    if (pending && eligibility.value.commitment === pending.commitment) {
      saveBackup(pending.encrypted)
      localStorage.removeItem(replacementKey)
    }
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    checking.value = false
  }
}
onMounted(checkEnrollment)

function selectAction(next: CredentialAction) {
  action.value = next
  backup.value = undefined
  password.value = ''
  passwordEditing.value = false
  error.value = ''
  notice.value = ''
}

function lockCredential() {
  rememberCredential(null)
  selectAction('default')
  void checkEnrollment()
}

async function replaceCredential(newPassword: string, enrollment: Eligibility) {
  const pending = pendingReplacement()
  const retry = pending?.replaces === enrollment.commitment ? pending : null
  const credential = retry ? await unlockCredential(newPassword, retry.encrypted) : createCredential()
  const encrypted = retry?.encrypted ?? await encryptCredential(credential, newPassword)
  localStorage.setItem(replacementKey, JSON.stringify({ commitment: credential.commitment, replaces: enrollment.commitment, encrypted }))
  const proof = await generateQuorumProof(credential, [credential.commitment], '0', enrollment.replacement_scope ?? enrollment.scope)
  await api.post('/api/quorum/enroll', {
    headers: discordHeaders,
    body: { commitment: credential.commitment, replaces: enrollment.commitment, proof },
  })
  saveBackup(encrypted)
  localStorage.removeItem(replacementKey)
  rememberCredential(credential)
  selectAction('default')
  notice.value = 'New credential saved.'
}

async function continueWithCredential() {
  if (busy.value || !password.value) return
  const submittedPassword = password.value
  password.value = ''
  passwordEditing.value = false
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    if (action.value === 'change-password' && unlockedCredential.value) {
      saveBackup(await encryptCredential(unlockedCredential.value, submittedPassword))
      selectAction('default')
      notice.value = 'Password updated. Download a fresh backup.'
      return
    }
    const enrollment = props.members ? null : eligibility.value
    if (action.value === 'replace' && enrollment) {
      await replaceCredential(submittedPassword, enrollment)
      return
    }
    if (backup.value && backup.value.size > 4096) throw new Error('This credential backup is too large.')
    const encrypted = backup.value ? await backup.value.text() : stored.value
    if (!encrypted && (!enrollment || enrollment.commitment))
      throw new Error('Restore the encrypted backup for your enrolled credential.')
    const credential = encrypted ? await unlockCredential(submittedPassword, encrypted) : createCredential()
    if (!encrypted) saveBackup(await encryptCredential(credential, submittedPassword))
    if (props.members && !props.members.includes(credential.commitment)) throw new Error('This credential was not enrolled when the ballot was published.')
    if (enrollment?.commitment && enrollment.commitment !== credential.commitment)
      throw new Error('Your Discord account already enrolled a different credential.')
    if (enrollment && !enrollment.commitment) {
      const proof = await generateQuorumProof(credential, [credential.commitment], '0', enrollment.scope)
      await api.post('/api/quorum/enroll', { headers: discordHeaders, body: { commitment: credential.commitment, proof } })
    }
    if (encrypted) saveBackup(encrypted)
    rememberCredential(credential)
    selectAction('default')
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not use this credential.'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="section-stack">
    <p class="muted">This web client uses code supplied by COPE. That code can access your credential while it is unlocked.</p>
    <div v-if="!members && unlockedCredential && action === 'default'" class="button-row">
      <span>Credential unlocked</span>
      <BaseButton size="small" variant="ghost" @click="selectAction('change-password')">Change password</BaseButton>
      <BaseButton size="small" variant="ghost" @click="lockCredential">Lock</BaseButton>
    </div>
    <p v-else-if="checking" class="muted" role="status">Checking enrollment...</p>
    <div v-else-if="!members && !unlockedCredential && discord.loaded && !discord.account" class="section-stack">
      <div v-if="discord.enabled" class="button-row">
        <BaseButton :href="discordLogin('/quorum?credential=1')" variant="primary">Connect Discord</BaseButton>
      </div>
      <p v-else class="muted">Discord sign-in is unavailable.</p>
    </div>
    <div v-else-if="!members && eligibility?.commitment && action === 'default'" class="button-row">
      <span>Enrolled</span>
      <BaseButton size="small" variant="ghost" @click="selectAction('restore')">Restore a backup</BaseButton>
      <BaseButton size="small" variant="ghost" @click="selectAction('replace')">Replace lost credential</BaseButton>
    </div>
    <form v-else-if="members || eligibility || unlockedCredential" class="section-stack" autocomplete="off" @submit.prevent="continueWithCredential">
      <p v-if="action === 'replace' && eligibility?.commitment" class="muted">The new credential is for future ballots. Published ballots still require the original key.</p>
      <p v-if="members && !stored" class="muted">Restore the encrypted backup you saved when you enrolled.</p>
      <div v-if="action === 'default'" class="button-row">
        <BaseButton size="small" variant="ghost" @click="selectAction('restore')">Restore a backup</BaseButton>
        <BaseButton v-if="!members && stored" size="small" variant="ghost" @click="selectAction('replace')">Start enrollment over</BaseButton>
      </div>
      <label v-if="action === 'restore'" class="field"><span>Restore encrypted backup</span>
        <input type="file" accept=".json,application/json" :disabled="busy" @change="backup = ($event.target as HTMLInputElement).files?.[0]">
      </label>
      <label class="field">
        <span>{{ action === 'change-password' || action === 'replace' ? 'New credential password' : stored || action === 'restore' || members ? 'Credential password' : 'Choose a credential password' }}</span>
        <input v-model="password" class="input" type="password" autocomplete="off"
          data-1p-ignore data-lpignore="true" data-bwignore :readonly="!passwordEditing" @focus="passwordEditing = true"
          :minlength="action === 'change-password' || action === 'replace' || (!stored && !members && action === 'default') ? 12 : 1"
          maxlength="256" required :disabled="busy">
      </label>
      <div class="button-row">
        <BaseButton type="submit" variant="primary" :loading="busy" :disabled="!password || (action === 'restore' && !backup)">
          {{ action === 'change-password' ? 'Save password' : action === 'replace' ? 'Create new credential' : members ? 'Unlock credential' : action === 'restore' && eligibility?.commitment ? 'Restore credential' : 'Enroll credential' }}
        </BaseButton>
        <BaseButton v-if="action !== 'default' && (!members || stored)" size="small" variant="ghost" :disabled="busy" @click="selectAction('default')">Cancel</BaseButton>
      </div>
    </form>
    <InlineFeedback :message="error" />
    <InlineFeedback :message="notice" tone="info" />
    <div v-if="stored" class="button-row">
      <BaseButton size="small" @click="downloadQuorumFile('quorum-credential.json', stored)">Download encrypted backup</BaseButton>
    </div>
  </div>
</template>
