<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import { errorText, formatDate, formatNumber } from '@/components/admin/format'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'

interface RatingRow { engine: { engine_id: number; name: string; version: string }; elo: number; games_played: number; error_margin?: number | null; updated_at?: string }
interface Commit { tournament_id: number; tournament_name: string; tournament_status: string; applied_at: string | null; requested_at: string; games: number; hardware_games: number }
interface EngineVersion { id: number; name: string; version: string }
interface Response { rating_list: { id: number; name: string; anchor_engine_id: number | null; anchor_elo: number; created_at: string }; ratings: RatingRow[]; tournaments: Commit[]; engine_versions: EngineVersion[] }

const route = useRoute(); const router = useRouter(); const toast = useToast(); const { confirm } = useConfirm()
const id = computed(() => Number(route.params.id)); const data = ref<Response | null>(null); const loading = ref(true); const error = ref(''); const pending = ref('')
const anchorEngineId = ref<number | ''>(''); const anchorElo = ref(1500)

async function load() { loading.value = true; error.value = ''; try { data.value = await api.get<Response>(`/api/admin/rating-lists/${id.value}`); anchorEngineId.value = data.value.rating_list.anchor_engine_id ?? ''; anchorElo.value = data.value.rating_list.anchor_elo } catch (cause) { error.value = errorText(cause) } finally { loading.value = false } }
async function uncommit(item: Commit) {
  if (!await confirm({ title: 'Uncommit tournament?', message: `Remove ${item.tournament_name} from this list? The tournament and games are retained. Calculate the ratings when you are ready to apply the change.`, confirmLabel: 'Uncommit', tone: 'danger' })) return
  pending.value = `uncommit-${item.tournament_id}`
  try { const response = await api.delete<{message:string}>(`/api/admin/rating-lists/${id.value}/tournaments/${item.tournament_id}`); toast.success(response.message); await load() } catch (cause) { error.value = errorText(cause); toast.error(cause) } finally { pending.value = '' }
}
async function calculate() {
  if (anchorEngineId.value === '') { error.value = 'Select an engine version to use as the Elo anchor.'; return }
  pending.value = 'calculate'; error.value = ''
  try { const response = await api.post<{message:string}>(`/api/admin/rating-lists/${id.value}/calculate`, { body: { anchor_engine_id: anchorEngineId.value, anchor_elo: anchorElo.value } }); toast.success(response.message); await load() } catch (cause) { error.value = errorText(cause); toast.error(cause) } finally { pending.value = '' }
}
async function remove() {
  if (!data.value || !await confirm({ title: 'Delete rating list?', message: `Delete ${data.value.rating_list.name} and its rating history? Tournament results are retained.`, confirmLabel: 'Delete list', tone: 'danger' })) return
  pending.value = 'delete'; try { const response = await api.delete<{message:string}>(`/api/admin/rating-lists/${id.value}`); toast.success(response.message); await router.push('/admin/ratings') } catch (cause) { error.value = errorText(cause) } finally { pending.value = '' }
}
onMounted(load)
</script>

<template><div class="admin-page page-stack">
  <div v-if="loading" class="panel loading">Loading rating list…</div>
  <template v-else-if="data">
    <AdminPageHeader :title="data.rating_list.name" description="Hardware-adjusted Elo for individual engine versions."><template #actions><RouterLink class="button button--ghost" to="/admin/ratings">All lists</RouterLink><RouterLink class="button button--secondary" :to="`/ratings?rating_list_id=${id}`">Public list</RouterLink><button class="button button--danger" :disabled="!!pending" @click="remove">Delete</button></template></AdminPageHeader>
    <InlineFeedback :message="error" />
    <form class="panel anchor-panel" @submit.prevent="calculate">
      <div class="anchor-heading"><h2>Elo anchor</h2><p>Choose the engine version that defines the rating scale. Its calculated rating will be fixed to the Elo entered here.</p></div>
      <label class="field"><span>Engine version</span><select v-model.number="anchorEngineId" class="input" required><option disabled value="">Select an engine version</option><option v-for="engine in data.engine_versions" :key="engine.id" :value="engine.id">{{ engine.name }} — {{ engine.version }}</option></select></label>
      <label class="field"><span>Elo</span><input v-model.number="anchorElo" class="input" type="number" min="-10000" max="10000" step="1" required></label>
      <button class="button button--primary" type="submit" :disabled="!!pending || !data.engine_versions.length">{{ pending === 'calculate' ? 'Calculating…' : 'Calculate ratings' }}</button>
    </form>
    <section class="panel table-panel"><div class="heading"><h2>Ratings</h2><span>{{ formatNumber(data.ratings.length) }} engine versions</span></div><div v-if="data.ratings.length" class="table-wrap"><table><thead><tr><th>Rank</th><th>Engine</th><th>Version</th><th>Elo</th><th>Games</th><th>95% error</th><th>Updated</th></tr></thead><tbody><tr v-for="(row,index) in data.ratings" :key="row.engine.engine_id"><td>{{ index+1 }}</td><td><strong>{{ row.engine.name }}</strong></td><td>{{ row.engine.version }}</td><td><strong>{{ Math.round(row.elo).toLocaleString() }}</strong></td><td>{{ formatNumber(row.games_played) }}</td><td>{{ row.error_margin == null ? '-' : `±${Math.round(row.error_margin)}` }}</td><td>{{ formatDate(row.updated_at) }}</td></tr></tbody></table></div><AdminEmptyState v-else title="No ratings yet" /></section>
    <section class="panel table-panel"><div class="heading"><h2>Committed tournaments</h2><span>{{ data.tournaments.length }}</span></div><div v-if="data.tournaments.length" class="table-wrap"><table><thead><tr><th>Tournament</th><th>Applied</th><th>Games</th><th>Hardware</th><th></th></tr></thead><tbody><tr v-for="item in data.tournaments" :key="item.tournament_id"><td><RouterLink :to="`/admin/tournaments/${item.tournament_id}`">{{ item.tournament_name }}</RouterLink></td><td>{{ formatDate(item.applied_at ?? item.requested_at) }}</td><td>{{ item.games }}</td><td>{{ item.hardware_games }}/{{ item.games }}</td><td><button class="button button--danger button--small" :disabled="!!pending" @click="uncommit(item)">{{ pending === `uncommit-${item.tournament_id}` ? 'Removing…' : 'Uncommit' }}</button></td></tr></tbody></table></div><AdminEmptyState v-else title="No committed tournaments" /></section>
  </template><InlineFeedback v-else :message="error" />
</div></template>

<style scoped>.loading{min-height:16rem;padding:2rem}.anchor-panel{align-items:end;display:grid;gap:1rem;grid-template-columns:minmax(16rem,2fr) minmax(8rem,1fr) auto;padding:1rem}.anchor-heading{grid-column:1/-1}.anchor-heading h2{font-size:.9rem;margin:0}.anchor-heading p{color:var(--color-text-muted);font-size:.72rem;margin:.25rem 0 0}.field{display:grid;gap:.38rem}.field>span{font-size:.76rem;font-weight:650}.table-panel{overflow:hidden;padding:0}.heading{align-items:center;border-bottom:1px solid var(--color-border);display:flex;justify-content:space-between;padding:.9rem 1rem}.heading h2{font-size:.9rem;margin:0}.heading span{color:var(--color-text-muted);font-size:.7rem}.table-wrap{overflow:auto}table{border-collapse:collapse;min-width:48rem;width:100%}th,td{border-bottom:1px solid var(--color-border);font-size:.72rem;padding:.72rem .8rem;text-align:left}th{color:var(--color-text-muted);font-size:.62rem;text-transform:uppercase}tbody tr:last-child td{border-bottom:0}@media(max-width:44rem){.anchor-panel{align-items:stretch;grid-template-columns:1fr}.anchor-heading{grid-column:auto}}</style>
