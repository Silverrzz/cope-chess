<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import api from '@/api/client'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { downloadQuorumFile } from '@/quorum-voter'
import { discordHeaders, discordLogin, useDiscordSession } from '@/stores/discord'

const discord = useDiscordSession()
const state = ref<{ statement: string; public_key: string | null }>()
const publicKey = ref('')
const signature = ref('')
const error = ref('')
const busy = ref(false)
const route = useRoute()
const ballotId = computed(() => {
  const id = route.query.ballot
  return typeof id === 'string' && /^[a-f0-9-]{36}$/.test(id) ? id : ''
})
const signedCheckpoint = ref('')
const signingError = ref('')
const signingNotice = ref('')
const signing = ref(false)
const evidencePath = computed(() => ballotId.value.trim() ? '/api/quorum/ballots/' + encodeURIComponent(ballotId.value.trim()) : '/api/quorum/founding')
const evidenceUrl = computed(() => location.origin + evidencePath.value)

async function downloadEvidence() {
  signingError.value = ''
  try {
    const evidence = await api.get(evidencePath.value)
    downloadQuorumFile('quorum-evidence.json', JSON.stringify(evidence))
  } catch (cause) { signingError.value = (cause as Error).message }
}

async function submitSignature() {
  signing.value = true
  signingError.value = ''
  signingNotice.value = ''
  try {
    const response = await fetch(evidencePath.value + '/endorse', {
      method: 'POST', credentials: 'omit', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(JSON.parse(signedCheckpoint.value)),
    })
    if (!response.ok) {
      const result = await response.json()
      throw new Error(typeof result.detail === 'string' ? result.detail : result.detail?.message ?? 'The signature was rejected.')
    }
    signedCheckpoint.value = ''
    signingNotice.value = 'Witness signature recorded.'
  } catch (cause) { signingError.value = (cause as Error).message }
  finally { signing.value = false }
}

async function load() {
  error.value = ''
  try {
    await discord.load()
    if (discord.account) state.value = await api.get('/api/quorum/witness', { headers: discordHeaders })
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not load witness registration.'
  }
}
function downloadStatement() {
  if (!state.value) return
  const url = URL.createObjectURL(new Blob([state.value.statement], { type: 'text/plain' }))
  const link = document.createElement('a')
  link.href = url
  link.download = 'quorum-witness.txt'
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
async function register() {
  if (!state.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await api.post<{ public_key: string }>('/api/quorum/witness', {
      body: { public_key: publicKey.value.trim(), signature: signature.value.trim() }, headers: discordHeaders,
    })
    state.value.public_key = result.public_key
    publicKey.value = ''
    signature.value = ''
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not register this signing key.'
  } finally {
    busy.value = false
  }
}
onMounted(load)
</script>

<template>
  <div class="page-container page-container--narrow page-stack">
    <PageHeader title="Quorum witness signing" />
    <InlineFeedback :message="error" />
    <section v-if="discord.loaded && !discord.account" class="panel panel__body section-stack">
      <p>Connect the Discord account nominated to operate a witness.</p>
      <div v-if="discord.enabled" class="button-row">
        <BaseButton :href="discordLogin()" variant="primary">Connect Discord</BaseButton>
      </div>
      <p v-else class="muted">Discord sign-in is unavailable.</p>
    </section>
    <section v-else-if="state?.public_key" class="panel panel__body section-stack">
      <h2>Signing key registered</h2>
      <details><summary>View public key</summary><code>{{ state.public_key }}</code></details>
      <p class="muted">Use the matching private key on your independent host to sign the founding roster and ballot checkpoints.</p>
    </section>
    <form v-else-if="state" class="panel panel__body section-stack" @submit.prevent="register">
      <h2>Register your signing key</h2>
      <p>Download your enrollment statement and move it to your witness host. Generate a fresh key there and sign the statement exactly as downloaded.</p>
      <div class="button-row"><BaseButton @click="downloadStatement">Download statement</BaseButton></div>
      <pre class="witness-commands">openssl genpkey -algorithm ED25519 -out quorum-witness.pem
openssl pkey -in quorum-witness.pem -pubout -out quorum-witness.pub
openssl pkeyutl -sign -rawin -inkey quorum-witness.pem -in quorum-witness.txt -out quorum-witness.sig
openssl base64 -A -in quorum-witness.sig</pre>
      <p class="muted">Keep the private key on the witness host.</p>
      <label class="field">
        <span>Public key PEM</span>
        <textarea v-model="publicKey" class="textarea input--mono" maxlength="250" spellcheck="false" required />
        <small class="muted">Paste all of quorum-witness.pub, including the BEGIN PUBLIC KEY and END PUBLIC KEY lines.</small>
      </label>
      <label class="field">
        <span>Statement signature (Base64)</span>
        <input v-model="signature" class="input input--mono" maxlength="88" autocomplete="off" spellcheck="false" required>
        <small class="muted">Paste the single line printed by the final OpenSSL command.</small>
      </label>
      <div class="button-row"><BaseButton type="submit" variant="primary" :loading="busy">Register signing key</BaseButton></div>
    </form>
    <section class="panel panel__body section-stack">
      <div class="toolbar toolbar--between">
        <h2>{{ ballotId ? 'Ballot checkpoint' : 'Founding roster' }}</h2>
        <BaseButton v-if="ballotId" :to="{ name: 'quorum-ballot', params: { id: ballotId } }" size="small" variant="ghost">View ballot</BaseButton>
      </div>
      <p>Review the evidence and sign it on your independent witness host.</p>
      <div class="button-row"><BaseButton @click="downloadEvidence">Download evidence</BaseButton></div>
      <p class="muted">Run these commands from the frontend directory of a trusted COPE checkout with Node 24 and its npm dependencies installed.</p>
      <pre class="witness-commands">node quorum-witness.mjs inspect {{ evidenceUrl }}
node quorum-witness.mjs {{ ballotId.trim() ? 'sign' : 'sign-genesis' }} {{ evidenceUrl }} TRUSTED_GENESIS_SHA quorum-witness.pem witness-state.json{{ ballotId.trim() ? ' APPROVED_MANIFEST_SHA' : '' }}</pre>
      <p v-if="ballotId.trim()" class="muted">Sign the empty opening checkpoint first. Sign updates using the same state. Sign the final checkpoint after the deadline on your witness host.</p>
      <p class="muted">Add <code>--submit</code> to send the signature directly from your service, or paste the signed JSON below.</p>
      <details class="section-stack">
        <summary>Submit a signature manually</summary>
        <form class="section-stack" @submit.prevent="submitSignature">
          <label class="field"><span>Signed JSON from your witness host</span>
            <textarea v-model="signedCheckpoint" class="textarea input--mono" maxlength="2048" required />
          </label>
          <div class="button-row"><BaseButton type="submit" variant="primary" :loading="signing">Submit witness signature</BaseButton></div>
        </form>
      </details>
      <InlineFeedback :message="signingError" />
      <InlineFeedback :message="signingNotice" tone="info" />
    </section>
  </div>
</template>

<style scoped>
summary { cursor: pointer; }
details[open] > summary { margin-bottom: var(--space-3); }
.witness-commands {
  overflow-x: auto;
  border-radius: var(--radius-md);
  padding: var(--space-3);
  background: var(--color-surface-sunken);
}
</style>
