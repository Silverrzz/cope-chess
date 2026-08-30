<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '@/api/client'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import { errorText, formatDate } from '@/components/admin/format'

interface ToolSummary {
  name: string
  label: string
  description: string
  href: string
  status: string
}

interface JobSummary {
  id: number
  tool_name: string
  status: string
  input: { option_name?: string }
  completed_items: number
  total_items: number
  created_at: string
  worker?: { label: string } | null
}

interface Response {
  tools: ToolSummary[]
  recent_jobs: JobSummary[]
  connected_workers: number
}

const data = ref<Response | null>(null)
const loading = ref(true)
const error = ref('')

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.get<Response>('/api/admin/tools')
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    loading.value = false
  }
}

function statusLabel(status: string): string {
  return status.charAt(0).toUpperCase() + status.slice(1)
}

onMounted(load)
</script>

<template>
  <div class="admin-page page-stack tools-page">
    <AdminPageHeader title="Tools" description="Planning and operational utilities for tournaments, engines, and workers." />
    <InlineFeedback :message="error" />

    <section class="tools-hero panel">
      <div class="tools-hero__mark"><AppIcon name="wrench" :size="24" /></div>
      <div>
        <span class="eyebrow">Admin utilities</span>
        <h2>One home for tournament planning and fleet-wide maintenance</h2>
        <p>Tools use the same rating lists, engine definitions, build cache, authentication, and live worker connections as tournament operations.</p>
      </div>
      <div class="fleet-state" :class="{ 'fleet-state--offline': !data?.connected_workers }">
        <span class="fleet-state__pulse" />
        <strong>{{ data?.connected_workers ?? 0 }}</strong>
        <span>worker{{ data?.connected_workers === 1 ? '' : 's' }} online</span>
      </div>
    </section>

    <section aria-labelledby="available-tools-title">
      <div class="section-heading">
        <div><span class="eyebrow">Catalog</span><h2 id="available-tools-title">Available tools</h2></div>
        <span class="section-heading__count">{{ data?.tools.length ?? 0 }} available</span>
      </div>
      <div v-if="loading" class="tool-grid">
        <div v-for="index in 4" :key="index" class="tool-card tool-card--loading panel" />
      </div>
      <div v-else class="tool-grid">
        <RouterLink v-for="tool in data?.tools" :key="tool.name" class="tool-card panel" :to="tool.href">
          <div class="tool-card__top">
            <span class="tool-card__icon"><AppIcon :name="tool.name === 'invalidate_rating_list_engine' ? 'trash' : tool.name === 'tournament_creator' ? 'trophy' : tool.name === 'clone_environment' ? 'copy' : 'search'" :size="22" /></span>
            <span class="tool-card__status"><span />{{ statusLabel(tool.status) }}</span>
          </div>
          <div><h3>{{ tool.label }}</h3><p>{{ tool.description }}</p></div>
          <span class="tool-card__action">Open tool <AppIcon name="arrow-right" :size="16" /></span>
        </RouterLink>
        <article class="tool-card tool-card--soon panel">
          <div class="tool-card__top">
            <span class="tool-card__icon"><AppIcon name="plus" :size="22" /></span>
            <span class="tool-card__status tool-card__status--muted">Extensible</span>
          </div>
          <div><h3>More tools</h3><p>The shared queue, progress, results, and worker execution model is ready for additional utilities.</p></div>
          <span class="tool-card__action tool-card__action--muted">Tool framework ready</span>
        </article>
      </div>
    </section>

    <section class="panel recent" aria-labelledby="recent-title">
      <div class="recent__header"><div><span class="eyebrow">Activity</span><h2 id="recent-title">Recent runs</h2></div></div>
      <div v-if="data?.recent_jobs.length" class="recent__list">
        <RouterLink
          v-for="job in data.recent_jobs"
          :key="job.id"
          class="recent-row"
          :to="{ path: '/admin/tools/who-has-this', query: { job: job.id } }"
        >
          <span class="recent-row__icon"><AppIcon name="search" :size="16" /></span>
          <span class="recent-row__main"><strong>{{ job.input.option_name || 'Who Has This' }}</strong><small>{{ formatDate(job.created_at) }}</small></span>
          <span class="recent-row__progress">{{ job.completed_items }}/{{ job.total_items }}</span>
          <span class="job-status" :class="`job-status--${job.status}`">{{ statusLabel(job.status) }}</span>
          <AppIcon name="chevron-right" :size="16" />
        </RouterLink>
      </div>
      <div v-else-if="!loading" class="recent__empty">Your completed and in-progress tool runs will appear here.</div>
    </section>
  </div>
</template>

<style scoped>
.tools-page{gap:1.6rem}.tools-hero{align-items:center;background:linear-gradient(120deg,color-mix(in srgb,var(--color-accent) 9%,var(--color-surface-raised)),var(--color-surface-raised) 55%);display:grid;gap:1rem;grid-template-columns:auto minmax(0,1fr) auto;overflow:hidden;padding:1.35rem;position:relative}.tools-hero:after{background:radial-gradient(circle,color-mix(in srgb,var(--color-accent) 14%,transparent),transparent 68%);content:"";height:14rem;position:absolute;right:-4rem;top:-7rem;width:14rem}.tools-hero__mark,.tool-card__icon,.recent-row__icon{align-items:center;background:var(--color-accent-soft);border:1px solid color-mix(in srgb,var(--color-accent) 25%,transparent);border-radius:.7rem;color:var(--color-accent);display:flex;justify-content:center}.tools-hero__mark{height:3rem;width:3rem}.eyebrow{color:var(--color-accent);font-size:.62rem;font-weight:760;letter-spacing:.1em;text-transform:uppercase}.tools-hero h2,.section-heading h2,.recent h2{font-size:1rem;margin:.25rem 0 0}.tools-hero p{color:var(--color-text-muted);font-size:.78rem;margin:.35rem 0 0;max-width:60rem}.fleet-state{align-items:center;background:color-mix(in srgb,var(--color-success) 9%,var(--color-surface-raised));border:1px solid color-mix(in srgb,var(--color-success) 24%,var(--color-border));border-radius:999px;display:flex;gap:.4rem;padding:.5rem .7rem;position:relative;z-index:1}.fleet-state span:last-child{color:var(--color-text-muted);font-size:.68rem}.fleet-state__pulse{background:var(--color-success);border-radius:50%;height:.42rem;width:.42rem}.fleet-state--offline{background:var(--color-surface-sunken);border-color:var(--color-border)}.fleet-state--offline .fleet-state__pulse{background:var(--color-text-faint)}.section-heading,.recent__header{align-items:end;display:flex;justify-content:space-between;margin-bottom:.75rem}.section-heading__count{color:var(--color-text-muted);font-size:.7rem}.tool-grid{display:grid;gap:.8rem;grid-template-columns:repeat(2,minmax(0,1fr))}.tool-card{color:inherit;display:flex;flex-direction:column;gap:1rem;min-height:12rem;padding:1rem;text-decoration:none;transition:border-color var(--transition-fast),transform var(--transition-fast),box-shadow var(--transition-fast)}a.tool-card:hover{border-color:color-mix(in srgb,var(--color-accent) 55%,var(--color-border));box-shadow:var(--shadow-sm);transform:translateY(-2px)}.tool-card__top{align-items:center;display:flex;justify-content:space-between}.tool-card__icon{height:2.55rem;width:2.55rem}.tool-card__status{align-items:center;background:color-mix(in srgb,var(--color-success) 10%,transparent);border-radius:999px;color:var(--color-success);display:flex;font-size:.62rem;font-weight:680;gap:.35rem;padding:.3rem .5rem}.tool-card__status span{background:currentColor;border-radius:50%;height:.35rem;width:.35rem}.tool-card__status--muted{background:var(--color-surface-sunken);color:var(--color-text-muted)}.tool-card h3{font-size:.95rem;margin:0}.tool-card p{color:var(--color-text-muted);font-size:.72rem;line-height:1.55;margin:.35rem 0 0;max-width:52ch}.tool-card__action{align-items:center;color:var(--color-accent);display:flex;font-size:.7rem;font-weight:690;gap:.35rem;margin-top:auto}.tool-card__action--muted{color:var(--color-text-faint)}.tool-card--soon{border-style:dashed}.tool-card--loading{animation:pulse 1.3s ease-in-out infinite;background:var(--color-surface-sunken)}.recent{overflow:hidden;padding:0}.recent__header{border-bottom:1px solid var(--color-border);margin:0;padding:.85rem 1rem}.recent__list{display:grid}.recent-row{align-items:center;color:inherit;display:grid;gap:.7rem;grid-template-columns:auto minmax(0,1fr) auto auto auto;padding:.7rem 1rem;text-decoration:none}.recent-row+.recent-row{border-top:1px solid var(--color-border)}.recent-row:hover{background:var(--color-surface-hover)}.recent-row__icon{border-radius:.5rem;height:2rem;width:2rem}.recent-row__main{display:grid;gap:.15rem}.recent-row__main strong{font-size:.75rem}.recent-row__main small,.recent-row__progress{color:var(--color-text-muted);font-size:.63rem}.job-status{border-radius:999px;font-size:.6rem;font-weight:700;padding:.25rem .45rem}.job-status--completed{background:color-mix(in srgb,var(--color-success) 10%,transparent);color:var(--color-success)}.job-status--running{background:var(--color-accent-soft);color:var(--color-accent)}.job-status--queued{background:color-mix(in srgb,var(--color-warning) 10%,transparent);color:var(--color-warning)}.job-status--failed,.job-status--cancelled{background:color-mix(in srgb,var(--color-danger) 10%,transparent);color:var(--color-danger)}.recent__empty{color:var(--color-text-muted);font-size:.72rem;padding:2rem;text-align:center}@keyframes pulse{50%{opacity:.58}}@media(max-width:48rem){.tools-hero{grid-template-columns:auto 1fr}.fleet-state{grid-column:1/-1;width:max-content}.tool-grid{grid-template-columns:1fr}}@media(max-width:34rem){.recent-row{grid-template-columns:auto minmax(0,1fr) auto}.recent-row__progress,.recent-row>.job-status{display:none}}
</style>
