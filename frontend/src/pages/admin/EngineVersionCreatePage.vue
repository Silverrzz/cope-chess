<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useToast } from '@/composables/useToast'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import DockerfilePicker from '@/components/admin/DockerfilePicker.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import { errorText, formatNumber } from '@/components/admin/format'

interface Repository {
  host_id: number
  host_name: string
  provider: string
  full_name: string
  name: string
  owner: string
  description: string
  web_url: string
  repository_url: string
  default_branch: string
  stars: number
}

interface Release {
  tag: string
  name: string
  published_at: string
}

interface GitHost {
  id: number
  name: string
  provider: string
}

interface DockerfileEntry {
  path: string
  size: number
}

const route = useRoute()
const router = useRouter()
const toast = useToast()
const engineId = computed(() => Number(route.params.id))
const query = ref('')
const gitHosts = ref<GitHost[]>([])
const selectedHostIds = ref<number[]>([])
const repositories = ref<Repository[]>([])
const selected = ref<Repository | null>(null)
const releases = ref<Release[]>([])
const sourceKind = ref<'release' | 'commit'>('release')
const sourceRef = ref('')
const version = ref('')
const dockerfiles = ref<DockerfileEntry[]>([])
const dockerfilePath = ref('')
const dockerfileContent = ref('')
const searching = ref(false)
const loadingReleases = ref(false)
const loadingDockerfile = ref(false)
const creating = ref(false)
const error = ref('')

async function loadGitHosts(): Promise<void> {
  try {
    const response = await api.get<{ git_hosts: GitHost[] }>('/api/admin/git-hosts')
    gitHosts.value = response.git_hosts
    selectedHostIds.value = response.git_hosts.map((host) => host.id)
  } catch (cause) {
    error.value = errorText(cause)
  }
}

async function loadDockerfiles(): Promise<void> {
  try {
    const response = await api.get<{ dockerfiles: DockerfileEntry[] }>('/api/admin/engine-dockerfiles')
    dockerfiles.value = response.dockerfiles
    if (response.dockerfiles.length) {
      dockerfilePath.value = response.dockerfiles[0]?.path ?? ''
      await loadDockerfile()
    }
  } catch (cause) {
    error.value = errorText(cause)
  }
}

async function loadDockerfile(): Promise<void> {
  if (!dockerfilePath.value) {
    dockerfileContent.value = ''
    return
  }
  loadingDockerfile.value = true
  error.value = ''
  try {
    const response = await api.get<{ content: string }>('/api/admin/engine-dockerfiles/content', {
      query: { path: dockerfilePath.value },
    })
    dockerfileContent.value = response.content
  } catch (cause) {
    dockerfileContent.value = ''
    error.value = errorText(cause)
  } finally {
    loadingDockerfile.value = false
  }
}

onMounted(() => {
  void Promise.all([loadGitHosts(), loadDockerfiles()])
})

async function search(): Promise<void> {
  if (query.value.trim().length < 2) {
    error.value = 'Enter at least two characters.'
    return
  }
  if (!selectedHostIds.value.length) {
    error.value = 'Select at least one Git host.'
    return
  }
  searching.value = true
  error.value = ''
  selected.value = null
  try {
    const response = await api.get<{ repositories: Repository[] }>('/api/admin/repositories/search', {
      query: { q: query.value.trim(), host_id: selectedHostIds.value },
    })
    repositories.value = response.repositories
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    searching.value = false
  }
}

async function selectRepository(repository: Repository): Promise<void> {
  selected.value = repository
  releases.value = []
  sourceRef.value = ''
  version.value = ''
  loadingReleases.value = true
  error.value = ''
  try {
    const response = await api.get<{ releases: Release[] }>('/api/admin/repositories/releases', {
      query: { host_id: repository.host_id, full_name: repository.full_name },
    })
    releases.value = response.releases
    const firstRelease = releases.value[0]
    if (firstRelease) {
      sourceKind.value = 'release'
      sourceRef.value = firstRelease.tag
      version.value = firstRelease.name || firstRelease.tag
    } else {
      sourceKind.value = 'commit'
    }
  } catch (cause) {
    sourceKind.value = 'commit'
    error.value = errorText(cause)
  } finally {
    loadingReleases.value = false
  }
}

function chooseRelease(): void {
  const release = releases.value.find((item) => item.tag === sourceRef.value)
  if (release && !version.value.trim()) version.value = release.name || release.tag
}

async function create(): Promise<void> {
  if (!selected.value) {
    error.value = 'Choose a repository.'
    return
  }
  if (!version.value.trim() || !sourceRef.value.trim()) {
    error.value = 'Enter a version label and source reference.'
    return
  }
  if (!dockerfilePath.value) {
    error.value = 'Choose a Dockerfile from data/engines.'
    return
  }
  creating.value = true
  error.value = ''
  try {
    const response = await api.post<{ id: number; message: string }>(`/api/admin/engines/${engineId.value}/versions`, {
      body: {
        version: version.value.trim(),
        git_host_id: selected.value.host_id,
        repository_full_name: selected.value.full_name,
        source_ref: sourceRef.value.trim(),
        source_kind: sourceKind.value,
        dockerfile_path: dockerfilePath.value,
        uci_options: {},
      },
    })
    toast.success(response.message)
    await router.push(`/admin/engine-versions/${response.id}`)
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <div class="admin-page page-stack">
    <AdminPageHeader title="New engine version">
      <template #actions><RouterLink class="button button--ghost" :to="`/admin/engines/${engineId}`">Back to engine</RouterLink></template>
    </AdminPageHeader>
    <InlineFeedback :message="error" />

    <section class="panel search-panel">
      <div><h2>Find the source repository</h2><p>Choose which enabled Git hosts COPE should search.</p></div>
      <form class="search-form" @submit.prevent="search">
        <input v-model="query" class="input" type="search" placeholder="Stockfish or Silverrzz/sable" autofocus>
        <button class="button button--primary" type="submit" :disabled="searching">{{ searching ? 'Searching…' : 'Search' }}</button>
      </form>
      <fieldset class="host-filter">
        <legend>Git hosts</legend>
        <div class="host-grid">
          <label v-for="host in gitHosts" :key="host.id" :class="{ selected: selectedHostIds.includes(host.id) }">
            <input v-model="selectedHostIds" type="checkbox" :value="host.id">
            <span><strong>{{ host.name }}</strong><small>{{ host.provider }}</small></span>
          </label>
          <p v-if="!gitHosts.length" class="host-empty">No enabled Git hosts are configured.</p>
        </div>
      </fieldset>
    </section>

    <section v-if="repositories.length && !selected" class="result-section">
      <div class="section-heading"><h2>Repositories</h2><span>{{ repositories.length }} results</span></div>
      <div class="repository-grid">
        <button v-for="repository in repositories" :key="`${repository.host_id}:${repository.full_name}`" class="panel repository-card" type="button" @click="selectRepository(repository)">
          <div><span class="host">{{ repository.host_name }}</span><span class="stars">★ {{ formatNumber(repository.stars) }}</span></div>
          <h3>{{ repository.full_name }}</h3>
          <p>{{ repository.description || 'No description provided.' }}</p>
          <span class="select-label">Select repository →</span>
        </button>
      </div>
    </section>

    <form v-if="selected" class="panel source-panel" @submit.prevent="create">
      <div class="selected-repository">
        <div><span>{{ selected.host_name }}</span><h2>{{ selected.full_name }}</h2><p>{{ selected.description }}</p></div>
        <button class="button button--ghost button--small" type="button" @click="selected = null">Change</button>
      </div>
      <div v-if="loadingReleases" class="loading-row">Loading public releases…</div>
      <template v-else>
        <div class="source-choice">
          <label :class="{ selected: sourceKind === 'release' }"><input v-model="sourceKind" type="radio" value="release" :disabled="!releases.length"><span><strong>Public release</strong><small>{{ releases.length ? `${releases.length} releases available` : 'No public releases found' }}</small></span></label>
          <label :class="{ selected: sourceKind === 'commit' }"><input v-model="sourceKind" type="radio" value="commit"><span><strong>Commit hash</strong><small>Build an exact commit from the repository</small></span></label>
        </div>
        <div class="form-grid">
          <label v-if="sourceKind === 'release'" class="field"><span>Release</span><select v-model="sourceRef" class="input" required @change="chooseRelease"><option v-for="release in releases" :key="release.tag" :value="release.tag">{{ release.name }} ({{ release.tag }})</option></select></label>
          <label v-else class="field"><span>Commit hash</span><input v-model="sourceRef" class="input" required maxlength="64" placeholder="e7f92b8…"></label>
          <label class="field"><span>Version label</span><input v-model="version" class="input" required maxlength="80" placeholder="17.1"></label>
          <div class="field form-span-full"><span>Dockerfile</span><DockerfilePicker v-model="dockerfilePath" :files="dockerfiles" @change="loadDockerfile" /><small>Managed in the repository under <code>data/engines</code>.</small></div>
          <details v-if="dockerfileContent || loadingDockerfile" class="dockerfile-preview form-span-full">
            <summary>Preview Dockerfile</summary>
            <pre class="dockerfile-viewer" tabindex="0">{{ loadingDockerfile ? 'Loading Dockerfile…' : dockerfileContent }}</pre>
          </details>
          <p class="activation-note">The version becomes active automatically after its current build completes a benchmark.</p>
        </div>
        <div class="form-actions"><button class="button button--primary" type="submit" :disabled="creating">{{ creating ? 'Creating…' : 'Create version' }}</button></div>
      </template>
    </form>
  </div>
</template>

<style scoped>
.search-panel,.source-panel{display:grid;gap:1rem;padding:1.1rem}.search-panel h2,.source-panel h2,.section-heading h2{font-size:.95rem;margin:0}.search-panel p,.selected-repository p{color:var(--color-text-muted);font-size:.72rem;margin:.2rem 0 0}.search-form{display:flex;gap:.6rem}.search-form .input{flex:1}.result-section{display:grid;gap:.75rem}.section-heading{align-items:center;display:flex;justify-content:space-between}.section-heading span{color:var(--color-text-muted);font-size:.7rem}.repository-grid{display:grid;gap:.75rem;grid-template-columns:repeat(auto-fill,minmax(min(100%,20rem),1fr))}.repository-card{color:inherit;cursor:pointer;display:grid;gap:.6rem;padding:1rem;text-align:left}.repository-card:hover{border-color:var(--color-accent)}.repository-card>div{display:flex;justify-content:space-between}.host,.stars,.select-label{color:var(--color-text-muted);font-size:.67rem}.repository-card h3{font-size:.85rem;margin:0}.repository-card p{color:var(--color-text-muted);display:-webkit-box;font-size:.7rem;line-height:1.4;margin:0;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:2}.select-label{color:var(--color-accent);font-weight:650}.selected-repository{align-items:start;border-bottom:1px solid var(--color-border);display:flex;gap:1rem;justify-content:space-between;padding-bottom:1rem}.selected-repository span{color:var(--color-accent);font-size:.65rem;font-weight:650}.source-choice{display:grid;gap:.65rem;grid-template-columns:repeat(2,minmax(0,1fr))}.source-choice label{border:1px solid var(--color-border);border-radius:var(--radius-md);cursor:pointer;display:flex;gap:.6rem;padding:.8rem}.source-choice label.selected{border-color:var(--color-accent);background:var(--color-accent-soft)}.source-choice span,.switch-row span{display:grid}.source-choice strong,.switch-row strong{font-size:.78rem}.source-choice small,.switch-row small{color:var(--color-text-muted);font-size:.67rem}.form-grid{display:grid;gap:.85rem;grid-template-columns:repeat(2,minmax(0,1fr))}.field{display:grid;gap:.38rem}.field>span{font-size:.76rem;font-weight:650}.switch-row{align-items:center;cursor:pointer;display:flex;gap:.6rem;grid-column:1/-1}.form-actions{display:flex;justify-content:flex-end}.loading-row{color:var(--color-text-muted);font-size:.75rem;padding:.75rem 0}@media(max-width:40rem){.search-form,.selected-repository{align-items:stretch;flex-direction:column}.source-choice,.form-grid{grid-template-columns:1fr}.switch-row{grid-column:auto}}
.host-filter{border:0;margin:0;min-width:0;padding:0}.host-filter legend{font-size:.72rem;font-weight:650;margin-bottom:.5rem;padding:0}.host-grid{display:grid;gap:.55rem;grid-template-columns:repeat(auto-fit,minmax(10rem,1fr))}.host-grid label{align-items:center;border:1px solid var(--color-border);border-radius:var(--radius-md);cursor:pointer;display:flex;gap:.55rem;padding:.65rem .7rem}.host-grid label.selected{background:var(--color-accent-soft);border-color:var(--color-accent)}.host-grid label>span{display:grid;gap:.1rem}.host-grid strong{font-size:.73rem}.host-grid small{color:var(--color-text-muted);font-size:.63rem;text-transform:capitalize}.host-empty{grid-column:1/-1!important;margin:0!important}
.form-span-full{grid-column:1/-1}.field small{color:var(--color-text-muted);font-size:.67rem}.dockerfile-viewer{background:#0f172a;border-radius:var(--radius-md);color:#e2e8f0;font-family:var(--font-mono);font-size:.68rem;line-height:1.5;margin:0;max-height:24rem;overflow:auto;padding:.8rem;tab-size:2;white-space:pre}
.activation-note{color:var(--color-text-muted);font-size:.72rem;grid-column:1/-1;margin:0}
.dockerfile-preview{border-top:1px solid var(--color-border);padding-top:.75rem}.dockerfile-preview summary{cursor:pointer;font-size:.73rem;font-weight:650}.dockerfile-preview[open] summary{margin-bottom:.75rem}
</style>
