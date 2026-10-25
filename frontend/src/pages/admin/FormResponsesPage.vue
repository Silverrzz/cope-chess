<script setup lang="ts">
import BaseButton from '@/components/ui/BaseButton.vue'
import { onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { formResponses } from '@/api/forms'
import type { FormResponse } from '@/types/forms'

const route = useRoute()
const items = ref<(FormResponse & { form_title: string })[]>([])
const total = ref(0)
const cursor = ref<string | null>(null)
const loading = ref(false)
const error = ref('')
const labels: Record<string, string> = { awaiting_review: 'Awaiting review', accepted: 'Accepted', declined: 'Declined', changes_requested: 'Changes requested', withdrawn: 'Withdrawn' }
async function load(more = false): Promise<void> {
  loading.value = true; error.value = ''
  try {
    const page = await formResponses(typeof route.query.form === 'string' ? route.query.form : undefined, more ? cursor.value : null)
    items.value = more ? [...items.value, ...page.items] : page.items; cursor.value = page.next_cursor; total.value = page.total
  } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Could not load responses.' }
  finally { loading.value = false }
}
onMounted(() => load())
</script>

<template>
  <section class="response-inbox" :aria-busy="loading"><header><RouterLink to="/admin/forms">← Forms</RouterLink><h1>Response inbox</h1><p>{{ total }} responses available to you. Membership and counts reflect your current permissions.</p><BaseButton type="button" class="button button--ghost" :disabled="loading" @click="load()" variant="ghost">Refresh</BaseButton></header><p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="!items.length && !loading">No responses are available.</p><ul><li v-for="item in items" :key="item.id"><div><RouterLink :to="`/admin/form-responses/${item.id}`">{{ item.form_title }}</RouterLink><small>Revision {{ item.revision }} · {{ new Date(item.created_at).toLocaleString() }}</small></div><span>{{ labels[item.state] }}</span></li></ul><BaseButton v-if="cursor" type="button" class="button button--ghost" :disabled="loading" @click="load(true)" variant="ghost">Load more</BaseButton></section>
</template>

<style scoped>
.response-inbox{max-width:65rem}.response-inbox h1{font-size:1.8rem}.response-inbox ul{list-style:none;padding:0}.response-inbox li{border-top:1px solid var(--color-border);display:flex;gap:1rem;justify-content:space-between;padding:1rem 0}.response-inbox li div{display:grid;gap:.4rem}.response-inbox small,.response-inbox li>span{font-size:.8rem;color:var(--color-text-muted,#64748b)}.error{color:var(--color-danger,#b91c1c)}
</style>
