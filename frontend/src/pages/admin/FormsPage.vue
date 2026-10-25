<script setup lang="ts">
import BaseButton from '@/components/ui/BaseButton.vue'
import { onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { applicationQuery } from '@/api/application'
import { useApplicationMutation } from '@/composables/useApplicationMutation'
import type { FormDetail, Page } from '@/types/forms'

const router = useRouter()
const mutation = useApplicationMutation()
const rows = ref<FormDetail[]>([])
const cursor = ref<string | null>(null)
const total = ref(0)
const loading = ref(false)
const error = ref('')
const label = ref('')
const slug = ref('')
async function load(more = false): Promise<void> {
  loading.value = true; error.value = ''
  try {
    const page = await applicationQuery<Page<FormDetail>>('forms.list.v1', { limit: 30, cursor: more ? cursor.value : null })
    rows.value = more ? [...rows.value, ...page.items] : page.items
    total.value = page.total; cursor.value = page.next_cursor
  } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Could not load forms.' }
  finally { loading.value = false }
}
async function create(): Promise<void> {
  error.value = ''
  try {
    const receipt = await mutation.execute<'forms.create.v1'>({ command: 'forms.create.v1', input: { slug: slug.value, label: label.value, document: { title: label.value, audience: 'authenticated', binding: 'feedback.v1', mapping: { message: 'message' }, fields: [{ id: 'message', kind: 'long_text', label: 'Message', required: true }] } } })
    await router.push(`/admin/forms/${receipt.output.form_id}?copy=${receipt.output.copy_id}`)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Could not create the form.' }
}
onMounted(() => load())
</script>

<template>
  <div class="forms-page">
    <header><p class="eyebrow">Forms</p><h1>Collect and review responses</h1><p>Build private drafts, publish a release and review responses in a private inbox.</p><RouterLink to="/admin/form-responses">Open response inbox</RouterLink></header>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <form class="panel create-form" @submit.prevent="create"><h2>Create a form</h2><fieldset :disabled="mutation.busy.value || mutation.uncertain.value"><label>Internal name<input v-model="label" class="input" required maxlength="300"></label><label>Public link name<input v-model="slug" class="input" required maxlength="100" pattern="[a-z0-9]+(-[a-z0-9]+)*" placeholder="event-feedback"><small>Lowercase words separated by hyphens. This becomes part of the public URL.</small></label></fieldset><BaseButton type="submit" class="button" :disabled="mutation.busy.value">{{ mutation.uncertain.value ? 'Retry creation' : 'Create private draft' }}</BaseButton></form>
    <section class="panel" :aria-busy="loading"><div class="list-heading"><h2>Your forms <small>{{ total }}</small></h2><BaseButton type="button" class="button button--ghost" :disabled="loading" @click="load()" variant="ghost">Refresh</BaseButton></div><p v-if="!loading && !rows.length">No forms are available to you.</p><ul class="form-list"><li v-for="form in rows" :key="form.id"><RouterLink :to="`/admin/forms/${form.id}`">{{ form.label }}</RouterLink><span>{{ form.closed ? 'Closed' : form.published_release ? 'Published' : 'Draft only' }}</span><RouterLink :to="`/admin/form-responses?form=${form.id}`">Responses</RouterLink></li></ul><BaseButton type="submit" v-if="cursor" class="button button--ghost" :disabled="loading" @click="load(true)" variant="ghost">Load more</BaseButton></section>
  </div>
</template>

<style scoped>
.forms-page{display:grid;gap:1.5rem;max-width:70rem}.forms-page h1{font-size:1.8rem}.panel{border:1px solid var(--color-border);border-radius:.8rem;padding:1.25rem}.panel h2{font-size:1rem;margin-top:0}.create-form fieldset{border:0;display:grid;gap:1rem;grid-template-columns:1fr 1fr;margin:0 0 1rem;padding:0}.create-form label{display:grid;gap:.35rem;font-size:.85rem}.create-form small{font-size:.75rem}.list-heading,.form-list li{align-items:center;display:flex;gap:1rem;justify-content:space-between}.form-list{list-style:none;margin:0;padding:0}.form-list li{border-top:1px solid var(--color-border);padding:1rem 0}.form-list li>a:first-child{flex:1}.error{color:var(--color-danger,#b91c1c)}@media(max-width:42rem){.create-form fieldset{grid-template-columns:1fr}.form-list li{align-items:flex-start;flex-wrap:wrap}}
</style>
