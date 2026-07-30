<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useToast } from '@/composables/useToast'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import StatusBadge from '@/components/admin/StatusBadge.vue'
import { errorText, formatDate, formatNumber } from '@/components/admin/format'
import type { Engine, EngineFamily } from '@/components/admin/types'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const id = computed(() => Number(route.params.id) || null)
const loading = ref(!!id.value)
const pending = ref(false)
const error = ref('')
const form = reactive({ name: '', author: '', active: true })
const versions = ref<Engine[]>([])
const gameCounts = ref<Record<string, number>>({})

async function load(): Promise<void> {
  if (!id.value) return
  loading.value = true
  try {
    const response = await api.get<{ engine: EngineFamily; versions: Engine[]; game_counts: Record<string, number> }>(`/api/admin/engines/${id.value}`)
    Object.assign(form, response.engine)
    versions.value = response.versions
    gameCounts.value = response.game_counts
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    loading.value = false
  }
}

async function saveIdentity(): Promise<void> {
  if (!form.name.trim()) {
    error.value = 'Enter an engine name.'
    return
  }
  pending.value = true
  error.value = ''
  try {
    const body = { name: form.name.trim(), author: form.author.trim(), active: form.active }
    const response = id.value
      ? await api.put<{ id: number; message: string }>(`/api/admin/engines/${id.value}`, { body })
      : await api.post<{ id: number; message: string }>('/api/admin/engines', { body })
    toast.success(response.message)
    if (!id.value) await router.replace(`/admin/engines/${response.id}`)
    else await load()
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    pending.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="admin-page page-stack">
    <AdminPageHeader :title="id ? form.name || 'Engine' : 'New engine'">
      <template #actions>
        <RouterLink class="button button--ghost" to="/admin/engines">Back to engines</RouterLink>
        <RouterLink v-if="id" class="button button--primary" :to="`/admin/engines/${id}/versions/new`">New version</RouterLink>
      </template>
    </AdminPageHeader>
    <InlineFeedback :message="error" />
    <div v-if="loading" class="panel form-card" role="status">Loading engine…</div>
    <template v-else>
      <form class="panel form-card" @submit.prevent="saveIdentity">
        <div class="form-card__heading"><h2>Engine identity</h2><p>Name and author are shared by every version.</p></div>
        <div class="form-grid">
          <label class="field"><span>Engine name</span><input v-model="form.name" class="input" required maxlength="80"></label>
          <label class="field"><span>Author <small>optional</small></span><input v-model="form.author" class="input" maxlength="120"></label>
          <label class="switch-row form-span-full"><input v-model="form.active" type="checkbox"><span><strong>Engine active</strong><small>Inactive engines and all their versions are unavailable for new tournaments.</small></span></label>
        </div>
        <div class="form-actions"><button class="button button--primary" type="submit" :disabled="pending">{{ pending ? 'Saving…' : id ? 'Save engine' : 'Create engine' }}</button></div>
      </form>

      <section v-if="id" class="version-section">
        <div class="section-heading">
          <div><h2>Versions</h2><p>{{ versions.length }} version{{ versions.length === 1 ? '' : 's' }}</p></div>
          <RouterLink class="button button--primary button--small" :to="`/admin/engines/${id}/versions/new`">New version</RouterLink>
        </div>
        <div v-if="versions.length" class="version-grid">
          <RouterLink v-for="version in versions" :key="version.id" class="panel version-card" :to="`/admin/engine-versions/${version.id}`">
            <div class="version-card__heading"><div><h3>{{ version.version }}</h3><p>{{ version.repository_full_name }}</p></div><StatusBadge :status="version.active ? 'active' : 'inactive'" /></div>
            <dl>
              <div><dt>Source</dt><dd>{{ version.source_kind === 'release' ? 'Release' : 'Commit' }} · <code>{{ version.source_ref }}</code></dd></div>
              <div><dt>Games</dt><dd>{{ formatNumber(gameCounts[String(version.id)]) }}</dd></div>
              <div><dt>Created</dt><dd>{{ formatDate(version.created_at) }}</dd></div>
            </dl>
            <span class="open-label">Edit Dockerfile and settings →</span>
          </RouterLink>
        </div>
        <AdminEmptyState v-else title="No versions yet">
          <RouterLink class="button button--primary button--small" :to="`/admin/engines/${id}/versions/new`">Create first version</RouterLink>
        </AdminEmptyState>
      </section>
    </template>
  </div>
</template>

<style scoped>
.form-card{display:grid;gap:1rem;padding:clamp(1rem,2vw,1.35rem)}.form-card__heading{border-bottom:1px solid var(--color-border);padding-bottom:.8rem}.form-card__heading h2,.section-heading h2{font-size:.95rem;margin:0}.form-card__heading p,.section-heading p{color:var(--color-text-muted);font-size:.72rem;margin:.2rem 0 0}.form-grid{display:grid;gap:.85rem;grid-template-columns:repeat(2,minmax(0,1fr))}.form-span-full{grid-column:1/-1}.field{display:grid;gap:.38rem}.field>span{font-size:.8rem;font-weight:650}.field small{color:var(--color-text-muted);font-size:.7rem}.switch-row{align-items:flex-start;cursor:pointer;display:flex;gap:.6rem}.switch-row span{display:grid}.switch-row strong{font-size:.8rem}.switch-row small{color:var(--color-text-muted);font-size:.7rem}.form-actions{display:flex;gap:.6rem;justify-content:flex-end}.version-section{display:grid;gap:.8rem}.section-heading{align-items:center;display:flex;justify-content:space-between}.version-grid{display:grid;gap:.8rem;grid-template-columns:repeat(auto-fill,minmax(min(100%,20rem),1fr))}.version-card{color:inherit;display:grid;gap:1rem;padding:1rem;text-decoration:none;transition:border-color var(--transition-fast),transform var(--transition-fast)}.version-card:hover{border-color:var(--color-accent);transform:translateY(-1px)}.version-card__heading{align-items:start;display:flex;gap:.75rem;justify-content:space-between}.version-card h3{font-size:.95rem;margin:0}.version-card p{color:var(--color-text-muted);font-size:.7rem;margin:.2rem 0 0}.version-card dl{display:grid;gap:.6rem;margin:0}.version-card dl div{display:grid;gap:.15rem}.version-card dt{color:var(--color-text-muted);font-size:.62rem;text-transform:uppercase}.version-card dd{font-size:.73rem;margin:0;overflow-wrap:anywhere}.open-label{color:var(--color-accent);font-size:.7rem;font-weight:650}@media(max-width:40rem){.form-grid{grid-template-columns:1fr}.form-span-full{grid-column:auto}}
</style>
