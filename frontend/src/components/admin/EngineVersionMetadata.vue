<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import EngineVersionTags from '@/components/engines/EngineVersionTags.vue'
import type { EngineVersionTag } from '@/components/engines/types'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import InlineFeedback from './InlineFeedback.vue'
import { errorText } from './format'
import type { Engine } from './types'

const props = defineProps<{ version: Engine }>()
const toast = useToast()
const { confirm } = useConfirm()
const tags = ref<EngineVersionTag[]>([])
const hidden = ref(Boolean(props.version.public_hidden))
const savedHidden = ref(hidden.value)
const selected = ref((props.version.tags ?? []).map((tag) => tag.id))
const savedSelected = ref([...selected.value])
const busy = ref(false)
const ready = ref(false)
const error = ref('')
const editingId = ref<number | null>(null)
const name = ref('')
const color = ref('#4789cf')
const tagsChanged = computed(() => selected.value.length !== savedSelected.value.length
  || selected.value.some((id) => !savedSelected.value.includes(id)))
const changed = computed(() => hidden.value !== savedHidden.value || tagsChanged.value)

async function loadTags(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    tags.value = (await api.get<{ tags: EngineVersionTag[] }>('/api/admin/engine-version-tags')).tags
    ready.value = true
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}

function editTag(tag?: EngineVersionTag): void {
  editingId.value = tag?.id ?? null
  name.value = tag?.name ?? ''
  color.value = tag?.color ?? '#4789cf'
}

async function saveTag(): Promise<void> {
  busy.value = true
  error.value = ''
  const body = { name: name.value.trim(), color: color.value }
  try {
    const response = editingId.value === null
      ? await api.post<{ tag: EngineVersionTag }>('/api/admin/engine-version-tags', { body })
      : await api.put<{ tag: EngineVersionTag }>(`/api/admin/engine-version-tags/${editingId.value}`, { body })
    tags.value = [...tags.value.filter((tag) => tag.id !== response.tag.id), response.tag]
      .sort((left, right) => left.name.localeCompare(right.name))
    editTag()
    toast.success('Tag saved.')
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}

async function removeTag(): Promise<void> {
  const id = editingId.value
  if (id === null || !await confirm({
    title: 'Delete tag?',
    message: `Remove ${name.value} from the tag catalog and from every engine version?`,
    confirmLabel: 'Delete tag',
    tone: 'danger',
  })) return
  busy.value = true
  error.value = ''
  try {
    await api.delete(`/api/admin/engine-version-tags/${id}`)
    tags.value = tags.value.filter((tag) => tag.id !== id)
    selected.value = selected.value.filter((tagId) => tagId !== id)
    savedSelected.value = savedSelected.value.filter((tagId) => tagId !== id)
    editTag()
    toast.success('Tag deleted.')
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}

async function saveMetadata(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    const response = await api.put<{ message: string }>(`/api/admin/engine-versions/${props.version.id}/metadata`, {
      body: {
        ...(hidden.value !== savedHidden.value ? { public_hidden: hidden.value } : {}),
        ...(tagsChanged.value ? { tag_ids: selected.value } : {}),
      },
    })
    savedSelected.value = [...selected.value]
    savedHidden.value = hidden.value
    toast.success(response.message)
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = false
  }
}

onMounted(loadTags)
</script>

<template>
  <section class="panel metadata-panel" aria-label="Public listing">
    <header>
      <h2>Public listing</h2>
      <p>Set how this version appears in the engine directory and apply version-specific tags.</p>
    </header>
    <InlineFeedback :message="error" />
    <button v-if="!ready && error" class="button button--secondary button--small" type="button" :disabled="busy" @click="loadTags">Reload tags</button>
    <form class="metadata-form" @submit.prevent="saveMetadata">
      <label class="metadata-check"><input v-model="hidden" type="checkbox" :disabled="busy"> Hide from public engine pages</label>
      <fieldset v-if="ready" :disabled="busy">
        <legend>Assigned tags</legend>
        <div class="metadata-options">
          <label v-for="tag in tags" :key="tag.id" class="metadata-check">
            <input v-model="selected" type="checkbox" :value="tag.id">
            <EngineVersionTags :tags="[tag]" />
          </label>
          <p v-if="!tags.length">Create a tag below to assign it.</p>
        </div>
      </fieldset>
      <button class="button button--primary button--small" type="submit" :disabled="busy || !changed">Save public listing</button>
    </form>
    <details v-if="ready" class="metadata-catalog">
      <summary>Manage tag catalog</summary>
      <form class="metadata-form" @submit.prevent="saveTag">
        <label>
          <span>Tag to manage</span>
          <select class="input" :value="editingId ?? ''" :disabled="busy" @change="editTag(tags.find((tag) => tag.id === Number(($event.target as HTMLSelectElement).value)))">
            <option value="">New tag</option>
            <option v-for="tag in tags" :key="tag.id" :value="tag.id">{{ tag.name }}</option>
          </select>
        </label>
        <div class="metadata-options">
          <label><span>Name</span><input v-model="name" class="input" maxlength="40" required :disabled="busy"></label>
          <label><span>Colour</span><input v-model="color" class="input metadata-color" type="color" :disabled="busy"></label>
        </div>
        <div class="metadata-options">
          <button class="button button--secondary button--small" type="submit" :disabled="busy || !name.trim()">{{ editingId === null ? 'Create tag' : 'Save tag' }}</button>
          <button v-if="editingId !== null" class="button button--danger button--small" type="button" :disabled="busy" @click="removeTag">Delete tag</button>
        </div>
      </form>
    </details>
  </section>
</template>

<style scoped>
.metadata-panel { display: grid; gap: .8rem; padding: 1rem; }
h2 { font-size: .95rem; margin: 0; }
p { color: var(--color-text-muted); font-size: .76rem; margin: .25rem 0 0; }
.metadata-form { display: grid; gap: .65rem; justify-items: start; }
.metadata-form label:not(.metadata-check) { display: grid; font-size: .75rem; gap: .25rem; }
.metadata-form fieldset { border: 0; margin: 0; min-width: 0; padding: 0; }
.metadata-form legend { font-size: .75rem; font-weight: 650; margin-bottom: .35rem; padding: 0; }
.metadata-check, .metadata-options { align-items: center; display: flex; flex-wrap: wrap; gap: .5rem; }
.metadata-check { font-size: .8rem; }
.metadata-options { gap: .65rem; }
.metadata-color { min-height: 2.25rem; width: 4rem; }
.metadata-catalog { border-top: 1px solid var(--color-border); padding-top: .75rem; }
.metadata-catalog summary { cursor: pointer; font-size: .8rem; font-weight: 650; }
.metadata-catalog form { margin-top: .75rem; }
</style>
