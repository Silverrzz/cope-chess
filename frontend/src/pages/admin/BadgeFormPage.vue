<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '@/api/client'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import BadgeEmojiPicker from '@/components/admin/BadgeEmojiPicker.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import { errorText } from '@/components/admin/format'
import type { Badge, EngineFamily } from '@/components/admin/types'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'

interface BadgeContext {
  badge: Badge | null
  engine_ids: number[]
  engines: EngineFamily[]
}

const route = useRoute()
const router = useRouter()
const toast = useToast()
const { confirm } = useConfirm()
const id = computed(() => Number(route.params.id) || null)
const loading = ref(true)
const saving = ref(false)
const deleting = ref(false)
const error = ref('')
const engineQuery = ref('')
const engines = ref<EngineFamily[]>([])
const selectedEngineIds = ref<number[]>([])
const form = reactive({ name: '', emoji: '', description: '' })

const filteredEngines = computed(() => {
  const needle = engineQuery.value.trim().toLocaleLowerCase()
  if (!needle) return engines.value
  return engines.value.filter((engine) => `${engine.name} ${engine.author ?? ''}`.toLocaleLowerCase().includes(needle))
})
const selectedCount = computed(() => selectedEngineIds.value.length)
const allVisibleSelected = computed(() => filteredEngines.value.length > 0 && filteredEngines.value.every((engine) => selectedEngineIds.value.includes(engine.id)))

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const path = id.value ? `/api/admin/badges/${id.value}` : '/api/admin/badges/form'
    const response = await api.get<BadgeContext>(path)
    if (response.badge) Object.assign(form, response.badge)
    engines.value = response.engines
    selectedEngineIds.value = [...response.engine_ids]
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    loading.value = false
  }
}

function toggleVisible(): void {
  const visibleIds = filteredEngines.value.map((engine) => engine.id)
  if (allVisibleSelected.value) {
    const visible = new Set(visibleIds)
    selectedEngineIds.value = selectedEngineIds.value.filter((engineId) => !visible.has(engineId))
    return
  }
  selectedEngineIds.value = [...new Set([...selectedEngineIds.value, ...visibleIds])]
}

async function save(): Promise<void> {
  if (!form.name.trim()) {
    error.value = 'Enter a badge name.'
    return
  }
  if (!form.emoji) {
    error.value = 'Choose an emoji for the badge.'
    return
  }
  saving.value = true
  error.value = ''
  const body = {
    name: form.name.trim(),
    emoji: form.emoji,
    description: form.description.trim(),
    engine_ids: selectedEngineIds.value,
  }
  try {
    const response = id.value
      ? await api.put<{ id: number; message: string }>(`/api/admin/badges/${id.value}`, { body })
      : await api.post<{ id: number; message: string }>('/api/admin/badges', { body })
    toast.success(response.message)
    if (!id.value) await router.replace(`/admin/badges/${response.id}`)
    else await load()
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    saving.value = false
  }
}

async function remove(): Promise<void> {
  if (!id.value) return
  const approved = await confirm({
    title: 'Delete badge?',
    message: `Delete “${form.name}” and remove it from ${selectedCount.value} engine${selectedCount.value === 1 ? '' : 's'}?`,
    confirmLabel: 'Delete badge',
    tone: 'danger',
  })
  if (!approved) return
  deleting.value = true
  error.value = ''
  try {
    const response = await api.delete<{ message: string }>(`/api/admin/badges/${id.value}`)
    toast.success(response.message)
    await router.replace('/admin/badges')
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
    <AdminPageHeader :title="id ? form.name || 'Badge' : 'New badge'" description="Badge details are shown publicly on every assigned engine.">
      <template #actions>
        <RouterLink class="button button--ghost" to="/admin/badges">Back to badges</RouterLink>
        <button v-if="id" class="button button--danger" type="button" :disabled="saving || deleting" @click="remove">{{ deleting ? 'Deleting…' : 'Delete badge' }}</button>
      </template>
    </AdminPageHeader>
    <InlineFeedback :message="error" />
    <div v-if="loading" class="panel loading" role="status">Loading badge…</div>
    <form v-else class="badge-editor" @submit.prevent="save">
      <section class="panel identity-panel">
        <header><div><h2>Badge identity</h2><p>Choose a memorable emoji and keep the description concise.</p></div></header>
        <div class="identity-layout">
          <div class="form-grid">
            <label class="field"><span>Name</span><input v-model="form.name" class="input" required maxlength="80" placeholder="e.g. World champion"></label>
            <div class="field"><span>Emoji</span><BadgeEmojiPicker v-model="form.emoji" /></div>
            <label class="field form-span-full"><span>Description <small>{{ form.description.length }}/240</small></span><textarea v-model="form.description" class="textarea" maxlength="240" rows="3" placeholder="What does this badge recognize?" /></label>
          </div>
          <aside class="badge-preview" aria-label="Badge preview">
            <span>Preview</span>
            <div class="badge-preview__mark" :class="{ 'badge-preview__mark--empty': !form.emoji }">{{ form.emoji || '＋' }}</div>
            <h3>{{ form.name || 'Badge name' }}</h3>
            <p>{{ form.description || 'Your short description will appear here.' }}</p>
          </aside>
        </div>
      </section>

      <section class="panel engines-panel">
        <header>
          <div><h2>Assigned engines</h2><p>Every version in a selected engine family displays this badge.</p></div>
          <strong>{{ selectedCount }} selected</strong>
        </header>
        <div class="engine-toolbar">
          <input v-model="engineQuery" class="input" type="search" placeholder="Search engines" aria-label="Search engines">
          <button class="button button--secondary button--small" type="button" :disabled="!filteredEngines.length" @click="toggleVisible">{{ allVisibleSelected ? 'Clear shown' : 'Select shown' }}</button>
        </div>
        <div v-if="filteredEngines.length" class="engine-grid">
          <label v-for="engine in filteredEngines" :key="engine.id" class="engine-option" :class="{ 'engine-option--selected': selectedEngineIds.includes(engine.id) }">
            <input v-model="selectedEngineIds" type="checkbox" :value="engine.id">
            <span class="engine-option__check">✓</span>
            <span><strong>{{ engine.name }}</strong><small>{{ engine.author || 'Unknown author' }}</small></span>
            <i :class="{ 'engine-option__status--active': engine.active }">{{ engine.active ? 'Active' : 'Inactive' }}</i>
          </label>
        </div>
        <div v-else class="engine-empty">{{ engines.length ? 'No engines match this search.' : 'Create an engine before assigning this badge.' }}</div>
      </section>

      <div class="form-actions sticky-actions">
        <RouterLink class="button button--ghost" to="/admin/badges">Cancel</RouterLink>
        <button class="button button--primary" type="submit" :disabled="saving || deleting">{{ saving ? 'Saving…' : id ? 'Save badge' : 'Create badge' }}</button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.loading{color:var(--color-text-muted);padding:1.25rem}.badge-editor{display:grid;gap:var(--space-5)}.identity-panel,.engines-panel{overflow:hidden;padding:0}.identity-panel>header,.engines-panel>header{align-items:start;border-bottom:1px solid var(--color-border);display:flex;justify-content:space-between;padding:1rem 1.15rem}.identity-panel h2,.engines-panel h2{font-size:.92rem;margin:0}.identity-panel header p,.engines-panel header p{color:var(--color-text-muted);font-size:.7rem;margin:.22rem 0 0}.engines-panel header>strong{background:var(--color-accent-soft);border-radius:999px;color:var(--color-accent);font-size:.68rem;padding:.35rem .58rem}.identity-layout{display:grid;gap:1.5rem;grid-template-columns:minmax(0,1.5fr) minmax(14rem,.65fr);padding:1.15rem}.form-grid{display:grid;gap:.9rem;grid-template-columns:minmax(0,1fr) minmax(12rem,.65fr)}.form-span-full{grid-column:1/-1}.field textarea{min-height:5.5rem}.field>span{display:flex;justify-content:space-between}.badge-preview{align-items:center;background:linear-gradient(155deg,color-mix(in srgb,var(--color-accent-soft) 65%,var(--color-surface)) 0%,var(--color-surface-sunken) 100%);border:1px solid var(--color-border);border-radius:var(--radius-lg);display:flex;flex-direction:column;justify-content:center;min-height:15rem;padding:1.2rem;text-align:center}.badge-preview>span{color:var(--color-text-muted);font-size:.58rem;font-weight:720;letter-spacing:.08em;text-transform:uppercase}.badge-preview__mark{align-items:center;background:var(--color-surface-raised);border:1px solid var(--color-border);border-radius:1.2rem;box-shadow:var(--shadow-sm);display:flex;font-family:"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif;font-size:2.5rem;height:4.5rem;justify-content:center;margin:.8rem 0;width:4.5rem}.badge-preview__mark--empty{color:var(--color-text-muted)}.badge-preview h3{font-size:1rem;margin:0}.badge-preview p{color:var(--color-text-muted);font-size:.7rem;line-height:1.45;margin:.35rem 0 0;max-width:25ch}.engine-toolbar{align-items:center;border-bottom:1px solid var(--color-border);display:flex;gap:.7rem;padding:.75rem}.engine-toolbar .input{max-width:28rem}.engine-grid{display:grid;gap:.55rem;grid-template-columns:repeat(auto-fill,minmax(min(100%,17rem),1fr));padding:.75rem}.engine-option{align-items:center;border:1px solid var(--color-border);border-radius:var(--radius-md);cursor:pointer;display:grid;gap:.6rem;grid-template-columns:auto minmax(0,1fr) auto;padding:.7rem;transition:background-color var(--transition-fast),border-color var(--transition-fast)}.engine-option:hover{background:var(--color-surface-hover);border-color:var(--color-border-strong)}.engine-option--selected{background:color-mix(in srgb,var(--color-accent-soft) 60%,var(--color-surface));border-color:color-mix(in srgb,var(--color-accent) 55%,var(--color-border))}.engine-option input{position:absolute;opacity:0;pointer-events:none}.engine-option__check{align-items:center;border:1px solid var(--color-border-strong);border-radius:.35rem;color:transparent;display:flex;font-size:.7rem;height:1.2rem;justify-content:center;width:1.2rem}.engine-option--selected .engine-option__check{background:var(--color-accent);border-color:var(--color-accent);color:var(--color-on-accent)}.engine-option>span:nth-of-type(2){display:grid;min-width:0}.engine-option strong{font-size:.76rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.engine-option small{color:var(--color-text-muted);font-size:.64rem;margin-top:.12rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.engine-option i{color:var(--color-text-muted);font-size:.59rem;font-style:normal}.engine-option__status--active{color:var(--color-success)!important}.engine-empty{color:var(--color-text-muted);font-size:.75rem;padding:2rem;text-align:center}.sticky-actions{background:color-mix(in srgb,var(--color-bg) 92%,transparent);border:1px solid var(--color-border);border-radius:var(--radius-lg);bottom:1rem;box-shadow:var(--shadow-sm);padding:.65rem;position:sticky;z-index:10}@media(max-width:48rem){.identity-layout{grid-template-columns:1fr}.badge-preview{min-height:12rem}.form-grid{grid-template-columns:1fr}.form-span-full{grid-column:auto}}@media(max-width:34rem){.engine-toolbar{align-items:stretch;flex-direction:column}.engine-toolbar .input{max-width:none}}
</style>
