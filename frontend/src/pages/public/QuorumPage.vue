<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api/client'
import ContentState from '@/components/public/ContentState.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import { discordLogin, useDiscordSession } from '@/stores/discord'

const discord = useDiscordSession()
const status = ref<{ status: 'setup'; eligibility_check_available: boolean }>()
const error = ref('')
const eligible = ref(false)
const checking = ref(false)
const rosterFile = ref<File>()
const fingerprint = ref('')
const rosterCount = ref<number>()
const verificationError = ref('')
const verifying = ref(false)

async function load() {
  error.value = ''
  try {
    const [value] = await Promise.all([
      api.get<NonNullable<typeof status.value>>('/api/quorum'),
      discord.load(),
    ])
    status.value = value
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not load Quorum.'
  }
}

async function checkRole() {
  checking.value = true
  eligible.value = false
  error.value = ''
  try {
    await api.get('/api/quorum/eligibility')
    eligible.value = true
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not check the Discord role.'
  } finally {
    checking.value = false
  }
}

async function verifyRoster() {
  verificationError.value = ''
  rosterCount.value = undefined
  const digest = fingerprint.value.trim().toLowerCase()
  const file = rosterFile.value
  if (!file || file.size > 1024 * 1024 || !/^[a-f0-9]{64}$/.test(digest)) {
    verificationError.value = 'Choose a roster JSON file up to 1 MiB and enter its 64-character SHA-256 fingerprint.'
    return
  }
  verifying.value = true
  try {
    const bytes = await file.arrayBuffer()
    const actual = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)),
      byte => byte.toString(16).padStart(2, '0')).join('')
    if (actual !== digest) throw new Error('The file does not match the supplied fingerprint.')
    const value: unknown = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes))
    const members = value && typeof value === 'object' && 'members' in value ? value.members : undefined
    if (!Array.isArray(members) || members.length < 1 || members.length > 1024 ||
        members.some(item => typeof item !== 'string' || !/^[1-9][0-9]{0,76}$/.test(item) ||
          BigInt(item) >= 21888242871839275222246405745257275088548364400416034343698204186575808495617n) ||
        new Set(members).size !== members.length) {
      throw new Error('Expected a roster with 1–1,024 distinct public commitments in its members array.')
    }
    rosterCount.value = members.length
  } catch (cause) {
    verificationError.value = cause instanceof Error ? cause.message : 'Could not check the roster.'
  } finally {
    verifying.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-container page-container--narrow page-stack">
    <PageHeader title="Quorum" description="Community decisions on engines and voting membership." />
    <ContentState v-if="!status && !error" kind="loading" compact title="Loading Quorum" />
    <ContentState v-else-if="!status" kind="error" compact :message="error" action-label="Try again" @action="load" />
    <section v-else class="panel panel__body section-stack" aria-labelledby="quorum-enrollment">
      <h2 id="quorum-enrollment">Founding enrollment</h2>
      <p class="muted">Quorum is in setup. Role checks are available when configured, and credential enrollment opens after the founding process is ready.</p>
      <div v-if="status.eligibility_check_available" class="button-row">
        <BaseButton v-if="discord.account" :loading="checking" @click="checkRole">Check my Discord role</BaseButton>
        <BaseButton v-else-if="discord.enabled" :href="discordLogin('/quorum')" variant="primary">Connect Discord</BaseButton>
      </div>
      <p v-if="status.eligibility_check_available && !discord.account && !discord.enabled" class="muted">Discord sign-in is unavailable.</p>
      <InlineFeedback :message="error" />
      <InlineFeedback v-if="eligible" tone="info" message="Your connected account holds the enrollment role." />
    </section>

    <form class="panel panel__body section-stack" @submit.prevent="verifyRoster">
      <h2>Check a roster file</h2>
      <p class="muted">Supply the fingerprint from a trusted channel. The file stays on your device. This check compares the file with that fingerprint.</p>
      <label class="field"><span>Roster JSON</span><input type="file" accept=".json,application/json" :disabled="verifying"
        @change="rosterFile = ($event.target as HTMLInputElement).files?.[0]; rosterCount = undefined"></label>
      <label class="field"><span>SHA-256 fingerprint</span><input v-model="fingerprint" class="input input--mono" maxlength="64"
        autocomplete="off" spellcheck="false" :disabled="verifying" @input="rosterCount = undefined"></label>
      <InlineFeedback :message="verificationError" />
      <InlineFeedback v-if="rosterCount !== undefined" tone="info" :message="`The file matches the fingerprint and lists ${rosterCount} unique commitments.`" />
      <div class="button-row"><BaseButton type="submit" :loading="verifying">Check roster</BaseButton></div>
    </form>
  </div>
</template>
