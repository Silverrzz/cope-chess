<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import type { ChessPositionAnswer } from '@/types/forms'
import FormPositionEditor from './FormPositionEditor.vue'

const props = defineProps<{
  id: string
  modelValue?: unknown
  disabled?: boolean
  required?: boolean
  invalid?: boolean
  describedBy?: string | undefined
}>()
const emit = defineEmits<{ 'update:modelValue': [value: ChessPositionAnswer | undefined] }>()

const dialog = ref<HTMLDialogElement | null>(null)
const editorOpen = ref(false)
const initialFen = ref('')
const fen = computed(() => {
  const value = props.modelValue
  return value && typeof value === 'object' && 'fen' in value && typeof value.fen === 'string' ? value.fen : ''
})

function updateFen(value: string) {
  emit('update:modelValue', value.trim() ? { fen: value } : undefined)
}

async function openEditor() {
  if (editorOpen.value) return
  initialFen.value = fen.value
  editorOpen.value = true
  await nextTick()
  dialog.value?.showModal()
}

function applyPosition(value: ChessPositionAnswer) {
  if (!dialog.value?.open || props.disabled) return
  emit('update:modelValue', value)
  dialog.value.close()
}
</script>

<template>
  <div class="position-field">
    <input
      :id="id" class="input input--mono" :value="fen" type="text" maxlength="120"
      placeholder="Enter FEN" spellcheck="false" autocapitalize="off" autocomplete="off"
      :disabled="disabled" :required="required" :aria-invalid="invalid || undefined" :aria-describedby="describedBy"
      @input="updateFen(($event.target as HTMLInputElement).value)"
    >
    <BaseButton :disabled="disabled" aria-haspopup="dialog" @click="openEditor">Edit position</BaseButton>

    <Teleport to="body">
      <dialog ref="dialog" class="panel position-dialog" :aria-labelledby="`${id}-editor-title`" @close="editorOpen = false">
        <header class="panel__header">
          <h2 :id="`${id}-editor-title`" tabindex="-1" autofocus>Edit position</h2>
          <BaseButton size="small" variant="ghost" icon-only @click="dialog?.close()">
            <template #icon><AppIcon name="close" :size="18" /></template>
            Close position editor
          </BaseButton>
        </header>
        <div class="panel__body">
          <FormPositionEditor v-if="editorOpen" :initial-fen="initialFen" @apply="applyPosition" />
        </div>
      </dialog>
    </Teleport>
  </div>
</template>

<style scoped>
.position-field {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: var(--space-2);
}

.position-dialog {
  width: min(52rem, calc(100vw - 2rem));
  max-height: calc(100dvh - 2rem);
  margin: auto;
  padding: 0;
  color: var(--color-text);
  overscroll-behavior: contain;
}

.position-dialog::backdrop {
  background: var(--color-overlay);
}

.position-dialog .panel__header {
  align-items: center;
}

.position-dialog h2 {
  font-size: 1.125rem;
}
</style>
