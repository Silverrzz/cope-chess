<script setup lang="ts">
import { nextTick, ref } from 'vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import FormResourcePicker from './FormResourcePicker.vue'
import type { FormChoice } from '@/types/forms'

const props = defineProps<{ modelValue?: string | undefined; label: string; maxLength: number; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const area = ref<HTMLTextAreaElement | null>(null)
const panel = ref<'mention' | 'game' | 'ratings' | 'board' | ''>('')
const fen = ref('')
const fence = String.fromCharCode(96).repeat(3)
let caret: [number, number] | null = null

function remember(): void {
  if (area.value) caret = [area.value.selectionStart, area.value.selectionEnd]
}

async function replace(makeText: (selected: string) => string): Promise<void> {
  const value = props.modelValue ?? ''
  const [start, end] = caret ?? [value.length, value.length]
  const addition = makeText(value.slice(start, end))
  const next = value.slice(0, start) + addition + value.slice(end)
  if (next.length > props.maxLength) return
  emit('update:modelValue', next)
  panel.value = ''
  caret = [start + addition.length, start + addition.length]
  await nextTick()
  area.value?.focus()
  area.value?.setSelectionRange(...caret)
}

function open(next: typeof panel.value): void {
  remember()
  panel.value = panel.value === next ? '' : next
}

function wrap(before: string, after: string, fallback: string): void {
  remember()
  void replace(selected => before + (selected || fallback) + after)
}

function embed(kind: string, value: string): void {
  void replace(() => `\n\n${fence}cope-${kind}\n${value}\n${fence}\n\n`)
}

function select(choice: FormChoice): void {
  if (panel.value === 'mention') {
    const [tournament, game] = choice.id.split('/')
    const label = choice.label.replace(/[\\[\]]/g, '\\$&')
    void replace(() => `[${label}](/tournaments/${tournament}?game_id=${game})`)
  } else embed(panel.value, choice.id)
}
</script>

<template>
  <div class="markdown-editor">
    <label>{{ label }}
      <textarea ref="area" class="input" rows="5" :value="modelValue ?? ''" :maxlength="maxLength" :disabled="disabled"
        @blur="remember" @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)" />
    </label>
    <div class="markdown-actions" aria-label="Markdown formatting and Cope content">
      <BaseButton type="button" variant="ghost" size="small" :disabled="disabled" @click="wrap('**', '**', 'bold text')"><strong>Bold</strong></BaseButton>
      <BaseButton type="button" variant="ghost" size="small" :disabled="disabled" @click="wrap('*', '*', 'italic text')"><em>Italic</em></BaseButton>
      <BaseButton type="button" variant="ghost" size="small" :disabled="disabled" @click="wrap('[', '](https://)', 'link text')">Link</BaseButton>
      <BaseButton type="button" variant="ghost" size="small" :disabled="disabled" @click="open('mention')">Mention game</BaseButton>
      <BaseButton type="button" variant="ghost" size="small" :disabled="disabled" @click="open('game')">Game replay</BaseButton>
      <BaseButton type="button" variant="ghost" size="small" :disabled="disabled" @click="open('board')">Chess position</BaseButton>
      <BaseButton type="button" variant="ghost" size="small" :disabled="disabled" @click="open('ratings')">Rating list</BaseButton>
    </div>
    <FormResourcePicker v-if="panel && panel !== 'board'" :key="panel" :kind="panel === 'ratings' ? 'rating_list' : 'game'" :disabled="disabled" @selected="select" />
    <div v-else-if="panel === 'board'" class="position-editor">
      <label>Position FEN<input v-model="fen" class="input" placeholder="startpos or a FEN" maxlength="140" :disabled="disabled"></label>
      <BaseButton type="button" variant="secondary" size="small" :disabled="disabled || !fen.trim()" @click="embed('board', fen.trim())">Insert board</BaseButton>
    </div>
  </div>
</template>

<style scoped>
.markdown-editor, .markdown-editor label { display: grid; gap: .45rem; }
.markdown-editor textarea { line-height: 1.55; }
.markdown-actions { display: flex; flex-wrap: wrap; gap: .2rem; }
.position-editor { display: flex; gap: .6rem; align-items: end; }
.position-editor label { flex: 1; }
</style>
