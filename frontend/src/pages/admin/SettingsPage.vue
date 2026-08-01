<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api } from '@/api/client'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import { errorText } from '@/components/admin/format'

interface GitHost {
  id: number
  name: string
  provider: 'github' | 'gitlab'
  base_url: string
  api_url: string
  access_token_configured: boolean
  access_token?: string
  clear_access_token?: boolean
  enabled: boolean
}

const toast = useToast()
const { confirm } = useConfirm()
const loading = ref(true)
const hostPending = ref<number | 'new' | null>(null)
const error = ref('')
const hosts = ref<GitHost[]>([])
const showNewHost = ref(false)
const newHost = reactive<GitHost>({
  id: 0,
  name: '',
  provider: 'github',
  base_url: 'https://github.com',
  api_url: 'https://api.github.com',
  enabled: true,
  access_token_configured: false,
  access_token: '',
})

async function load(): Promise<void> {
  loading.value = true
  try {
    const response = await api.get<{ git_hosts: GitHost[] }>('/api/admin/settings')
    hosts.value = response.git_hosts.map((host) => ({ ...host, access_token: '', clear_access_token: false }))
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    loading.value = false
  }
}

function hostBody(host: GitHost): object {
  const accessToken = host.access_token?.trim() || null
  return {
    name: host.name.trim(),
    provider: host.provider,
    base_url: host.base_url.trim(),
    api_url: host.api_url.trim(),
    access_token: accessToken,
    clear_access_token: accessToken ? false : Boolean(host.clear_access_token),
    enabled: host.enabled,
  }
}

async function saveHost(host: GitHost): Promise<void> {
  hostPending.value = host.id
  error.value = ''
  try {
    const response = await api.put<{ message: string }>(`/api/admin/git-hosts/${host.id}`, { body: hostBody(host) })
    toast.success(response.message)
    await load()
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    hostPending.value = null
  }
}

async function addHost(): Promise<void> {
  hostPending.value = 'new'
  error.value = ''
  try {
    const response = await api.post<{ message: string }>('/api/admin/git-hosts', { body: hostBody(newHost) })
    toast.success(response.message)
    showNewHost.value = false
    Object.assign(newHost, {
      name: '',
      provider: 'github',
      base_url: 'https://github.com',
      api_url: 'https://api.github.com',
      access_token: '',
      enabled: true,
    })
    await load()
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    hostPending.value = null
  }
}

async function removeHost(host: GitHost): Promise<void> {
  if (!await confirm({ title: 'Delete Git host?', message: `Delete “${host.name}”?`, confirmLabel: 'Delete host', tone: 'danger' })) return
  hostPending.value = host.id
  try {
    const response = await api.delete<{ message: string }>(`/api/admin/git-hosts/${host.id}`)
    toast.success(response.message)
    await load()
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    hostPending.value = null
  }
}

function applyProviderDefaults(host: GitHost): void {
  if (host.provider === 'github') {
    host.base_url = 'https://github.com'
    host.api_url = 'https://api.github.com'
  } else {
    host.base_url = 'https://gitlab.com'
    host.api_url = 'https://gitlab.com/api/v4'
  }
}

onMounted(load)
</script>

<template>
  <div class="admin-page page-stack">
    <AdminPageHeader title="Settings" />
    <InlineFeedback :message="error" />
    <div v-if="loading" class="panel loading-card">Loading settings…</div>
    <template v-else>
      <section class="host-section">
        <div class="section-heading"><div><h2>Git hosts</h2><p>Add an API token to authenticate repository searches and avoid public rate limits.</p></div><button class="button button--primary button--small" type="button" @click="showNewHost = !showNewHost">{{ showNewHost ? 'Cancel' : 'Add Git host' }}</button></div>
        <form v-if="showNewHost" class="panel settings-card" @submit.prevent="addHost">
          <div class="form-grid host-grid">
            <label class="field"><span>Name</span><input v-model="newHost.name" class="input" required placeholder="Company GitHub"></label>
            <label class="field"><span>Provider</span><select v-model="newHost.provider" class="input" @change="applyProviderDefaults(newHost)"><option value="github">GitHub</option><option value="gitlab">GitLab</option></select></label>
            <label class="field"><span>Web URL</span><input v-model="newHost.base_url" class="input" type="url" required></label>
            <label class="field"><span>API URL</span><input v-model="newHost.api_url" class="input" type="url" required></label>
            <label class="field token-field"><span>API access token <small>recommended</small></span><input v-model="newHost.access_token" class="input" type="password" autocomplete="new-password" :placeholder="newHost.provider === 'github' ? 'github_pat_…' : 'glpat-…'"><small>{{ newHost.provider === 'github' ? 'A personal access token authenticates public GitHub API requests. Grant only the repository permissions Cope needs.' : 'Used as the PRIVATE-TOKEN header for GitLab API requests.' }}</small></label>
            <label class="switch-row"><input v-model="newHost.enabled" type="checkbox"><span><strong>Enabled</strong></span></label>
          </div>
          <div class="form-actions"><button class="button button--primary" type="submit" :disabled="hostPending === 'new'">{{ hostPending === 'new' ? 'Adding…' : 'Add host' }}</button></div>
        </form>
        <div class="host-grid-list">
          <form v-for="host in hosts" :key="host.id" class="panel settings-card" @submit.prevent="saveHost(host)">
            <div class="card-heading"><div><h3>{{ host.name }}</h3><p>{{ host.provider === 'github' ? 'GitHub' : 'GitLab' }} · {{ host.base_url }}</p></div><div class="host-heading-actions"><span :class="['auth-status', { configured: host.access_token_configured }]">{{ host.access_token_configured ? 'Token configured' : 'Unauthenticated' }}</span><label class="switch-row"><input v-model="host.enabled" type="checkbox"><span><strong>Enabled</strong></span></label></div></div>
            <div class="form-grid host-grid">
              <label class="field"><span>Name</span><input v-model="host.name" class="input" required></label>
              <label class="field"><span>Provider</span><select v-model="host.provider" class="input"><option value="github">GitHub</option><option value="gitlab">GitLab</option></select></label>
              <label class="field"><span>Web URL</span><input v-model="host.base_url" class="input" type="url" required></label>
              <label class="field"><span>API URL</span><input v-model="host.api_url" class="input" type="url" required></label>
              <label class="field token-field"><span>API access token</span><input v-model="host.access_token" class="input" type="password" autocomplete="new-password" :placeholder="host.access_token_configured ? 'Leave blank to keep current token' : (host.provider === 'github' ? 'github_pat_…' : 'glpat-…')"><small>{{ host.provider === 'github' ? 'Saved tokens are sent as bearer authentication on every GitHub API request.' : 'Saved tokens are sent in the PRIVATE-TOKEN header on every GitLab API request.' }}</small></label>
              <label v-if="host.access_token_configured" class="switch-row"><input v-model="host.clear_access_token" type="checkbox"><span><strong>Remove token</strong></span></label>
            </div>
            <div class="form-actions split"><button class="button button--danger" type="button" :disabled="hostPending === host.id" @click="removeHost(host)">Delete</button><button class="button button--secondary" type="submit" :disabled="hostPending === host.id">{{ hostPending === host.id ? 'Saving…' : 'Save host' }}</button></div>
          </form>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.loading-card,.settings-card{padding:1rem}.settings-card{display:grid;gap:1rem}.card-heading,.section-heading{align-items:start;display:flex;gap:1rem;justify-content:space-between}.card-heading{border-bottom:1px solid var(--color-border);padding-bottom:.8rem}.card-heading h2,.section-heading h2,.card-heading h3{font-size:.95rem;margin:0}.card-heading p,.section-heading p{color:var(--color-text-muted);font-size:.7rem;margin:.2rem 0 0}.card-heading>span,.auth-status{background:var(--color-surface-subtle);border-radius:999px;color:var(--color-warning);font-size:.65rem;padding:.3rem .55rem;white-space:nowrap}.card-heading>span.configured,.auth-status.configured{color:var(--color-success)}.form-grid{display:grid;gap:.85rem;grid-template-columns:repeat(2,minmax(0,1fr))}.field{display:grid;gap:.38rem}.field>span{font-size:.76rem;font-weight:650}.field small{color:var(--color-text-muted);font-size:.67rem;line-height:1.4}.token-field{align-content:start}.switch-row{align-items:center;cursor:pointer;display:flex;gap:.5rem}.switch-row strong{font-size:.73rem}.host-heading-actions{align-items:center;display:flex;gap:.7rem}.form-actions{display:flex;justify-content:flex-end}.form-actions.split{justify-content:space-between}.host-section,.host-grid-list{display:grid;gap:.8rem}.host-grid-list{grid-template-columns:repeat(auto-fill,minmax(min(100%,28rem),1fr))}.host-grid{grid-template-columns:repeat(2,minmax(0,1fr))}@media(max-width:42rem){.card-heading,.section-heading{align-items:stretch;flex-direction:column}.host-heading-actions{justify-content:space-between}.form-grid,.host-grid{grid-template-columns:1fr}}
</style>
