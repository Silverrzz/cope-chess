<script setup lang="ts">
import { computed, onMounted, ref } from "vue"

import { api } from "@/api/client"
import AdminPageHeader from "@/components/admin/AdminPageHeader.vue"
import EngineVersionPicker from "@/components/admin/EngineVersionPicker.vue"
import InlineFeedback from "@/components/admin/InlineFeedback.vue"
import AppIcon from "@/components/ui/AppIcon.vue"
import { errorText } from "@/components/admin/format"
import { useToast } from "@/composables/useToast"

interface AcknowledgedItem {
  id: number
  name: string
  version: string
}

interface WaitingItem {
  id: number
  engine_version_id: number
  rating_list_id: number
  engine_name: string
  engine_version: string
  rating_list_name: string
}

interface EngineVersionChoice {
  id: number
  engine_id: number
  name: string
  version: string
  author: string
  active: boolean
}

interface RatingListChoice {
  id: number
  name: string
}

interface QueueResponse {
  acknowledged: AcknowledgedItem[]
  waiting_for_test: WaitingItem[]
  engine_versions: EngineVersionChoice[]
  rating_lists: RatingListChoice[]
  message?: string
}

type QueueName = "acknowledged" | "waiting"
type QueueItem = AcknowledgedItem | WaitingItem

const toast = useToast()
const acknowledged = ref<AcknowledgedItem[]>([])
const waiting = ref<WaitingItem[]>([])
const engineVersions = ref<EngineVersionChoice[]>([])
const ratingLists = ref<RatingListChoice[]>([])
const newName = ref("")
const newVersion = ref("")
const selectedEngineVersion = ref<string | number>("")
const selectedRatingList = ref("")
const loading = ref(true)
const saving = ref(false)
const error = ref("")
const dragging = ref<{ queue: QueueName; index: number } | null>(null)
let temporaryId = 0

const selectedVersion = computed(() => engineVersions.value.find((item) => item.id === Number(selectedEngineVersion.value)))
const selectedList = computed(() => ratingLists.value.find((item) => item.id === Number(selectedRatingList.value)))

onMounted(load)

async function load(): Promise<void> {
  loading.value = true
  error.value = ""
  try {
    const response = await api.get<QueueResponse>("/api/admin/engines/queue")
    acknowledged.value = response.acknowledged
    waiting.value = response.waiting_for_test
    engineVersions.value = response.engine_versions
    ratingLists.value = response.rating_lists
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    loading.value = false
  }
}

function addAcknowledged(): void {
  const name = newName.value.trim()
  const version = newVersion.value.trim()
  if (!name || !version) {
    error.value = "Enter both an engine name and version."
    return
  }
  acknowledged.value.push({ id: --temporaryId, name, version })
  newName.value = ""
  newVersion.value = ""
  error.value = ""
}

function addWaiting(): void {
  const version = selectedVersion.value
  const ratingList = selectedList.value
  if (!version || !ratingList) {
    error.value = "Choose an engine version and rating list."
    return
  }
  if (waiting.value.some((item) => item.engine_version_id === version.id && item.rating_list_id === ratingList.id)) {
    error.value = "That test plan is already in the queue."
    return
  }
  waiting.value.push({
    id: --temporaryId,
    engine_version_id: version.id,
    rating_list_id: ratingList.id,
    engine_name: version.name,
    engine_version: version.version,
    rating_list_name: ratingList.name,
  })
  selectedEngineVersion.value = ""
  selectedRatingList.value = ""
  error.value = ""
}

function remove(queue: QueueName, index: number): void {
  listFor(queue).splice(index, 1)
}

function move(queue: QueueName, from: number, to: number): void {
  const list = listFor(queue)
  if (to < 0 || to >= list.length || from === to) return
  const [item] = list.splice(from, 1)
  if (item) list.splice(to, 0, item)
}

function listFor(queue: QueueName): QueueItem[] {
  return queue === "acknowledged" ? acknowledged.value : waiting.value
}

function startDrag(queue: QueueName, index: number, event: DragEvent): void {
  dragging.value = { queue, index }
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = "move"
    event.dataTransfer.setData("text/plain", `${queue}:${index}`)
  }
}

function drop(queue: QueueName, index: number): void {
  if (!dragging.value || dragging.value.queue !== queue) return
  move(queue, dragging.value.index, index)
  dragging.value = null
}

async function save(): Promise<void> {
  if (acknowledged.value.some((item) => !item.name.trim() || !item.version.trim())) {
    error.value = "Every acknowledged engine needs a name and version."
    return
  }
  saving.value = true
  error.value = ""
  try {
    const response = await api.put<QueueResponse>("/api/admin/engines/queue", {
      body: {
        acknowledged: acknowledged.value.map((item) => ({ name: item.name.trim(), version: item.version.trim() })),
        waiting_for_test: waiting.value.map((item) => ({
          engine_version_id: item.engine_version_id,
          rating_list_id: item.rating_list_id,
        })),
      },
    })
    acknowledged.value = response.acknowledged
    waiting.value = response.waiting_for_test
    toast.success(response.message ?? "Engine queues updated.")
  } catch (cause) {
    error.value = errorText(cause)
    toast.error(cause)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="admin-page page-stack">
    <AdminPageHeader title="Engine queue" description="Manage the public acknowledged and waiting-for-test queues.">
      <template #actions>
        <RouterLink class="button button--ghost" to="/admin/engines">Back to engines</RouterLink>
        <button class="button button--primary" type="button" :disabled="loading || saving" @click="save">
          {{ saving ? "Saving…" : "Save queues" }}
        </button>
      </template>
    </AdminPageHeader>

    <InlineFeedback :message="error" />

    <div v-if="loading" class="panel queue-loading">Loading engine queues…</div>
    <div v-else class="queue-grid">
      <section class="panel queue-panel">
        <header class="queue-header">
          <div><span>Acknowledged</span><h2>Recognised engines</h2></div>
          <strong>{{ acknowledged.length }}</strong>
        </header>
        <p class="queue-help">Engines COPE is aware of and plans to add. Drag entries to set their public order.</p>

        <form class="acknowledged-form" @submit.prevent="addAcknowledged">
          <label><span>Engine name</span><input v-model="newName" class="input" maxlength="120" placeholder="Engine name"></label>
          <label><span>Version</span><input v-model="newVersion" class="input" maxlength="120" placeholder="Version"></label>
          <button class="button button--secondary" type="submit"><AppIcon name="plus" :size="15" />Add</button>
        </form>

        <ol v-if="acknowledged.length" class="queue-list">
          <li
            v-for="(item, index) in acknowledged"
            :key="item.id"
            draggable="true"
            :class="{ 'queue-item--dragging': dragging?.queue === 'acknowledged' && dragging.index === index }"
            @dragstart="startDrag('acknowledged', index, $event)"
            @dragend="dragging = null"
            @dragover.prevent
            @drop.prevent="drop('acknowledged', index)"
          >
            <span class="drag-handle" title="Drag to reorder"><AppIcon name="more-vertical" :size="18" /><b>{{ index + 1 }}</b></span>
            <label><span class="sr-only">Engine name</span><input v-model="item.name" class="input" maxlength="120"></label>
            <label><span class="sr-only">Version</span><input v-model="item.version" class="input" maxlength="120"></label>
            <div class="item-actions">
              <button type="button" :disabled="index === 0" aria-label="Move up" @click="move('acknowledged', index, index - 1)"><AppIcon name="chevron-up" :size="15" /></button>
              <button type="button" :disabled="index === acknowledged.length - 1" aria-label="Move down" @click="move('acknowledged', index, index + 1)"><AppIcon name="chevron-down" :size="15" /></button>
              <button type="button" aria-label="Remove entry" @click="remove('acknowledged', index)"><AppIcon name="trash" :size="15" /></button>
            </div>
          </li>
        </ol>
        <p v-else class="queue-empty">No engines have been acknowledged yet.</p>
      </section>

      <section class="panel queue-panel">
        <header class="queue-header">
          <div><span>Waiting for test</span><h2>Planned test runs</h2></div>
          <strong>{{ waiting.length }}</strong>
        </header>
        <p class="queue-help">Specific engine versions waiting to be tested in a rating list. Drag entries to set their public order.</p>

        <form class="waiting-form" @submit.prevent="addWaiting">
          <EngineVersionPicker v-model="selectedEngineVersion" :engines="engineVersions" />
          <label><span>Rating list</span><select v-model="selectedRatingList" class="input"><option value="" disabled>Choose a rating list</option><option v-for="item in ratingLists" :key="item.id" :value="String(item.id)">{{ item.name }}</option></select></label>
          <button class="button button--secondary" type="submit"><AppIcon name="plus" :size="15" />Add test plan</button>
        </form>

        <ol v-if="waiting.length" class="queue-list waiting-list">
          <li
            v-for="(item, index) in waiting"
            :key="item.id"
            draggable="true"
            :class="{ 'queue-item--dragging': dragging?.queue === 'waiting' && dragging.index === index }"
            @dragstart="startDrag('waiting', index, $event)"
            @dragend="dragging = null"
            @dragover.prevent
            @drop.prevent="drop('waiting', index)"
          >
            <span class="drag-handle" title="Drag to reorder"><AppIcon name="more-vertical" :size="18" /><b>{{ index + 1 }}</b></span>
            <div class="plan-name"><strong>{{ item.engine_name }} {{ item.engine_version }}</strong><small>{{ item.rating_list_name }}</small></div>
            <div class="item-actions">
              <button type="button" :disabled="index === 0" aria-label="Move up" @click="move('waiting', index, index - 1)"><AppIcon name="chevron-up" :size="15" /></button>
              <button type="button" :disabled="index === waiting.length - 1" aria-label="Move down" @click="move('waiting', index, index + 1)"><AppIcon name="chevron-down" :size="15" /></button>
              <button type="button" aria-label="Remove test plan" @click="remove('waiting', index)"><AppIcon name="trash" :size="15" /></button>
            </div>
          </li>
        </ol>
        <p v-else class="queue-empty">No test plans are waiting.</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.queue-grid{display:grid;gap:1rem;grid-template-columns:repeat(2,minmax(0,1fr));align-items:start}.queue-panel{overflow:hidden;padding:0}.queue-header{align-items:center;border-bottom:1px solid var(--color-border);display:flex;justify-content:space-between;padding:1rem}.queue-header span{color:var(--color-accent);font-size:.62rem;font-weight:780;letter-spacing:.08em;text-transform:uppercase}.queue-header h2{font-size:1rem;margin:.15rem 0 0}.queue-header>strong{align-items:center;background:var(--color-accent-soft);border-radius:999px;color:var(--color-accent);display:flex;font-size:.72rem;height:1.8rem;justify-content:center;min-width:1.8rem}.queue-help{color:var(--color-text-muted);font-size:.74rem;line-height:1.55;margin:0;padding:.85rem 1rem 0}.acknowledged-form,.waiting-form{align-items:end;border-bottom:1px solid var(--color-border);display:grid;gap:.65rem;padding:1rem}.acknowledged-form{grid-template-columns:minmax(0,1fr) minmax(0,.7fr) auto}.waiting-form{grid-template-columns:minmax(0,1fr) minmax(0,.7fr);}.waiting-form>.button{grid-column:1/-1;justify-self:end}.acknowledged-form label,.waiting-form label{display:grid;gap:.3rem}.acknowledged-form label>span,.waiting-form label>span{color:var(--color-text-muted);font-size:.67rem;font-weight:680}.queue-list{display:grid;list-style:none;margin:0;padding:.7rem}.queue-list li{align-items:center;background:var(--color-surface);border:1px solid var(--color-border);border-radius:var(--radius-md);display:grid;gap:.5rem;grid-template-columns:auto minmax(0,1fr) minmax(5rem,.65fr) auto;margin-top:.5rem;padding:.55rem;transition:border-color var(--transition-fast),opacity var(--transition-fast),transform var(--transition-fast)}.queue-list li:first-child{margin-top:0}.waiting-list li{grid-template-columns:auto minmax(0,1fr) auto}.queue-list li:hover{border-color:var(--color-border-strong)}.queue-item--dragging{opacity:.45;transform:scale(.985)}.drag-handle{align-items:center;color:var(--color-text-muted);cursor:grab;display:flex}.drag-handle b{font-size:.65rem;font-variant-numeric:tabular-nums;min-width:1.2rem;text-align:center}.plan-name{display:grid;gap:.16rem;min-width:0}.plan-name strong{font-size:.78rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.plan-name small{color:var(--color-text-muted);font-size:.67rem}.item-actions{display:flex;gap:.15rem}.item-actions button{align-items:center;background:transparent;border:0;border-radius:var(--radius-sm);color:var(--color-text-muted);cursor:pointer;display:flex;height:1.8rem;justify-content:center;width:1.8rem}.item-actions button:hover:not(:disabled){background:var(--color-surface-hover);color:var(--color-text)}.item-actions button:last-child:hover{color:var(--color-danger)}.item-actions button:disabled{cursor:not-allowed;opacity:.3}.queue-empty,.queue-loading{color:var(--color-text-muted);font-size:.78rem;margin:0;padding:2.5rem 1rem;text-align:center}.queue-loading{padding:4rem}
@media(max-width:70rem){.queue-grid{grid-template-columns:1fr}}
@media(max-width:36rem){.acknowledged-form,.waiting-form{grid-template-columns:1fr}.acknowledged-form>.button,.waiting-form>.button{grid-column:auto;justify-self:stretch}.queue-list li{grid-template-columns:auto minmax(0,1fr) auto}.queue-list li>label:nth-of-type(2){grid-column:2}.queue-list li>.item-actions{grid-column:3;grid-row:1/3;flex-direction:column}}
</style>
