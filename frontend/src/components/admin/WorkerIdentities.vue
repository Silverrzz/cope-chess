<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '@/api/client'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import { errorText } from '@/components/admin/format'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'

interface Identity {
  id: number
  label: string
  donor_name: string
  connected: number
  revoked: boolean
}

const endpoint = '/api/admin/workers/identities'
const installer = `curl -fsSL ${window.location.origin}/install-worker.sh | sh -s -- --server ${window.location.origin}`
const { confirm } = useConfirm()
const toast = useToast()
const identities = ref<Identity[]>([])
const editing = ref<number | null>(null)
const label = ref('')
const donorName = ref('')
const token = ref('')
const error = ref('')
const busy = ref(false)

async function load(): Promise<void> {
  identities.value = (await api.get<{ identities: Identity[] }>(endpoint)).identities
}

function edit(identity?: Identity): void {
  editing.value = identity?.id ?? 0
  label.value = identity?.label ?? ''
  donorName.value = identity?.donor_name ?? ''
  token.value = ''
}

async function save(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    const body = { label: label.value.trim(), donor_name: donorName.value.trim() }
    if (editing.value) {
      await api.put(`${endpoint}/${editing.value}`, { body })
    } else {
      token.value = (await api.post<{ token: string }>(endpoint, { body })).token
    }
    editing.value = null
    await load()
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}

async function changeToken(identity: Identity, action: 'rotate' | 'revoke'): Promise<void> {
  const rotate = action === 'rotate'
  const accepted = await confirm({
    title: rotate ? 'Rotate identity token?' : 'Revoke worker identity?',
    message: `Every machine using “${identity.label}” will disconnect and unfinished work will return to Cope.`,
    confirmLabel: rotate ? 'Rotate token' : 'Revoke identity',
    tone: 'danger',
  })
  if (!accepted) return
  busy.value = true
  error.value = ''
  try {
    token.value = (await api.post<{ token: string | null }>(`${endpoint}/${identity.id}/${action}`)).token ?? ''
    await load()
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}

async function copyToken(): Promise<void> {
  try {
    await navigator.clipboard.writeText(token.value)
    toast.success('Identity token copied.')
  } catch (cause) {
    toast.error(cause)
  }
}

onMounted(async () => {
  try {
    await load()
  } catch (cause) {
    error.value = errorText(cause)
  }
})
</script>

<template>
  <section class="panel identities" aria-labelledby="worker-identities-title">
    <header>
      <h2 id="worker-identities-title">Worker identities</h2>
      <button class="button button--primary button--small" type="button" :disabled="busy" @click="edit()">Create identity</button>
    </header>
    <InlineFeedback :message="error" />
    <form v-if="editing !== null" class="identity-form" @submit.prevent="save">
      <label>Identity name<input v-model="label" class="input" required maxlength="80"></label>
      <label>Donor group<input v-model="donorName" class="input" required maxlength="80" list="worker-donor-groups"></label>
      <datalist id="worker-donor-groups">
        <option v-for="name in [...new Set(identities.map(identity => identity.donor_name))]" :key="name" :value="name" />
      </datalist>
      <div class="identity-actions">
        <button class="button button--ghost button--small" type="button" @click="editing = null">Cancel</button>
        <button class="button button--primary button--small" type="submit" :disabled="busy">Save identity</button>
      </div>
    </form>
    <div v-if="token" class="identity-token">
      <p>Save this token to a private file. Use the same file on each installation of this identity. The token is shown once.</p>
      <code>{{ token }}</code>
      <p>New installations can paste the token when prompted by the installer.</p>
      <code>{{ installer }}</code>
      <p>Use <code>cope config --token-file /path/to/token</code> on existing installations, then run <code>cope start</code>.</p>
      <div class="identity-actions">
        <button class="button button--secondary button--small" type="button" @click="copyToken">Copy token</button>
        <button class="button button--ghost button--small" type="button" @click="token = ''">Done</button>
      </div>
    </div>
    <ul v-if="identities.length" class="identity-list">
      <li v-for="identity in identities" :key="identity.id">
        <div><strong>{{ identity.label }}</strong><small>{{ identity.donor_name }} · {{ identity.revoked ? 'Revoked' : `${identity.connected} connected` }}</small></div>
        <div v-if="!identity.revoked" class="identity-actions">
          <button class="button button--ghost button--small" type="button" :disabled="busy" @click="edit(identity)">Edit</button>
          <button class="button button--secondary button--small" type="button" :disabled="busy" @click="changeToken(identity, 'rotate')">Rotate token</button>
          <button class="button button--danger button--small" type="button" :disabled="busy" @click="changeToken(identity, 'revoke')">Revoke</button>
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.identities { display: grid; gap: .8rem; padding: 1rem; }
.identities header, .identity-list li { display: flex; justify-content: space-between; align-items: center; gap: .75rem; flex-wrap: wrap; }
.identities h2 { margin: 0; font-size: .95rem; }
.identity-list { list-style: none; padding: 0; margin: 0; }
.identity-list li { border-top: 1px solid var(--stroke); padding: .65rem 0; }
.identity-list small { display: block; }
.identity-form, .identity-actions { display: flex; gap: .5rem; align-items: flex-end; flex-wrap: wrap; }
.identity-form label { display: grid; gap: .2rem; }
.identity-token { display: grid; gap: .5rem; overflow-wrap: anywhere; }
.identity-token p { margin: 0; }
</style>
