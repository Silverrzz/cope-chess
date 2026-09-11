<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import StatusBadge from '@/components/admin/StatusBadge.vue'
import { errorText, formatNumber } from '@/components/admin/format'
import type { EngineFamily } from '@/components/admin/types'

interface Response { engines: EngineFamily[]; game_counts: Record<string, number> }
const toast = useToast()
const { confirm } = useConfirm()
const data = ref<Response | null>(null)
const loading = ref(true)
const error = ref('')
const query = ref('')
const activeOnly = ref(false)
const deleting = ref<number | null>(null)
const filtered = computed(() => {
  const needle = query.value.trim().toLowerCase()
  return (data.value?.engines ?? []).filter((engine) =>
    (!activeOnly.value || engine.active) &&
    (!needle || `${engine.name} ${engine.author ?? ''} ${engine.versions.map((version) => `${version.version} ${version.repository_full_name}`).join(' ')}`.toLowerCase().includes(needle)),
  )
})

async function load(): Promise<void> {
  loading.value = true
  try { data.value = await api.get<Response>('/api/admin/engines') }
  catch (cause) { error.value = errorText(cause) }
  finally { loading.value = false }
}

function engineGameCount(engine: EngineFamily): number {
  return engine.versions.reduce(
    (total, version) => total + (data.value?.game_counts[String(version.id)] ?? 0),
    0,
  )
}

async function remove(engine: EngineFamily): Promise<void> {
  if (engine.versions.length) {
    error.value = 'Delete every version before deleting the engine.'
    return
  }
  if (!await confirm({ title: 'Delete engine?', message: `Delete “${engine.name}”?`, confirmLabel: 'Delete engine', tone: 'danger' })) return
  deleting.value = engine.id
  try {
    const response = await api.delete<{ message: string }>(`/api/admin/engines/${engine.id}`)
    toast.success(response.message)
    await load()
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    deleting.value = null
  }
}

onMounted(load)
</script>

<template>
  <div class="admin-page page-stack">
    <AdminPageHeader title="Engines">
      <template #actions>
        <RouterLink class="button button--secondary" to="/admin/engines/queue">Engine queue</RouterLink>
        <RouterLink class="button button--primary" to="/admin/engines/new">New engine</RouterLink>
      </template>
    </AdminPageHeader>
    <InlineFeedback :message="error" />
    <section class="panel engine-index">
      <div class="toolbar">
        <input v-model="query" class="input" type="search" placeholder="Search engines or repositories">
        <label><input v-model="activeOnly" type="checkbox"> Active only</label>
        <span>{{ filtered.length }} engines</span>
      </div>
      <div v-if="loading" class="loading">Loading engines…</div>
      <div v-else-if="filtered.length" class="grid">
        <article v-for="engine in filtered" :key="engine.id" class="engine-card">
          <RouterLink class="engine-card__link" :to="`/admin/engines/${engine.id}`" :aria-label="`Open ${engine.name}`" />
          <div class="heading">
            <div class="mark">{{ engine.name[0]?.toUpperCase() }}</div>
            <div><h2>{{ engine.name }}</h2><p>{{ engine.author || 'Unknown author' }}</p></div>
            <StatusBadge :status="engine.active ? 'active' : 'inactive'" />
          </div>
          <dl class="stats">
            <div><dt>Versions</dt><dd>{{ formatNumber(engine.versions.length) }}</dd></div>
            <div><dt>Artifacts ready</dt><dd>{{ formatNumber(engine.versions.filter((version) => version.artifact).length) }}</dd></div>
            <div><dt>Total games</dt><dd>{{ formatNumber(engineGameCount(engine)) }}</dd></div>
          </dl>
          <div class="actions">
            <button class="button button--danger button--small" type="button" :disabled="deleting === engine.id || engine.versions.length > 0" @click="remove(engine)">Delete</button>
          </div>
        </article>
      </div>
      <AdminEmptyState v-else :title="query || activeOnly ? 'No matching engines' : 'No engines registered'">
        <RouterLink v-if="!query && !activeOnly" class="button button--primary button--small" to="/admin/engines/new">New engine</RouterLink>
      </AdminEmptyState>
    </section>
  </div>
</template>

<style scoped>
.engine-index{overflow:hidden;padding:0}.toolbar{align-items:center;border-bottom:1px solid var(--color-border);display:flex;gap:.75rem;padding:.75rem}.toolbar .input{flex:1;max-width:28rem}.toolbar label,.toolbar span{color:var(--color-text-muted);font-size:.72rem}.grid{display:grid;gap:.8rem;grid-template-columns:repeat(auto-fill,minmax(min(100%,21rem),1fr));padding:.8rem}.engine-card{border:1px solid var(--color-border);border-radius:var(--radius-md);display:grid;gap:.8rem;padding:.9rem;position:relative;transition:border-color var(--transition-fast),transform var(--transition-fast)}.engine-card:hover{border-color:var(--color-accent);transform:translateY(-1px)}.engine-card:has(.engine-card__link:focus-visible){border-color:var(--color-accent);outline:2px solid var(--color-accent);outline-offset:2px}.engine-card__link{border-radius:inherit;inset:0;position:absolute}.heading{align-items:center;display:grid;gap:.65rem;grid-template-columns:auto minmax(0,1fr) auto}.mark{align-items:center;background:color-mix(in srgb,var(--color-accent) 10%,transparent);border-radius:.5rem;color:var(--color-accent);display:flex;font-weight:750;height:2.1rem;justify-content:center;width:2.1rem}.heading h2{font-size:.88rem;margin:0}.heading p{color:var(--color-text-muted);font-size:.68rem;margin:.15rem 0 0}.stats{border-block:1px solid var(--color-border);display:grid;grid-template-columns:repeat(3,minmax(0,1fr));margin:0;padding:.75rem 0}.stats div{display:grid;gap:.2rem;padding:0 .65rem}.stats div:first-child{padding-left:0}.stats div+div{border-left:1px solid var(--color-border)}.stats dt{color:var(--color-text-muted);font-size:.62rem;text-transform:uppercase}.stats dd{font-size:.82rem;font-weight:700;margin:0}.actions{display:flex;gap:.5rem;justify-content:flex-end;margin-top:auto;position:relative;width:max-content;z-index:1}.loading{color:var(--color-text-muted);min-height:14rem;padding:2rem}@media(max-width:38rem){.toolbar{align-items:stretch;flex-wrap:wrap}.toolbar .input{flex-basis:100%;max-width:none}}
</style>
