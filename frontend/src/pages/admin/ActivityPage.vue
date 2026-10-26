<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ApiError } from '@/api/client'
import { applicationQuery } from '@/api/application'
import { decodeActivityPage } from '@/api/activity'
import { useCoherentView } from '@/composables/useCoherentView'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { useSessionStore } from '@/stores/session'
import type { ApplicationQueryInputs, ApplicationQueryOutputs } from '@/types/application-inputs'

type ActivityPage = ApplicationQueryOutputs['activity.list.v1']
type ActivityItem = ActivityPage['items'][number]
type State = NonNullable<ApplicationQueryInputs['activity.list.v1']['state']>
const session = useSessionStore()
const coherent = useCoherentView<'legacy.activity.list.v1'>(decodeActivityPage)
const coherentEnabled = computed(() => session.user?.role === 'admin')
const items = ref<ActivityItem[]>([])
const page = ref<ActivityPage | null>(null)
const selected = ref<ActivityItem | null>(null)
const removed = ref<ApplicationQueryOutputs['objects.removal.v1'] | null>(null)
const sources = ref<ApplicationQueryOutputs['activity.sources.v1']['items']>([])
const delivery = ref<ApplicationQueryOutputs['events.status.v1'] | null>(null)
const kind = ref<'' | 'operation' | 'job'>('')
const source = ref('')
const state = ref<'' | State>('')
const manualLoading = ref(false)
const loading = computed(() => manualLoading.value || coherentEnabled.value && (coherent.state.value.status === 'connecting' || coherent.state.value.dirty && coherent.state.value.status === 'synchronizing'))
const detailLoading = ref(false)
const deliveryLoading = ref(false)
const error = ref('')
const detailError = ref('')
const deliveryError = ref('')
const states: State[] = ['planned','blocked','queued','leased','exported','running','waiting','cancelling','succeeded','failed','cancelled']
const countLabel = computed(() => page.value ? `${page.value.count.count.toLocaleString()}${page.value.count.more ? '+' : ''}` : '—')
let generation = 0
let detailGeneration = 0
let deliveryGeneration = 0
let catalogueGeneration = 0
let controller: AbortController | null = null
let detailController: AbortController | null = null
let deliveryController: AbortController | null = null
let active = true
const syncLabel = computed(() => ({stopped:'Refresh to inspect current activity.',connecting:'Acquiring a coherent snapshot…',synchronizing:'Changes received · refreshing the authoritative view…',current:'Current through the checked durable checkpoint',stale:'Connection interrupted · displayed activity may be stale',recovering:'Replay context changed · reacquiring the view…',incompatible:'A compatible application version is required to synchronize',denied:'Private activity access is unavailable',unavailable:'Synchronization stopped · refresh to retry'})[coherent.state.value.status])

function label(value: string): string { return value.replaceAll('_',' ').replace(/^./,(letter) => letter.toUpperCase()) }
function time(value: string | null): string { if (!value) return 'Unavailable'; const date = new Date(value); return Number.isFinite(date.getTime()) ? date.toLocaleString() : 'Unavailable' }
function message(cause: unknown): string { return cause instanceof Error ? cause.message : 'Could not load activity.' }
async function detail(id: string): Promise<void> {
  const current = ++detailGeneration
  detailController?.abort(); detailController = new AbortController()
  detailLoading.value = true; detailError.value = ''; removed.value = null
  try {
    const result = await applicationQuery('activity.detail.v1',{},id,detailController.signal)
    if (active && current === detailGeneration) selected.value = result
  } catch (cause) {
    if (!active || current !== detailGeneration || cause instanceof DOMException && cause.name === 'AbortError') return
    detailError.value = message(cause)
    if (cause instanceof ApiError && [401,403].includes(cause.status)) { selected.value = null; items.value = []; page.value = null; coherent.deny(message(cause)) }
    else if (cause instanceof ApiError && cause.status === 404) {
      const listed = items.value.some((item) => item.id === id)
      selected.value = null
      if (listed) { items.value = items.value.filter((item) => item.id !== id); page.value = null; if (coherentEnabled.value) coherent.refresh(); else void load() }
      if (session.user?.role === 'admin') {
        try {
          const record = await applicationQuery('objects.removal.v1',{},id,detailController.signal)
          if (active && current === detailGeneration) { removed.value = record; detailError.value = '' }
        } catch { if (active && current === detailGeneration) removed.value = null }
      }
    }
  } finally { if (active && current === detailGeneration) detailLoading.value = false }
}
async function load(more = false): Promise<void> {
  if (!more && coherentEnabled.value) { coherent.refresh(); return }
  const current = ++generation
  controller?.abort(); controller = new AbortController()
  manualLoading.value = true; error.value = ''
  const input: ApplicationQueryInputs['activity.list.v1'] = { limit: 30, cursor: more ? page.value?.next_cursor ?? null : null,
    ...(kind.value ? { kind: kind.value } : {}), ...(source.value ? { source: source.value } : {}), ...(state.value ? { state: state.value } : {}) }
  try {
    const result = await applicationQuery('activity.list.v1',input,undefined,controller.signal)
    if (!active || current !== generation) return
    items.value = more ? [...items.value,...result.items] : result.items; page.value = result
    if (selected.value) void detail(selected.value.id)
  } catch (cause) {
    if (!active || current !== generation || cause instanceof DOMException && cause.name === 'AbortError') return
    error.value = message(cause)
    if (cause instanceof ApiError && [401,403].includes(cause.status)) { items.value = []; page.value = null; selected.value = null; removed.value = null }
  } finally { if (active && current === generation) manualLoading.value = false }
}
async function inspectDelivery(): Promise<void> {
  const current = ++deliveryGeneration
  deliveryController?.abort(); deliveryController = new AbortController()
  deliveryLoading.value = true; deliveryError.value = ''
  try { const result = await applicationQuery('events.status.v1',{},undefined,deliveryController.signal); if (active && current === deliveryGeneration) delivery.value = result }
  catch (cause) { if (active && current === deliveryGeneration) { deliveryError.value = message(cause); if (cause instanceof ApiError && [401,403].includes(cause.status)) delivery.value = null } }
  finally { if (active && current === deliveryGeneration) deliveryLoading.value = false }
}
function configure(): void {
  ++generation; controller?.abort(); manualLoading.value = false
  if (coherentEnabled.value) coherent.configure({view:'legacy.activity.list.v1',input:{limit:30,cursor:null,...(kind.value ? {kind:kind.value} : {}),...(source.value ? {source:source.value} : {}),...(state.value ? {state:state.value} : {})}})
  else { coherent.configure(null); void load() }
}
async function catalogue(): Promise<void> {
  const current = ++catalogueGeneration
  try { const result = await applicationQuery('activity.sources.v1',{}); if (active && current === catalogueGeneration) sources.value = result.items }
  catch (cause) { if (active && current === catalogueGeneration) error.value = message(cause) }
}
watch(coherent.state,(next,previous) => {
  if (!coherentEnabled.value) return
  error.value = next.error
  if (next.snapshotSerial !== previous.snapshotSerial && next.data) {
    ++generation; controller?.abort(); manualLoading.value = false; items.value = next.data.items; page.value = next.data
    if (selected.value) void detail(selected.value.id)
  } else if (!next.data) { items.value = []; page.value = null; selected.value = null; removed.value = null }
  if (next.status === 'denied') {
    ++generation; controller?.abort(); manualLoading.value = false
    ++detailGeneration; detailController?.abort(); detailLoading.value = false
    ++deliveryGeneration; deliveryController?.abort(); delivery.value = null; deliveryLoading.value = false
    ++catalogueGeneration; sources.value = []
  }
})
watch([kind,source,state],() => { selected.value = null; removed.value = null; ++detailGeneration; detailController?.abort(); detailLoading.value = false; detailError.value = ''; configure() })
watch(() => session.user?.role,() => { selected.value = null; removed.value = null; items.value = []; page.value = null; delivery.value = null; sources.value = []; ++detailGeneration; detailController?.abort(); ++deliveryGeneration; deliveryController?.abort(); configure(); void catalogue() })
onMounted(() => { void catalogue(); configure(); if (coherentEnabled.value) void inspectDelivery() })
onBeforeUnmount(() => { active = false; ++generation; ++detailGeneration; ++deliveryGeneration; ++catalogueGeneration; controller?.abort(); detailController?.abort(); deliveryController?.abort() })
</script>

<template>
  <div class="activity-page"><AdminPageHeader title="Activity" description="Inspect work, its reported state and the service that owns its execution."><template #actions><BaseButton :disabled="loading" @click="load()">Refresh</BaseButton></template></AdminPageHeader>
    <p class="context">Legacy runs retain their existing executors. Their shared status categories describe reported history; cleanup and result acceptance remain separate facts.</p>
    <div class="filters"><label>Resource<select v-model="kind" class="input"><option value="">All resources</option><option value="operation">Operations</option><option value="job">Jobs</option></select></label><label>Source<select v-model="source" class="input"><option value="">All available sources</option><option v-for="item in sources" :key="item.key" :value="item.key">{{ item.label }}</option></select></label><label>Status category<select v-model="state" class="input"><option value="">All states</option><option v-for="item in states" :key="item" :value="item">{{ label(item) }}</option></select></label></div>
    <p v-if="coherentEnabled" class="context" role="status">{{ syncLabel }} · changes trigger a fresh first page</p><p v-if="error" class="error" role="alert">{{ error }}</p><p class="count" role="status">{{ countLabel }} matching records · ordered by first observation · membership reflects current access and state{{ loading ? ' · refreshing…' : '' }}</p>
    <div class="activity-workspace"><section class="activity-list" :aria-busy="loading"><p v-if="!loading && !items.length">No activity is available for these filters.</p><ul><li v-for="item in items" :key="item.id" :class="{ selected: selected?.id === item.id }"><button type="button" class="activity-select" @click="detail(item.id)"><span class="item-heading"><strong>{{ item.title }}</strong><span class="state">{{ label(item.source_state) }}</span></span><span>{{ label(item.kind) }} · {{ item.execution_owner }}</span><small>Reported creation: {{ time(item.source_created_at) }}</small></button></li></ul><BaseButton v-if="page?.next_cursor" :disabled="loading" @click="load(true)">Load more</BaseButton></section>
    <aside class="activity-detail" :aria-busy="detailLoading"><p v-if="detailError" class="error" role="alert">{{ detailError }}</p><template v-if="selected"><h2>{{ selected.title }}</h2><dl><dt>Reported state</dt><dd>{{ label(selected.source_state) }}</dd><dt>Shared status category</dt><dd>{{ label(selected.state) }}</dd><dt>Execution authority</dt><dd>{{ selected.execution_owner }}</dd><dt>Ownership</dt><dd>Observed legacy work</dd><dt>Cleanup evidence</dt><dd>Not recorded by this adapter</dd><dt>Result acceptance</dt><dd>Inspect the owning workflow’s history</dd><dt>Original reference</dt><dd>{{ selected.source }} · {{ selected.source_id }}</dd><dt v-if="selected.owner_actor">Requesting actor</dt><dd v-if="selected.owner_actor">{{ selected.owner_actor }}</dd><dt>First observed</dt><dd>{{ time(selected.observed_at) }}</dd></dl><BaseButton :to="selected.inspect_url">Open owning control panel</BaseButton><BaseButton variant="ghost" :disabled="detailLoading" @click="detail(selected.id)">Refresh detail</BaseButton></template><section v-else-if="removed"><h2>Resource removed</h2><p>The owning legacy workflow removed this record. Its identity remains reserved.</p><dl><dt>Original reference</dt><dd>{{ removed.source }} · {{ removed.source_id }}</dd><dt>Removal observation</dt><dd>{{ time(removed.observed_at) }}</dd><dt>Evidence</dt><dd>{{ removed.reason === 'source_deleted' ? 'Deletion recorded in the source transaction' : 'Source record was already absent at migration' }}</dd><dt>Cleanup evidence</dt><dd>Not recorded by the legacy adapter</dd><dt>Restoration</dt><dd>This legacy record has no supported restore action</dd></dl></section><p v-else>Select a run to inspect its owner and reported state.</p></aside></div>
    <details v-if="session.user?.role === 'admin'" class="delivery"><summary>Durable event delivery</summary><p>Delivery progress tracks committed changes. Each consumer records its own applied checkpoint and recovery state.</p><BaseButton size="small" :disabled="deliveryLoading" @click="inspectDelivery">Refresh delivery status</BaseButton><p v-if="deliveryError" class="error" role="alert">{{ deliveryError }}</p><template v-if="delivery"><dl><dt>Undelivered source events</dt><dd>{{ delivery.pending_source.count }}{{ delivery.pending_source.more ? '+' : '' }}</dd><dt>Delivery position</dt><dd>{{ delivery.last_delivery_position }}</dd><dt>Latest delivery</dt><dd>{{ time(delivery.last_delivered_at) }}</dd></dl><ul class="consumer-list"><li v-for="consumer in delivery.consumers" :key="consumer.key"><strong>{{ consumer.key }}</strong><span>{{ label(consumer.state) }} · {{ consumer.pending.count }}{{ consumer.pending.more ? '+' : '' }} unapplied events</span><small v-if="consumer.error">{{ consumer.error }} · {{ consumer.failure_count }} failed cycles{{ consumer.retry_at ? ` · retry after ${time(consumer.retry_at)}` : '' }}</small></li></ul><p v-if="delivery.consumers_truncated">Showing the first 100 consumers.</p></template></details>
  </div>
</template>

<style scoped>
.activity-page{display:grid;gap:1.2rem}.context,.count{color:var(--color-text-muted);font-size:.8rem;line-height:1.6;margin:0}.filters{display:grid;gap:1rem;grid-template-columns:repeat(3,1fr)}.filters label{display:grid;gap:.4rem;font-size:.8rem}.input{background:var(--color-surface);border:1px solid var(--color-border-strong);border-radius:var(--radius-md);color:var(--color-text);font:inherit;min-height:var(--control-height);padding:.6rem;width:100%}.activity-workspace{align-items:start;display:grid;gap:1.2rem;grid-template-columns:minmax(0,1.3fr) minmax(17rem,1fr)}.activity-list ul,.consumer-list{list-style:none;margin:0 0 1rem;padding:0}.activity-list li{border:1px solid var(--color-border);border-bottom:0}.activity-list li:first-child{border-radius:.6rem .6rem 0 0}.activity-list li:last-child{border-bottom:1px solid var(--color-border);border-radius:0 0 .6rem .6rem}.activity-select{background:transparent;color:inherit;cursor:pointer;display:grid;gap:.5rem;padding:1rem;text-align:left;width:100%}.activity-select:hover,.selected .activity-select{background:var(--color-surface-hover)}.activity-select:focus-visible{outline:2px solid var(--color-focus);outline-offset:-3px}.item-heading{align-items:start;display:flex;gap:1rem;justify-content:space-between}.activity-select>span:not(.item-heading),.activity-select small{color:var(--color-text-muted);font-size:.75rem}.state{background:var(--color-surface-sunken);border-radius:.35rem;font-size:.7rem;padding:.2rem .4rem;white-space:nowrap}.activity-detail,.delivery{border:1px solid var(--color-border);border-radius:.7rem;padding:1.2rem}.activity-detail h2{font-size:1.05rem;margin-top:0}.activity-detail .base-button{margin:.4rem .4rem 0 0}.activity-page dl{display:grid;font-size:.8rem;gap:.65rem 1rem;grid-template-columns:minmax(7rem,auto) minmax(0,1fr)}.activity-page dt{color:var(--color-text-muted)}.activity-page dd{margin:0;overflow-wrap:anywhere}.error{color:var(--color-danger);font-size:.85rem}.delivery summary{cursor:pointer;font-size:.9rem;font-weight:650}.delivery p{font-size:.8rem}.consumer-list li{border-top:1px solid var(--color-border);display:grid;font-size:.8rem;gap:.4rem;padding:.8rem 0}.consumer-list small{color:var(--color-text-muted)}@media(max-width:65rem){.activity-workspace{grid-template-columns:1fr}}@media(max-width:42rem){.filters{grid-template-columns:1fr}.activity-page dl{grid-template-columns:1fr}.activity-page dd{padding-bottom:.35rem}}
</style>
