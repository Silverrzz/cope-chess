<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api/client'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import { discordHeaders, discordLogin, useDiscordSession } from '@/stores/discord'

const discord = useDiscordSession()
const state = ref<{ statement: string; public_key: string | null }>()
const publicKey = ref('')
const signature = ref('')
const error = ref('')
const busy = ref(false)

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
    <PageHeader title="Witness key registration" description="Register an Ed25519 key generated on your independent witness host." />
    <InlineFeedback :message="error" />
    <section v-if="discord.loaded && !discord.account" class="panel panel__body section-stack">
      <p>Connect the Discord account nominated to operate a witness.</p>
      <div v-if="discord.enabled" class="button-row">
        <BaseButton :href="discordLogin('/quorum/witness')" variant="primary">Connect Discord</BaseButton>
      </div>
      <p v-else class="muted">Discord sign-in is unavailable.</p>
    </section>
    <section v-else-if="state?.public_key" class="panel panel__body section-stack">
      <h2>Signing key registered</h2>
      <code>{{ state.public_key }}</code>
      <p class="muted">This key is registered for setup. Checkpoint signing is pending.</p>
    </section>
    <form v-else-if="state" class="panel panel__body section-stack" @submit.prevent="register">
      <p>Download your enrollment statement and move it to your witness host. Generate a fresh key there and sign the statement exactly as downloaded.</p>
      <div class="button-row"><BaseButton @click="downloadStatement">Download statement</BaseButton></div>
      <pre class="witness-commands">openssl genpkey -algorithm ED25519 -out quorum-witness.pem
openssl pkey -in quorum-witness.pem -pubout -out quorum-witness.pub
openssl pkeyutl -sign -rawin -inkey quorum-witness.pem -in quorum-witness.txt | openssl base64 -A</pre>
      <p class="muted">Keep the private key on the witness host.</p>
      <label class="field"><span>Public key PEM</span><textarea v-model="publicKey" class="textarea input--mono" maxlength="250" required /></label>
      <label class="field"><span>Statement signature (base64)</span><input v-model="signature" class="input input--mono" maxlength="88" required></label>
      <div class="button-row"><BaseButton type="submit" variant="primary" :loading="busy">Register signing key</BaseButton></div>
    </form>
  </div>
</template>

<style scoped>
.witness-commands {
  overflow-x: auto;
  border-radius: var(--radius-md);
  padding: var(--space-3);
  background: var(--color-surface-sunken);
}
</style>
