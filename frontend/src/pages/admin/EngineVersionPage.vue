<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import EngineOptionsEditor from '@/components/admin/EngineOptionsEditor.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import { errorText, formatDate } from '@/components/admin/format'
import type { Engine } from '@/components/admin/types'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const { confirm } = useConfirm()
const id = computed(() => Number(route.params.versionId))
const version = ref<Engine | null>(null)
const loading = ref(true)
const saving = ref(false)
const generating = ref(false)
const deleting = ref(false)
const error = ref('')

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const response = await api.get<{ version: Engine }>(`/api/admin/engine-versions/${id.value}`)
    version.value = response.version
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  if (!version.value) return
  saving.value = true
  error.value = ''
  try {
    const response = await api.put<{ message: string }>(`/api/admin/engine-versions/${id.value}`, {
      body: {
        version: version.value.version.trim(),
        dockerfile: version.value.dockerfile,
        uci_options: version.value.uci_options,
        active: version.value.active,
      },
    })
    toast.success(response.message)
    await load()
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    saving.value = false
  }
}

async function generate(): Promise<void> {
  if (!version.value) return
  generating.value = true
  error.value = ''
  try {
    const response = await api.post<{ dockerfile: string; model: string }>(`/api/admin/engine-versions/${id.value}/generate-dockerfile`)
    version.value.dockerfile = response.dockerfile
    toast.success(`Dockerfile generated with ${response.model}. Review it, then save.`)
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    generating.value = false
  }
}

async function remove(): Promise<void> {
  if (!version.value) return
  if (!await confirm({ title: 'Delete engine version?', message: `Delete ${version.value.name} ${version.value.version}?`, confirmLabel: 'Delete version', tone: 'danger' })) return
  deleting.value = true
  try {
    const response = await api.delete<{ message: string }>(`/api/admin/engine-versions/${id.value}`)
    toast.success(response.message)
    await router.push(`/admin/engines/${version.value.engine_id}`)
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    deleting.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="admin-page page-stack">
    <AdminPageHeader :title="version ? `${version.name} ${version.version}` : 'Engine version'">
      <template #actions><RouterLink v-if="version" class="button button--ghost" :to="`/admin/engines/${version.engine_id}`">Back to engine</RouterLink></template>
    </AdminPageHeader>
    <InlineFeedback :message="error" />
    <div v-if="loading" class="panel loading-card">Loading version…</div>
    <form v-else-if="version" class="page-stack" @submit.prevent="save">
      <section class="panel detail-card">
        <div class="detail-heading"><div><h2>Source</h2><p>COPE checks out this exact source before building on each worker.</p></div><label class="switch-row"><input v-model="version.active" type="checkbox"><span><strong>Active</strong></span></label></div>
        <div class="form-grid">
          <label class="field"><span>Version label</span><input v-model="version.version" class="input" required maxlength="80"></label>
          <div class="field"><span>Repository</span><a class="readonly-value" :href="version.repository_url.replace(/\.git$/, '')" target="_blank" rel="noopener">{{ version.repository_full_name }}</a></div>
          <div class="field"><span>{{ version.source_kind === 'release' ? 'Release' : 'Commit' }}</span><code class="readonly-value">{{ version.source_ref }}</code></div>
          <div class="field"><span>Build cache key</span><code class="readonly-value" :title="version.build_hash">{{ version.build_hash }}</code></div>
          <div class="field"><span>Created</span><span class="readonly-value">{{ formatDate(version.created_at) }}</span></div>
        </div>
      </section>

      <section class="panel detail-card">
        <div class="detail-heading"><div><h2>Dockerfile</h2><p>The image must provide an executable at <code>/opt/cope/engine</code> with <code>ENTRYPOINT ["./engine"]</code>.</p></div><button class="button button--secondary" type="button" :disabled="generating" @click="generate">{{ generating ? 'Generating…' : 'Generate with AI' }}</button></div>
        <textarea v-model="version.dockerfile" class="input dockerfile-editor" required spellcheck="false" aria-label="Dockerfile" />
      </section>

      <section class="panel detail-card">
        <div class="detail-heading"><div><h2>Default UCI options</h2><p>Applied whenever this version starts unless a tournament overrides them.</p></div></div>
        <EngineOptionsEditor v-model="version.uci_options" />
      </section>

      <div class="form-actions">
        <button class="button button--danger" type="button" :disabled="deleting || saving" @click="remove">{{ deleting ? 'Deleting…' : 'Delete version' }}</button>
        <button class="button button--primary" type="submit" :disabled="saving || deleting">{{ saving ? 'Saving…' : 'Save version' }}</button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.loading-card,.detail-card{padding:1rem}.detail-card{display:grid;gap:1rem}.detail-heading{align-items:start;border-bottom:1px solid var(--color-border);display:flex;gap:1rem;justify-content:space-between;padding-bottom:.8rem}.detail-heading h2{font-size:.95rem;margin:0}.detail-heading p{color:var(--color-text-muted);font-size:.7rem;margin:.2rem 0 0}.form-grid{display:grid;gap:.85rem;grid-template-columns:repeat(2,minmax(0,1fr))}.field{display:grid;gap:.38rem;min-width:0}.field>span:first-child{font-size:.76rem;font-weight:650}.readonly-value{color:var(--color-text);font-size:.73rem;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.switch-row{align-items:center;cursor:pointer;display:flex;gap:.5rem}.switch-row strong{font-size:.76rem}.dockerfile-editor{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.72rem;line-height:1.55;min-height:27rem;resize:vertical;tab-size:2;white-space:pre}.form-actions{display:flex;gap:.6rem;justify-content:flex-end}@media(max-width:42rem){.detail-heading{align-items:stretch;flex-direction:column}.form-grid{grid-template-columns:1fr}.dockerfile-editor{min-height:22rem}}
</style>
