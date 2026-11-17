<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api/client'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import QuorumCredential from '@/components/public/QuorumCredential.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { verifyFounding, type Founding } from '@/quorum'
import { downloadQuorumFile, genesisStorage } from '@/quorum-voter'

const founding = ref<{ genesis: Founding['genesis'] | null; signatures: Founding['signatures'] }>()
const available = ref(false)
const fingerprint = ref(localStorage.getItem(genesisStorage) ?? '')
const error = ref('')
const notice = ref('')
const busy = ref(false)

onMounted(async () => {
  try {
    const [status, state] = await Promise.all([
      api.get<{ eligibility_check_available: boolean }>('/api/quorum'),
      api.get<NonNullable<typeof founding.value>>('/api/quorum/founding'),
    ])
    available.value = status.eligibility_check_available
    founding.value = state
  } catch (cause) { error.value = (cause as Error).message }
})

async function verify() {
  if (!founding.value?.genesis) return
  error.value = ''
  notice.value = ''
  busy.value = true
  try {
    const trusted = fingerprint.value.trim().toLowerCase()
    await verifyFounding({ genesis: founding.value.genesis, signatures: founding.value.signatures }, trusted)
    localStorage.setItem(genesisStorage, trusted)
    notice.value = 'Verified the founding roster of ' + founding.value.genesis.members.length + ' credentials.'
  } catch (cause) { error.value = (cause as Error).message }
  finally { busy.value = false }
}
</script>

<template>
  <div class="page-container page-container--narrow page-stack">
    <PageHeader title="Quorum voter tools" />
    <section class="panel panel__body section-stack">
      <h2>Founding enrollment</h2>
      <p v-if="!founding">Loading enrollment status.</p>
      <p v-else-if="founding.genesis">The founding roster is frozen. Use your enrolled credential to vote from an open ballot.</p>
      <template v-else-if="available">
        <p class="muted">Eligible Discord role holders can enroll one voting credential. Your private credential stays on your device.</p>
        <QuorumCredential />
      </template>
      <p v-else class="muted">Enrollment opens after the eligibility role is configured.</p>
    </section>
    <form v-if="founding?.genesis" class="panel panel__body section-stack" @submit.prevent="verify">
      <h2>Founding roster</h2>
      <p class="muted">Get the fingerprint from your independent witnesses before verifying the founding roster.</p>
      <label class="field"><span>Trusted founding fingerprint</span>
        <input v-model="fingerprint" class="input input--mono" maxlength="64" autocomplete="off" spellcheck="false" required @input="notice = ''">
      </label>
      <div class="button-row">
        <BaseButton type="submit" :loading="busy">Verify roster</BaseButton>
        <BaseButton @click="downloadQuorumFile('quorum-founding.json', JSON.stringify(founding))">Download founding evidence</BaseButton>
      </div>
    </form>
    <InlineFeedback :message="error" />
    <InlineFeedback :message="notice" tone="info" />
    <div class="button-row"><BaseButton :to="{ name: 'quorum-witness' }">Witness tools</BaseButton></div>
  </div>
</template>
