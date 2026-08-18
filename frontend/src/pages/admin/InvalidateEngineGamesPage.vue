<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { errorText } from '@/components/admin/format'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'

interface EngineChoice { id: number; name: string; version: string; author: string }
interface RatingListChoice { id: number; name: string; engine_ids: number[] }
interface ContextResponse { engines: EngineChoice[]; rating_lists: RatingListChoice[] }
interface InvalidationResponse {
  message: string
  games_invalidated: number
  tournaments_affected: number
  rating_lists_affected: number
  list_memberships_removed: number
}

const toast = useToast()
const { confirm } = useConfirm()
const context = ref<ContextResponse | null>(null)
const engineId = ref<number | ''>('')
const ratingListId = ref<number | ''>('')
const loading = ref(true)
const invalidating = ref(false)
const error = ref('')
const result = ref<InvalidationResponse | null>(null)

const ratingLists = computed(() => {
  if (engineId.value === '') return context.value?.rating_lists ?? []
  return (context.value?.rating_lists ?? []).filter((ratingList) => ratingList.engine_ids.includes(engineId.value as number))
})
const selectedEngine = computed(() => context.value?.engines.find((engine) => engine.id === engineId.value))
const selectedRatingList = computed(() => context.value?.rating_lists.find((ratingList) => ratingList.id === ratingListId.value))

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    context.value = await api.get<ContextResponse>('/api/admin/tools/invalidate-engine-games')
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    loading.value = false
  }
}

function selectEngine(): void {
  ratingListId.value = ''
  result.value = null
}

async function invalidate(): Promise<void> {
  if (engineId.value === '' || ratingListId.value === '' || !selectedEngine.value || !selectedRatingList.value) return
  const accepted = await confirm({
    title: 'Invalidate engine games?',
    message: `Permanently remove every ${selectedEngine.value.name} ${selectedEngine.value.version} game committed to ${selectedRatingList.value.name}? Those games will also be removed from every other rating list containing them. Elo values will remain unchanged until you calculate them manually.`,
    confirmLabel: 'Invalidate games',
    tone: 'danger',
  })
  if (!accepted) return
  invalidating.value = true
  error.value = ''
  result.value = null
  try {
    result.value = await api.post<InvalidationResponse>('/api/admin/tools/invalidate-engine-games', {
      body: { engine_id: engineId.value, rating_list_id: ratingListId.value },
    })
    toast.success(result.value.message)
    engineId.value = ''
    ratingListId.value = ''
    await load()
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    invalidating.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="admin-page page-stack invalidation-page">
    <AdminPageHeader title="Invalidate engine games" description="Surgically remove one engine version's games from committed rating data.">
      <template #actions><BaseButton variant="ghost" to="/admin/tools"><template #icon><AppIcon name="arrow-left" :size="16" /></template>All tools</BaseButton></template>
    </AdminPageHeader>
    <InlineFeedback :message="error" />

    <form class="panel invalidation-form" @submit.prevent="invalidate">
      <div class="form-heading">
        <span class="form-heading__icon"><AppIcon name="trash" :size="21" /></span>
        <div><span class="eyebrow">Destructive database tool</span><h2>Select the exact engine and source list</h2><p>Only games involving the selected engine version are deleted. Their rating history is removed from the selected list and every other list containing the same games.</p></div>
      </div>
      <div class="fields">
        <label class="field">
          <span>Engine version</span>
          <select v-model.number="engineId" class="input" required :disabled="loading || invalidating" @change="selectEngine">
            <option disabled value="">Choose an engine</option>
            <option v-for="engine in context?.engines" :key="engine.id" :value="engine.id">{{ engine.name }} - {{ engine.version }}</option>
          </select>
          <small>Only versions currently present in committed rating history are shown.</small>
        </label>
        <label class="field">
          <span>Rating list</span>
          <select v-model.number="ratingListId" class="input" required :disabled="loading || invalidating || engineId === ''">
            <option disabled value="">Choose a rating list</option>
            <option v-for="ratingList in ratingLists" :key="ratingList.id" :value="ratingList.id">{{ ratingList.name }}</option>
          </select>
          <small>Lists without committed games for this engine are hidden.</small>
        </label>
      </div>
      <div class="impact">
        <AppIcon name="alert-circle" :size="18" />
        <div><strong>Ratings are not recalculated</strong><span>The current Elo rows remain as they are. Use the ratings page to calculate them after reviewing the change.</span></div>
      </div>
      <div class="actions">
        <BaseButton type="submit" variant="danger" size="large" :loading="invalidating" :disabled="loading || engineId === '' || ratingListId === ''"><template #icon><AppIcon name="trash" :size="16" /></template>Invalidate</BaseButton>
      </div>
    </form>

    <section v-if="result" class="panel result" aria-live="polite">
      <span class="result__icon"><AppIcon name="check" :size="20" /></span>
      <div><span class="eyebrow">Complete</span><h2>{{ result.message }}</h2><p>{{ result.list_memberships_removed }} game/list association{{ result.list_memberships_removed === 1 ? '' : 's' }} removed across {{ result.rating_lists_affected }} rating list{{ result.rating_lists_affected === 1 ? '' : 's' }} and {{ result.tournaments_affected }} tournament{{ result.tournaments_affected === 1 ? '' : 's' }}.</p></div>
    </section>
  </div>
</template>

<style scoped>
.invalidation-page{max-width:62rem}.invalidation-form{display:grid;gap:1.2rem;padding:1.2rem}.form-heading{align-items:flex-start;display:flex;gap:.85rem}.form-heading__icon,.result__icon{align-items:center;border-radius:.65rem;display:flex;flex:0 0 auto;justify-content:center}.form-heading__icon{background:color-mix(in srgb,var(--color-danger) 10%,transparent);border:1px solid color-mix(in srgb,var(--color-danger) 25%,var(--color-border));color:var(--color-danger);height:2.7rem;width:2.7rem}.eyebrow{color:var(--color-accent);font-size:.62rem;font-weight:760;letter-spacing:.1em;text-transform:uppercase}.form-heading h2,.result h2{font-size:1rem;margin:.25rem 0 0}.form-heading p,.result p{color:var(--color-text-muted);font-size:.73rem;line-height:1.55;margin:.3rem 0 0;max-width:60rem}.fields{display:grid;gap:1rem;grid-template-columns:repeat(2,minmax(0,1fr))}.field{display:grid;gap:.4rem}.field>span{font-size:.76rem;font-weight:650}.field small{color:var(--color-text-muted);font-size:.65rem}.impact{align-items:flex-start;background:color-mix(in srgb,var(--color-warning) 8%,var(--color-surface-sunken));border:1px solid color-mix(in srgb,var(--color-warning) 25%,var(--color-border));border-radius:var(--radius-md);color:var(--color-warning);display:flex;gap:.65rem;padding:.8rem}.impact div{display:grid;gap:.2rem}.impact strong{font-size:.75rem}.impact span{color:var(--color-text-muted);font-size:.68rem;line-height:1.45}.actions{display:flex;justify-content:flex-end}.result{align-items:flex-start;display:flex;gap:.8rem;padding:1rem}.result__icon{background:color-mix(in srgb,var(--color-success) 12%,transparent);color:var(--color-success);height:2.5rem;width:2.5rem}@media(max-width:42rem){.fields{grid-template-columns:1fr}.actions .base-button{width:100%}}
</style>
