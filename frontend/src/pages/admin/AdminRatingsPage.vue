<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import InlineFeedback from '@/components/admin/InlineFeedback.vue'
import { errorText, formatNumber } from '@/components/admin/format'
import { useToast } from '@/composables/useToast'

interface RatingList { id: number; name: string; created_at: string; engine_versions: number; games: number; tournaments: number }
interface Response { rating_lists: RatingList[] }

const router = useRouter()
const toast = useToast()
const data = ref<Response | null>(null)
const loading = ref(true)
const pending = ref(false)
const error = ref('')
const showCreate = ref(false)
const name = ref('')

const totalRatedEngines = computed(() => (data.value?.rating_lists ?? []).reduce((sum, item) => sum + item.engine_versions, 0))

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try { data.value = await api.get<Response>('/api/admin/ratings') }
  catch (cause) { error.value = errorText(cause) }
  finally { loading.value = false }
}

async function createList(): Promise<void> {
  if (!name.value.trim()) { error.value = 'Enter a name for the rating list.'; return }
  pending.value = true
  try {
    const response = await api.post<{ id: number; message: string }>('/api/admin/rating-lists', { body: { name: name.value.trim() } })
    toast.success(response.message)
    await router.push(`/admin/ratings/${response.id}`)
  } catch (cause) { error.value = errorText(cause); toast.error(cause) }
  finally { pending.value = false }
}

onMounted(load)
</script>

<template>
  <div class="admin-page page-stack">
    <AdminPageHeader title="Rating lists">
      <template #actions><button class="button button--primary" type="button" @click="showCreate = true">New rating list</button></template>
    </AdminPageHeader>
    <InlineFeedback :message="error" />

    <form v-if="showCreate" class="panel create-list" @submit.prevent="createList">
      <label><span>List name</span><input v-model="name" class="input" maxlength="120" autofocus placeholder="e.g. Classical" /></label>
      <div><button class="button button--ghost" type="button" @click="showCreate = false">Cancel</button><button class="button button--primary" :disabled="pending">{{ pending ? 'Creating…' : 'Create list' }}</button></div>
    </form>

    <section class="summary panel"><span>{{ formatNumber(data?.rating_lists.length) }} lists</span><span>{{ formatNumber(totalRatedEngines) }} rated engine versions</span></section>
    <div v-if="loading" class="panel loading" role="status">Loading rating lists…</div>
    <section v-else-if="data?.rating_lists.length" class="list-grid">
      <RouterLink v-for="ratingList in data.rating_lists" :key="ratingList.id" class="panel list-card" :to="`/admin/ratings/${ratingList.id}`">
        <div><h2>{{ ratingList.name }}</h2><span>Open rating list →</span></div>
        <dl><div><dt>Engine versions</dt><dd>{{ formatNumber(ratingList.engine_versions) }}</dd></div><div><dt>Games</dt><dd>{{ formatNumber(ratingList.games) }}</dd></div><div><dt>Tournaments</dt><dd>{{ formatNumber(ratingList.tournaments) }}</dd></div></dl>
      </RouterLink>
    </section>
    <AdminEmptyState v-else title="No rating lists"><button class="button button--primary button--small" @click="showCreate = true">Create the first list</button></AdminEmptyState>
  </div>
</template>

<style scoped>
.create-list{align-items:end;display:flex;gap:1rem;padding:1rem}.create-list label{display:grid;flex:1;gap:.4rem}.create-list label span{font-size:.75rem;font-weight:700}.create-list>div{display:flex;gap:.5rem}.summary{display:flex;gap:1.5rem;padding:.8rem 1rem;color:var(--color-text-muted);font-size:.76rem}.loading{min-height:12rem;padding:2rem}.list-grid{display:grid;gap:1rem;grid-template-columns:repeat(auto-fill,minmax(18rem,1fr))}.list-card{color:inherit;display:grid;gap:1.2rem;padding:1.15rem;text-decoration:none;transition:border-color .15s,transform .15s}.list-card:hover{border-color:var(--color-accent);transform:translateY(-2px)}.list-card>div{align-items:start;display:flex;justify-content:space-between;gap:1rem}.list-card h2{font-size:1rem;margin:0}.list-card>div span{color:var(--color-accent);font-size:.7rem;font-weight:700}.list-card dl{display:grid;grid-template-columns:repeat(3,1fr);margin:0}.list-card dl div{display:grid;gap:.25rem}.list-card dt{color:var(--color-text-muted);font-size:.62rem}.list-card dd{font-size:1.05rem;font-weight:750;margin:0}@media(max-width:38rem){.create-list{align-items:stretch;flex-direction:column}}
</style>
