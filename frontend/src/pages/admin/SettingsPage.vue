<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api, setCsrfToken } from '@/api/client'
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

interface CopeBuildSettings {
  repository_url: string
  update_ref: string
}

interface PlatformSettings {
  privatise_platform: boolean
}

type AccessTokenRole = 'admin' | 'manager'

const toast = useToast()
const { confirm } = useConfirm()
const loading = ref(true)
const hostPending = ref<number | 'new' | null>(null)
const tokenPending = ref<`${AccessTokenRole}-${'copy' | 'rotate'}` | null>(null)
const tokenCopied = ref<AccessTokenRole | null>(null)
const canManageTokens = ref(false)
const error = ref('')
const hosts = ref<GitHost[]>([])
const buildPending = ref(false)
const copeBuild = reactive<CopeBuildSettings>({ repository_url: '', update_ref: 'main' })
const platformPending = ref(false)
const platform = reactive<PlatformSettings>({ privatise_platform: false })
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
    const response = await api.get<{ can_manage_tokens: boolean; cope_build: CopeBuildSettings; platform: PlatformSettings; git_hosts: GitHost[] }>('/api/admin/settings')
    canManageTokens.value = response.can_manage_tokens
    Object.assign(copeBuild, response.cope_build)
    Object.assign(platform, response.platform)
    hosts.value = response.git_hosts.map((host) => ({ ...host, access_token: '', clear_access_token: false }))
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    loading.value = false
  }
}

async function savePlatformSettings(): Promise<void> {
  platformPending.value = true
  error.value = ''
  try {
    const response = await api.put<{ platform: PlatformSettings; message: string }>('/api/admin/settings/platform', {
      body: { privatise_platform: platform.privatise_platform },
    })
    Object.assign(platform, response.platform)
    toast.success(response.message)
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    platformPending.value = false
  }
}

async function saveCopeBuild(): Promise<void> {
  buildPending.value = true
  error.value = ''
  try {
    const response = await api.put<{ cope_build: CopeBuildSettings; message: string }>('/api/admin/settings/cope-build', {
      body: {
        repository_url: copeBuild.repository_url.trim(),
        update_ref: copeBuild.update_ref.trim(),
      },
    })
    Object.assign(copeBuild, response.cope_build)
    toast.success(response.message)
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    buildPending.value = false
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

async function copyAccessToken(role: AccessTokenRole): Promise<void> {
  tokenPending.value = `${role}-copy`
  error.value = ''
  try {
    const response = await api.get<{ token: string }>(`/api/admin/settings/access-tokens/${role}`)
    if (!navigator.clipboard?.writeText) throw new Error('Clipboard access is not available.')
    await navigator.clipboard.writeText(response.token)
    tokenCopied.value = role
    toast.success(`${role === 'admin' ? 'Admin' : 'Manager'} token copied.`)
    window.setTimeout(() => { if (tokenCopied.value === role) tokenCopied.value = null }, 2000)
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    tokenPending.value = null
  }
}

async function rotateAccessToken(role: AccessTokenRole): Promise<void> {
  const label = role === 'admin' ? 'admin' : 'manager'
  const accepted = await confirm({
    title: `Rotate ${label} token?`,
    message: role === 'admin'
      ? 'The current admin token and other admin sessions will stop working immediately. This browser will stay signed in.'
      : 'The current manager token and all manager sessions will stop working immediately.',
    confirmLabel: 'Rotate token',
    tone: 'danger',
  })
  if (!accepted) return
  tokenPending.value = `${role}-rotate`
  error.value = ''
  try {
    const response = await api.post<{ csrf_token: string; message: string }>(`/api/admin/settings/access-tokens/${role}`)
    setCsrfToken(response.csrf_token)
    tokenCopied.value = null
    toast.success(response.message)
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    tokenPending.value = null
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
      <section class="build-section">
        <div class="section-heading"><div><h2>Cope build</h2><p>Choose the source repository and default branch or ref used when this environment checks for updates.</p></div></div>
        <form class="panel settings-card" @submit.prevent="saveCopeBuild">
          <div class="card-heading"><div><h3>Update source</h3><p>Repository changes take effect the next time an update starts.</p></div><span class="auth-status configured">Environment</span></div>
          <div class="form-grid build-grid">
            <label class="field"><span>Repository</span><input v-model="copeBuild.repository_url" class="input" required autocomplete="off" spellcheck="false" placeholder="https://github.com/organisation/cope-chess.git"><small>HTTPS, SSH, SCP-style Git URLs, and local deployment paths are supported.</small></label>
            <label class="field"><span>Default branch or ref</span><input v-model="copeBuild.update_ref" class="input" required autocomplete="off" spellcheck="false" placeholder="main"><small>Used by platform, web, and engine-definition updates unless a different ref is entered for that update.</small></label>
          </div>
          <div class="form-actions"><button class="button button--primary" type="submit" :disabled="buildPending">{{ buildPending ? 'Saving…' : 'Save build settings' }}</button></div>
        </form>
      </section>
      <section class="platform-section">
        <div class="section-heading"><div><h2>Platform access</h2><p>Control who can view the public-facing Cope site.</p></div></div>
        <form class="panel settings-card" @submit.prevent="savePlatformSettings">
          <div class="card-heading"><div><h3>Public access</h3><p>Authentication applies to public pages, downloads, APIs, and live updates.</p></div><span :class="['auth-status', { configured: platform.privatise_platform }]">{{ platform.privatise_platform ? 'Private' : 'Public' }}</span></div>
          <label class="privacy-toggle"><input v-model="platform.privatise_platform" type="checkbox"><span><strong>Privatise platform</strong><small>Require an admin or manager login before anyone can access a public page.</small></span></label>
          <div class="form-actions"><button class="button button--primary" type="submit" :disabled="platformPending">{{ platformPending ? 'Saving…' : 'Save platform access' }}</button></div>
        </form>
      </section>
      <section v-if="canManageTokens" class="token-section">
        <div class="section-heading"><div><h2>Access tokens</h2></div></div>
        <div class="token-grid">
          <article class="panel settings-card">
            <div class="card-heading"><div><h3>Admin token</h3><p>Full access, including these token controls.</p></div><span class="auth-status configured">Hidden</span></div>
            <div class="token-actions"><button class="button button--secondary" type="button" :disabled="tokenPending !== null" @click="copyAccessToken('admin')">{{ tokenPending === 'admin-copy' ? 'Copying…' : tokenCopied === 'admin' ? 'Copied' : 'Copy token' }}</button><button class="button button--danger" type="button" :disabled="tokenPending !== null" @click="rotateAccessToken('admin')">{{ tokenPending === 'admin-rotate' ? 'Rotating…' : 'Rotate token' }}</button></div>
          </article>
          <article class="panel settings-card">
            <div class="card-heading"><div><h3>Manager token</h3><p>Full admin access except viewing or rotating access tokens.</p></div><span class="auth-status configured">Hidden</span></div>
            <div class="token-actions"><button class="button button--secondary" type="button" :disabled="tokenPending !== null" @click="copyAccessToken('manager')">{{ tokenPending === 'manager-copy' ? 'Copying…' : tokenCopied === 'manager' ? 'Copied' : 'Copy token' }}</button><button class="button button--danger" type="button" :disabled="tokenPending !== null" @click="rotateAccessToken('manager')">{{ tokenPending === 'manager-rotate' ? 'Rotating…' : 'Rotate token' }}</button></div>
          </article>
        </div>
      </section>
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
.loading-card,.settings-card{padding:1rem}.settings-card{display:grid;gap:1rem}.card-heading,.section-heading{align-items:start;display:flex;gap:1rem;justify-content:space-between}.card-heading{border-bottom:1px solid var(--color-border);padding-bottom:.8rem}.card-heading h2,.section-heading h2,.card-heading h3{font-size:.95rem;margin:0}.card-heading p,.section-heading p{color:var(--color-text-muted);font-size:.7rem;margin:.2rem 0 0}.card-heading>span,.auth-status{background:var(--color-surface-subtle);border-radius:999px;color:var(--color-warning);font-size:.65rem;padding:.3rem .55rem;white-space:nowrap}.card-heading>span.configured,.auth-status.configured{color:var(--color-success)}.build-section,.platform-section,.token-section,.token-grid{display:grid;gap:.8rem}.build-grid{grid-template-columns:minmax(0,2fr) minmax(12rem,1fr)}.privacy-toggle{align-items:flex-start;cursor:pointer;display:flex;gap:.7rem}.privacy-toggle input{margin-top:.18rem}.privacy-toggle span{display:grid;gap:.25rem}.privacy-toggle strong{font-size:.8rem}.privacy-toggle small{color:var(--color-text-muted);font-size:.7rem;line-height:1.45}.token-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.token-actions{display:flex;gap:.5rem;justify-content:flex-end}.form-grid{display:grid;gap:.85rem;grid-template-columns:repeat(2,minmax(0,1fr))}.field{display:grid;gap:.38rem}.field>span{font-size:.76rem;font-weight:650}.field small{color:var(--color-text-muted);font-size:.67rem;line-height:1.4}.token-field{align-content:start}.switch-row{align-items:center;cursor:pointer;display:flex;gap:.5rem}.switch-row strong{font-size:.73rem}.host-heading-actions{align-items:center;display:flex;gap:.7rem}.form-actions{display:flex;justify-content:flex-end}.form-actions.split{justify-content:space-between}.host-section,.host-grid-list{display:grid;gap:.8rem}.host-grid-list{grid-template-columns:repeat(auto-fill,minmax(min(100%,28rem),1fr))}.host-grid{grid-template-columns:repeat(2,minmax(0,1fr))}@media(max-width:42rem){.card-heading,.section-heading{align-items:stretch;flex-direction:column}.host-heading-actions{justify-content:space-between}.token-grid{grid-template-columns:1fr}.token-actions{align-items:stretch;flex-direction:column}.form-grid,.build-grid,.host-grid{grid-template-columns:1fr}}
</style>
