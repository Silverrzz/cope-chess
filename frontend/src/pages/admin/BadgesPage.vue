<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import { errorText } from '@/components/admin/format'
import type { Badge } from '@/components/admin/types'

interface BadgeSummary extends Badge {
  engine_count: number
}

const badges = ref<BadgeSummary[]>([])
const loading = ref(true)
const error = ref('')
const query = ref('')

const filtered = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase()
  if (!needle) return badges.value
  return badges.value.filter((badge) => `${badge.name} ${badge.description}`.toLocaleLowerCase().includes(needle))
})

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const response = await api.get<{ badges: BadgeSummary[] }>('/api/admin/badges')
    badges.value = response.badges
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="admin-page page-stack">
    <AdminPageHeader title="Badges">
      <template #actions><RouterLink class="button button--primary" to="/admin/badges/new">New badge</RouterLink></template>
    </AdminPageHeader>
    <InlineFeedback :message="error" />
    <section class="panel badge-index">
      <div class="toolbar">
        <input v-model="query" class="input" type="search" placeholder="Search badges" aria-label="Search badges">
        <span>{{ filtered.length }} badge{{ filtered.length === 1 ? '' : 's' }}</span>
      </div>
      <div v-if="loading" class="loading" role="status">Loading badges…</div>
      <div v-else-if="filtered.length" class="badge-grid">
        <article v-for="badge in filtered" :key="badge.id" class="badge-card">
          <RouterLink class="badge-card__link" :to="`/admin/badges/${badge.id}`" :aria-label="`Open ${badge.name}`" />
          <div class="badge-card__emoji" aria-hidden="true">{{ badge.emoji }}</div>
          <div class="badge-card__copy">
            <h2>{{ badge.name }}</h2>
            <p>{{ badge.description || 'No description yet.' }}</p>
          </div>
          <div class="badge-card__footer">
            <span>{{ badge.engine_count }} engine{{ badge.engine_count === 1 ? '' : 's' }}</span>
            <span>Edit badge →</span>
          </div>
        </article>
      </div>
      <AdminEmptyState v-else :title="query ? 'No matching badges' : 'No badges yet'" :description="query ? 'Try another search.' : 'Create the first badge, then choose which engines receive it.'">
        <RouterLink v-if="!query" class="button button--primary button--small" to="/admin/badges/new">Create badge</RouterLink>
      </AdminEmptyState>
    </section>
  </div>
</template>

<style scoped>
.badge-index{overflow:hidden;padding:0}.toolbar{align-items:center;border-bottom:1px solid var(--color-border);display:flex;gap:.75rem;padding:.75rem}.toolbar .input{flex:1;max-width:28rem}.toolbar span{color:var(--color-text-muted);font-size:.72rem}.loading{color:var(--color-text-muted);min-height:14rem;padding:2rem}.badge-grid{display:grid;gap:.8rem;grid-template-columns:repeat(auto-fill,minmax(min(100%,19rem),1fr));padding:.8rem}.badge-card{background:linear-gradient(145deg,color-mix(in srgb,var(--color-accent-soft) 35%,var(--color-surface)) 0%,var(--color-surface) 48%);border:1px solid var(--color-border);border-radius:var(--radius-lg);display:grid;gap:.8rem;grid-template-columns:auto minmax(0,1fr);padding:1rem;position:relative;transition:border-color var(--transition-fast),box-shadow var(--transition-fast),transform var(--transition-fast)}.badge-card:hover{border-color:color-mix(in srgb,var(--color-accent) 55%,var(--color-border));box-shadow:var(--shadow-sm);transform:translateY(-2px)}.badge-card:has(.badge-card__link:focus-visible){border-color:var(--color-focus);box-shadow:0 0 0 3px color-mix(in srgb,var(--color-focus) 20%,transparent)}.badge-card__link{border-radius:inherit;inset:0;position:absolute}.badge-card__emoji{align-items:center;background:var(--color-surface-raised);border:1px solid var(--color-border);border-radius:1rem;box-shadow:var(--shadow-sm);display:flex;font-family:"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif;font-size:2rem;height:3.5rem;justify-content:center;width:3.5rem}.badge-card__copy{min-width:0}.badge-card h2{font-size:.95rem;margin:.2rem 0 0}.badge-card p{color:var(--color-text-muted);display:-webkit-box;font-size:.72rem;line-height:1.45;margin:.3rem 0 0;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:2}.badge-card__footer{align-items:center;border-top:1px solid var(--color-border);color:var(--color-text-muted);display:flex;font-size:.67rem;grid-column:1/-1;justify-content:space-between;padding-top:.7rem}.badge-card__footer span:last-child{color:var(--color-accent);font-weight:680}@media(max-width:34rem){.toolbar{align-items:stretch;flex-direction:column}.toolbar .input{max-width:none}}
</style>
